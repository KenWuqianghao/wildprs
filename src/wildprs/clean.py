"""Cleaning rules. Each rule is one named entry in RULES and a pure function over the fixes
of one animal and sensor. The rules run in table order; the last step splits the track at
legs that are still faster than the speed ceiling."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import polars as pl

from .model import Animal, Group, Taxon, Track, haversine_km

SENSOR_CLASS = {"gps": "gps", "gnss": "gps", "argos-doppler-shift": "argos"}
# Ground-speed ceilings in m/s. Generous, so that tailwind flights stay in.
CEILING_MS = {Group.BIRD: 45.0, Group.BAT: 30.0, Group.LAND_MAMMAL: 20.0, Group.MARINE: 10.0,
              Group.TURTLE: 5.0, Group.OTHER: 15.0}
NON_WILD = {"Homo sapiens", "Felis catus", "Canis familiaris", "Canis lupus familiaris", "Bos taurus",
            "Capra hircus", "Ovis aries", "Equus caballus", "Columba livia", "Sus scrofa domesticus"}
NOT_FREE = {"relocated", "domesticated", "confined", "captive", "translocated"}
ANIMAL_CLASSES = {"Aves", "Mammalia", "Testudines", "Elasmobranchii", "Actinopterygii", "Squamata",
                  "Crocodylia", "Reptilia", "Amphibia", "Insecta"}
MIN_INTERVAL_S = 120
EXCURSION_MAX_FIXES = 5


@dataclass(frozen=True)
class Deployment:
    nick: str = ""
    manipulation: frozenset[str] = frozenset()
    on: float | None = None
    off: float | None = None
    taxon: str = ""


@dataclass
class Fixes:
    """Raw fixes of one animal and one sensor class, sorted by time (numpy columns)."""
    t: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    h: np.ndarray
    vis: np.ndarray
    flags: np.ndarray
    hdop: np.ndarray
    sats: np.ndarray
    lc: np.ndarray

    def take(self, keep: np.ndarray) -> "Fixes":
        return Fixes(*(getattr(self, f)[keep] for f in self.__dataclass_fields__))

    def __len__(self) -> int:
        return len(self.t)


@dataclass(frozen=True)
class Context:
    sensor: str
    taxon: Taxon
    deployment: Deployment

    @property
    def ceiling_ms(self) -> float:
        return CEILING_MS[self.taxon.group]


@dataclass(frozen=True)
class Rule:
    name: str
    doc: str
    keep: Callable[[Fixes, Context], np.ndarray]


def _all(f: Fixes) -> np.ndarray:
    return np.ones(len(f), bool)


def r_wild(f: Fixes, c: Context) -> np.ndarray:
    ok = (c.taxon.name not in NON_WILD and c.taxon.cls in ANIMAL_CLASSES
          and not (c.deployment.manipulation & NOT_FREE))
    return _all(f) if ok else ~_all(f)


def r_sensor(f: Fixes, c: Context) -> np.ndarray:
    return _all(f) if c.sensor in ("gps", "argos") else ~_all(f)


def r_flags(f: Fixes, c: Context) -> np.ndarray:
    return f.vis & (f.flags == 0)


def r_quality(f: Fixes, c: Context) -> np.ndarray:
    if c.sensor == "argos":
        has = f.lc != ""
        return ~has | np.isin(f.lc, ["3", "2", "1"]) if has.any() else _all(f)
    return (np.isnan(f.hdop) | (f.hdop <= 10)) & (np.isnan(f.sats) | (f.sats >= 4))


def r_deployment(f: Fixes, c: Context) -> np.ndarray:
    d = c.deployment
    keep = _all(f)
    if d.on is not None:
        keep &= f.t >= d.on
    if d.off is not None:
        keep &= f.t <= d.off
    return keep


def r_min_interval(f: Fixes, c: Context) -> np.ndarray:
    keep = np.ones(len(f), bool)
    last = -np.inf
    for i, x in enumerate(f.t.tolist()):
        if x - last < MIN_INTERVAL_S:
            keep[i] = False
        else:
            last = x
    return keep


def _leg_speed(t, lat, lon):
    return haversine_km(lat[:-1], lon[:-1], lat[1:], lon[1:]) * 1000 / np.maximum(np.diff(t), 1)


def r_excursions(f: Fixes, c: Context) -> np.ndarray:
    """Out-and-back excursions of 1..5 fixes: a fast leg out, a fast leg back, and the fixes
    before and after the excursion close together compared with the distance out."""
    ceil = c.ceiling_ms
    keep = np.ones(len(f), bool)
    for _ in range(3):
        idx = np.flatnonzero(keep)
        n = len(idx)
        if n < 3:
            break
        la, lo, tt = f.lat[idx], f.lon[idx], f.t[idx]
        v = _leg_speed(tt, la, lo)
        drop = np.zeros(n, bool)
        for k in range(1, EXCURSION_MAX_FIXES + 1):
            if n < k + 2:
                break
            a = np.arange(0, n - k - 1)
            b = a + k + 1
            dbase = haversine_km(la[a], lo[a], la[b], lo[b])
            dout = haversine_km(la[a], lo[a], la[a + 1], lo[a + 1])
            hit = (v[a] > ceil / 2) & (v[b - 1] > ceil / 2) & (dbase < 0.2 * dout)
            for x in np.flatnonzero(hit):
                drop[x + 1: x + k + 1] = True
        if not drop.any():
            break
        keep[idx[drop]] = False
    return keep


def r_speed_spikes(f: Fixes, c: Context) -> np.ndarray:
    """Repeatedly drop the fix whose slower adjacent leg is above the speed ceiling."""
    ceil = c.ceiling_ms
    keep = np.ones(len(f), bool)
    for _ in range(50):
        idx = np.flatnonzero(keep)
        if len(idx) < 3:
            break
        v = _leg_speed(f.t[idx], f.lat[idx], f.lon[idx])
        m = np.minimum(np.r_[v[0], v], np.r_[v, v[-1]])
        bad = m > ceil
        if not bad.any():
            break
        order = np.argsort(-np.where(bad, m, -1))[: max(1, bad.sum() // 4)]
        drop: list[int] = []
        for o in order:
            if not bad[o]:
                break
            if all(abs(o - x) > 1 for x in drop):
                drop.append(int(o))
        keep[idx[drop]] = False
    return keep


RULES: tuple[Rule, ...] = (
    Rule("wild_free_animal", "Drop humans, pets, livestock, pigeons and captive, relocated or confined animals.", r_wild),
    Rule("gps_or_argos", "Keep only GPS and Argos fixes.", r_sensor),
    Rule("movebank_flags", "Drop fixes that Movebank or the data owner marked as outliers.", r_flags),
    Rule("fix_quality", "Argos: keep location classes 3, 2, 1. GPS: HDOP 10 or less and 4 or more satellites, where given.", r_quality),
    Rule("deployment_window", "Keep fixes between the deployment start and end dates.", r_deployment),
    Rule("min_interval", f"Keep at most one fix per {MIN_INTERVAL_S} s.", r_min_interval),
    Rule("excursions", f"Drop out-and-back excursions of up to {EXCURSION_MAX_FIXES} fixes.", r_excursions),
    Rule("speed_spikes", "Drop single fixes that need a speed above the ceiling of the animal group.", r_speed_spikes),
)
SPLIT_DOC = "Split the track at a leg that is still faster than the ceiling; no record uses that leg."


def clean_fixes(f: Fixes, ctx: Context, counts: dict[str, int] | None = None) -> tuple[Fixes, np.ndarray]:
    """Apply RULES in order. Returns the kept fixes and brk (per leg: True = split)."""
    for rule in RULES:
        if len(f) == 0:
            break
        keep = rule.keep(f, ctx)
        if counts is not None:
            counts[rule.name] = counts.get(rule.name, 0) + int((~keep).sum())
        f = f.take(keep)
    brk = _leg_speed(f.t, f.lat, f.lon) > ctx.ceiling_ms if len(f) > 1 else np.zeros(0, bool)
    if counts is not None:
        counts["split_legs"] = counts.get("split_legs", 0) + int(brk.sum())
    return f, brk


# ---------- boundary: package files -> Fixes ----------

def _epoch(s: str) -> float | None:
    try:
        return datetime.fromisoformat(s[:19].replace("T", " ")).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def load_deployments(ref_csv: Path) -> dict[str, Deployment]:
    if not ref_csv.exists():
        return {}
    acc: dict[str, dict] = {}
    with ref_csv.open(newline="", encoding="utf8", errors="replace") as fh:
        for r in csv.DictReader(fh):
            a = acc.setdefault(r.get("animal-id") or "", {"nick": "", "manip": set(), "on": [], "off": [], "taxon": ""})
            a["nick"] = a["nick"] or (r.get("animal-nickname") or "").strip()
            a["manip"].add((r.get("manipulation-type") or "none").strip().lower())
            if (on := _epoch(r.get("deploy-on-date") or "")) is not None:
                a["on"].append(on)
            if (off := _epoch(r.get("deploy-off-date") or "")) is not None:
                a["off"].append(off)
            a["taxon"] = a["taxon"] or (r.get("animal-taxon") or "")
    return {k: Deployment(v["nick"], frozenset(v["manip"]), min(v["on"]) if v["on"] else None,
                          max(v["off"]) if v["off"] else None, v["taxon"]) for k, v in acc.items()}


def load_fixes(path: Path) -> pl.DataFrame:
    df = pl.read_parquet(path)
    return df.select(
        pl.col("ind").cast(pl.String), pl.col("taxon").cast(pl.String).fill_null(""),
        pl.col("sensor").cast(pl.String).replace_strict(SENSOR_CLASS, default="other").alias("sensor"),
        (pl.col("t").cast(pl.Int64) / 1000).alias("t"), "lat", "lon",
        pl.coalesce([pl.col("hmsl"), pl.col("hae")]).cast(pl.Float64).fill_null(np.nan).alias("h"),
        pl.col("vis").fill_null(True), pl.col("flags").fill_null(0),
        pl.col("hdop").cast(pl.Float64).fill_null(np.nan), pl.col("sats").cast(pl.Float64).fill_null(np.nan),
        pl.col("lc").cast(pl.String).fill_null(""),
    ).sort(["ind", "sensor", "t"])


def frame_to_fixes(g: pl.DataFrame) -> Fixes:
    return Fixes(*(g[c].to_numpy() for c in ("t", "lat", "lon", "h", "vis", "flags", "hdop", "sats", "lc")))


def clean_package(df: pl.DataFrame, deployments: dict[str, Deployment], taxon_of: Callable[[str], Taxon],
                  pkg: int) -> tuple[list[Track], dict[str, int]]:
    counts: dict[str, int] = {"input": df.height}
    tracks = []
    for (ind, sensor), g in df.group_by(["ind", "sensor"], maintain_order=True):
        dep = deployments.get(ind, Deployment())
        name = g["taxon"][0] or dep.taxon
        tx = taxon_of(name)
        f, brk = clean_fixes(frame_to_fixes(g), Context(sensor, tx, dep), counts)
        if len(f) < 2:
            continue
        tracks.append(Track(Animal(pkg, ind, tx, dep.nick), sensor, f.t, f.lat, f.lon, f.h, brk, n_input=g.height))
    counts["kept"] = sum(len(t) for t in tracks)
    return tracks, counts


def tracks_to_frame(tracks: list[Track]) -> pl.DataFrame:
    if not tracks:
        return pl.DataFrame()
    return pl.concat([pl.DataFrame({
        "ind": [tr.animal.ind] * len(tr), "sensor": [tr.sensor] * len(tr), "t": tr.t, "lat": tr.lat, "lon": tr.lon,
        "h": tr.h.astype(np.float32), "brk": np.r_[tr.brk, False]}) for tr in tracks])


def frame_to_tracks(df: pl.DataFrame, animals: dict[str, Animal]) -> list[Track]:
    out = []
    for (ind, sensor), g in df.group_by(["ind", "sensor"], maintain_order=True):
        out.append(Track(animals[ind], sensor, g["t"].to_numpy(), g["lat"].to_numpy(), g["lon"].to_numpy(),
                         g["h"].to_numpy().astype(float), g["brk"].to_numpy()[:-1]))
    return out


def fingerprints(tracks: list[Track]) -> pl.DataFrame:
    """A sample of fix keys per animal. The sample is chosen by the key itself, so two copies
    of the same data give the same sample. Used to find one animal published twice."""
    rows = []
    for tr in tracks:
        # Hour and 10 m: two copies may round time differently, but keep the same coordinates.
        key = (np.floor(tr.t / 3600).astype(np.int64) * 1_000_003
               + np.round(tr.lat * 1e4).astype(np.int64) * 7_919 + np.round(tr.lon * 1e4).astype(np.int64))
        key = np.unique(key[(key % 16) == 0])
        rows.append(pl.DataFrame({"ind": [tr.animal.ind] * len(key), "key": key},
                                 schema={"ind": pl.String, "key": pl.Int64}))
    return pl.concat(rows) if rows else pl.DataFrame(schema={"ind": pl.String, "key": pl.Int64})

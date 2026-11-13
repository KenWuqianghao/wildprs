"""Stages clean and records. Each package is one unit of work with its own output files, so a
stage can stop at any point and the next run does only the packages that are missing."""
from __future__ import annotations

import json

import numpy as np
import polars as pl

from . import config, taxa
from .categories import BY_KEY, efforts
from .clean import RULES, clean_package, fingerprints, frame_to_tracks, load_deployments, load_fixes, tracks_to_frame
from .geo import country
from .model import Animal, Track
from .repo import packages


def package_taxon(pkg: int):
    fallback = (packages()[pkg].taxa or ("",))[0]
    return lambda name: taxa.taxon(name, fallback)


def clean_pkg(pkg: int) -> dict:
    tracks, counts = clean_fixes_of(pkg)
    out = config.CLEAN / f"{pkg:03d}.parquet"
    df = tracks_to_frame(tracks)
    if df.height:
        tmp = out.with_suffix(".part")
        df.write_parquet(tmp, compression="zstd")
        tmp.replace(out)
        fingerprints(tracks).write_parquet(config.CLEAN / f"{pkg:03d}.fp.parquet")
    animals = {tr.animal.ind: {"taxon": tr.animal.taxon.name, "nick": tr.animal.nick} for tr in tracks}
    summary = {"pkg": pkg, "rules": {r.name: counts.get(r.name, 0) for r in RULES} | {
        "split_legs": counts.get("split_legs", 0)}, "input": counts["input"], "kept": counts["kept"],
        "animals": animals, "tracks": len(tracks)}
    config.atomic_write(config.CLEAN / f"{pkg:03d}.json", json.dumps(summary))
    return summary


def clean_fixes_of(pkg: int, fixes_path=None, ref_path=None) -> tuple[list[Track], dict]:
    df = load_fixes(fixes_path or config.FIXES / f"{pkg:03d}.parquet")
    deps = load_deployments(ref_path or config.REF / f"{pkg:03d}.csv")
    return clean_package(df, deps, package_taxon(pkg), pkg)


def load_tracks(pkg: int) -> list[Track]:
    p = config.CLEAN / f"{pkg:03d}.parquet"
    if not p.exists():
        return []
    summary = json.loads((config.CLEAN / f"{pkg:03d}.json").read_text())
    tx = package_taxon(pkg)
    animals = {ind: Animal(pkg, ind, tx(a["taxon"]), a["nick"]) for ind, a in summary["animals"].items()}
    return frame_to_tracks(pl.read_parquet(p), animals)


def effort_rows(tr: Track) -> list[dict]:
    rows = []
    for e in efforts(tr):
        s = slice(e.i0, e.i1 + 1)
        legs = tr.legs_km[e.i0:e.i1]
        dt = tr.dt[e.i0:e.i1]
        ok = ~tr.brk[e.i0:e.i1]
        rows.append({
            "category": e.category, "pkg": tr.animal.pkg, "ind": tr.animal.ind, "sensor": tr.sensor,
            "value": e.value, "t0": float(tr.t[e.i0]), "t1": float(tr.t[e.i1]), "support": e.support,
            "n_fixes": e.i1 - e.i0 + 1,
            "max_leg_kmh": float((legs[ok] / np.maximum(dt[ok], 1) * 3600).max()) if ok.any() else 0.0,
            "max_gap_h": float(dt.max() / 3600) if len(dt) else 0.0,
            "median_interval_min": float(np.median(tr.dt[s][:-1]) / 60) if e.i1 > e.i0 else 0.0,
            "lat0": float(tr.lat[e.i0]), "lon0": float(tr.lon[e.i0]),
            "lat1": float(tr.lat[e.i1]), "lon1": float(tr.lon[e.i1]),
            "track_fixes": len(tr), "track_t0": float(tr.t[0]), "track_t1": float(tr.t[-1]),
            "evidence": json.dumps(e.evidence),
        })
    return rows


def records_pkg(pkg: int) -> int:
    rows = [r for tr in load_tracks(pkg) for r in effort_rows(tr)]
    out = config.RECORDS / "efforts" / f"{pkg:03d}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame({"category": []}, schema={"category": pl.String})
    if df.height:
        df = df.with_columns(
            pl.Series("place0", country(df["lat0"].to_numpy(), df["lon0"].to_numpy())),
            pl.Series("place1", country(df["lat1"].to_numpy(), df["lon1"].to_numpy())))
    tmp = out.with_suffix(".part")
    df.write_parquet(tmp)
    tmp.replace(out)
    return len(rows)


# ---------- global: one animal published in several packages ----------

def identities(min_shared: int = 20, min_shared_same_id: int = 2, min_fraction: float = 0.5) -> pl.DataFrame:
    """Union of (pkg, ind) pairs that share at least min_fraction of the smaller animal's
    sampled fix keys. A short track samples few keys, so a pair with the same animal id needs
    only min_shared_same_id shared keys. Returns pkg, ind, animal (the canonical 'pkg:ind' of the group)."""
    parts = []
    for p in sorted(config.CLEAN.glob("*.fp.parquet")):
        parts.append(pl.read_parquet(p).with_columns(pl.lit(int(p.name[:3])).alias("pkg")))
    fp = pl.concat(parts)
    sizes = fp.group_by("pkg", "ind").len("n")
    pairs = (fp.join(fp, on="key").filter(pl.col("pkg") < pl.col("pkg_right"))
               .group_by("pkg", "ind", "pkg_right", "ind_right").len("shared")
               .join(sizes, on=["pkg", "ind"])
               .join(sizes.rename({"pkg": "pkg_right", "ind": "ind_right", "n": "n_right"}), on=["pkg_right", "ind_right"])
               .filter(((pl.col("shared") >= min_shared)
                        | ((pl.col("ind") == pl.col("ind_right")) & (pl.col("shared") >= min_shared_same_id)))
                       & (pl.col("shared") >= min_fraction * pl.min_horizontal("n", "n_right"))))
    parent: dict[tuple, tuple] = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    for r in pairs.iter_rows(named=True):
        a, b = find((r["pkg"], r["ind"])), find((r["pkg_right"], r["ind_right"]))
        if a != b:
            parent[max(a, b)] = min(a, b)
    size = {(r["pkg"], r["ind"]): r["n"] for r in sizes.iter_rows(named=True)}
    groups: dict[tuple, list[tuple]] = {}
    for x in size:
        groups.setdefault(find(x), []).append(x)
    rows = []
    for members in groups.values():
        canon = max(members, key=lambda m: (size[m], -m[0]))
        for m in members:
            rows.append({"pkg": m[0], "ind": m[1], "animal": f"{canon[0]}:{canon[1]}", "copies": len(members)})
    out = pl.DataFrame(rows)
    out.write_parquet(config.RECORDS / "identities.parquet")
    pairs.write_parquet(config.RECORDS / "identity_pairs.parquet")
    return out


def candidates() -> pl.DataFrame:
    """Best effort per category and animal (after the duplicate merge), with taxon facts."""
    ident = identities()
    frames = [pl.read_parquet(p) for p in sorted((config.RECORDS / "efforts").glob("*.parquet"))]
    df = pl.concat([f for f in frames if f.height], how="diagonal_relaxed").join(ident, on=["pkg", "ind"], how="left")
    df = df.with_columns(pl.coalesce([pl.col("animal"), pl.format("{}:{}", "pkg", "ind")]).alias("animal"),
                         pl.col("copies").fill_null(1))
    better = pl.col("category").replace_strict({k: c.better for k, c in BY_KEY.items()}, return_dtype=pl.Int8)
    df = (df.with_columns((pl.col("value") * better).alias("_score"))
            .sort(["category", "_score", "pkg"], descending=[False, True, False])
            .unique(["category", "animal"], keep="first", maintain_order=True).drop("_score"))
    df.write_parquet(config.RECORDS / "candidates.parquet")
    return df

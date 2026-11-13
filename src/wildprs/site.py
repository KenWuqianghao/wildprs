"""Static site: home, one page per board, one page per record, about. URL scheme:
  /                         home
  /b/<category>/            all-species board
  /b/<category>/<class>/    board of one animal class
  /r/<record-id>/           record page; record.json and card.png beside it
  /about/                   data source, method, limits, verification
All links are relative, so the site works under any base path and from a local server."""
from __future__ import annotations

import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl

from . import cartography as C
from . import config, replay, taxa
from .audit import BOARD_SIZE, BOARD_TITLES, STUDY_CAP
from .categories import BY_KEY, SEGMENT_BY_KEY, Category
from .clean import CEILING_MS, RULES, SPLIT_DOC
from .model import Track, haversine_km
from .pipeline import load_tracks
from .repo import packages

STATIC = Path(__file__).parent / "site" / "static"
SITE_NAME = "Wild PRs"
TAGLINE = "Personal records of individual wild animals, from open tracking data. Every record can be checked."
CARD_W, CARD_H = 1200, 675
WHOLE_TRACK = {"lifetime", "countries"}
POINT_LIKE = {"farthest-north", "farthest-south", "highest-flight"}
SCOPE_ORDER = ["all", "birds", "land-mammals", "marine-animals", "bats", "reptiles"]
esc = html.escape


# ---------- formatting ----------

def fmt_value(cat: Category, v: float) -> tuple[str, str]:
    if cat.unit == "deg":
        return f"{abs(v):.1f}°", "N" if v >= 0 else "S"
    if cat.unit == "h":
        return (f"{v:.1f}", "hours") if v < 48 else (f"{v / 24:.1f}", "days")
    if cat.unit == "count":
        return f"{v:.0f}", "countries"
    if cat.unit == "km/h":
        return f"{v:.0f}", "km/h"
    return f"{v:,.0f}", cat.unit


def fmt_duration(seconds: float) -> str:
    h = seconds / 3600
    if h < 48:
        return f"{h:.1f} hours"
    d = h / 24
    if d < 60:
        return f"{d:.1f} days"
    if d < 730:
        return f"{d / 30.44:.0f} months"
    return f"{d / 365.25:.1f} years"


def day(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%-d %b %Y")


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ordinal_rank(n: int) -> str:
    return f"#{n}"


def where(place: str, prep: str) -> str:
    return f"{prep} {place}" if place else "over open water"


def short_citation(citation: str) -> str:
    authors = citation.split(". ")[0]
    names = [a.strip() for a in authors.split(",")]
    year = next((p for p in citation.split(". ")[1:3] if p.strip().isdigit()), "")
    lead = names[0] + (" et al." if len(names) > 1 else "")
    return f"{lead} {year}".strip()


# ---------- record model for the site ----------

def hook(cat: Category, r: dict, value: str, unit: str) -> str:
    seg = SEGMENT_BY_KEY.get(cat.key)
    fields = {
        "value": value, "unit": unit, "duration": fmt_duration(r["t1"] - r["t0"]),
        "start_place": r["place0"] or "open water", "end_place": r["place1"] or "open water",
        "start_where": where(r["place0"], "over"), "end_where": where(r["place1"], "in"),
        "segment": seg.title if seg else "", "agl": f"{r.get('agl_m') or 0:,.0f}",
        "countries": "",
    }
    text = cat.hook
    if not (r["place0"] and r["place1"]) or r["place0"] == r["place1"]:
        text = text.replace(", from {start_place} to {end_place}", "")
    return text.format(**fields)


def animal_title(r: dict) -> str:
    return r["name"] or f"{r['common']} {r['ind']}"


def _geometry(r: dict, tr: Track, coarse: bool) -> dict:
    t = tr.t
    i0 = int(np.searchsorted(t, r["t0"]))
    i1 = int(np.searchsorted(t, r["t1"]))
    if r["category"] in WHOLE_TRACK:
        s0, s1 = 0, len(t) - 1
    elif r["category"] in POINT_LIKE:
        s0 = int(np.searchsorted(t, r["t0"] - 3 * 86400))
        s1 = min(len(t) - 1, int(np.searchsorted(t, r["t1"] + 3 * 86400)))
    else:
        s0, s1 = i0, i1
    raw = tr.lon[s0:s1 + 1]
    wrap = bool(raw.max() - raw.min() > 180)  # crosses the date line: draw in 0..360

    def lon(x):
        return np.where(x < 0, x + 360, x) if wrap else x
    return {"ctx": C.simplify(lon(raw), tr.lat[s0:s1 + 1], coarse),
            "eff": C.simplify(lon(tr.lon[i0:i1 + 1]), tr.lat[i0:i1 + 1], coarse), "wrap": wrap}


def map_view(lon: np.ndarray, lat: np.ndarray, w: int, h: int, place: str, thumb: bool = False,
             point: bool = False) -> C.MapView:
    return C.frame(lon, lat, w, h, place, min_span_deg=2.0 if thumb else (16.0 if point else 6.0))


def _lines(v: C.MapView, g: dict) -> tuple[str, str, np.ndarray, np.ndarray, int]:
    clon, clat = g["ctx"]
    elon, elat = g["eff"]
    cx, cy = v.xy(clon, clat)
    ex, ey = v.xy(elon, elat)
    ctx = " ".join(f"{a:.1f},{b:.1f}" for a, b in zip(cx, cy))
    eff = " ".join(f"{a:.1f},{b:.1f}" for a, b in zip(ex, ey))
    return ctx, eff, ex, ey, len(cx) if len(cx) != len(ex) else 0


def map_base(v: C.MapView, w: int, h: int, cls: str, thumb: bool = False, extra: str = "") -> str:
    """Sea, graticule, land and borders: the part of a map that never moves."""
    land = C.land_svg(v, tolerance_px=2.5 if thumb else 1.0)
    parts = [f'<svg class="{cls}" viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid slice" aria-hidden="true">',
             extra, f'<rect width="{w}" height="{h}" class="sea"/>']
    if not thumb:
        parts.append(f'<path class="grat" d="{C.graticule_svg(v)}"/>')
    parts.append(f'<path class="land" d="{land}"/>')
    if not thumb:
        parts.append(f'<path class="bord" d="{C.borders_svg(v)}"/>')
    return "".join(parts)


def map_svg(g: dict, v: C.MapView, w: int, h: int, cls: str, uid: str, coarse: bool, thumb: bool = False) -> str:
    ctx, eff, ex, ey, n_ctx = _lines(v, g)
    sw = 2.2 if thumb else 5
    grad = (f'<defs><linearGradient id="g{uid}" gradientUnits="userSpaceOnUse" x1="{ex[0]:.0f}" y1="{ey[0]:.0f}" '
            f'x2="{ex[-1]:.0f}" y2="{ey[-1]:.0f}"><stop offset="0" stop-color="var(--trk0)"/>'
            f'<stop offset="1" stop-color="var(--trk1)"/></linearGradient></defs>')
    parts = [map_base(v, w, h, cls, thumb, grad)]
    if n_ctx > 1:
        parts.append(f'<polyline class="ctx" points="{ctx}" stroke-width="{sw * 0.45:.1f}"/>')
    if not thumb:
        parts.append(f'<polyline class="glow" points="{eff}" stroke="url(#g{uid})" stroke-width="{sw * 4:.0f}"/>')
    parts.append(f'<polyline class="trk" points="{eff}" stroke="url(#g{uid})" stroke-width="{sw}"/>')
    if not thumb:
        r0 = 22 if coarse else 9
        parts.append(f'<circle class="{"zone" if coarse else "p0"}" cx="{ex[0]:.1f}" cy="{ey[0]:.1f}" r="{r0}"/>')
        parts.append(f'<circle class="{"zone" if coarse else "p1"}" cx="{ex[-1]:.1f}" cy="{ey[-1]:.1f}" r="{r0 + 2}"/>')
    parts.append("</svg>")
    return "".join(parts)


PROFILE_LABEL = {
    "speed": "Ground speed, km/h", "cumulative": "Distance along the track, km",
    "height": "Height above sea level, m, from 6 hours before", "latitude": "Latitude, from 3 days before",
    "round-trip": "Distance from the first fix, km",
}


def _profile(tf: dict, cat: Category) -> tuple[str, str]:
    """The chart under the poster, drawn from the same points as the replay, so the replay cursor fits it."""
    t = np.asarray(tf["t"], float)
    kind = "round-trip" if cat.key == "round-trip" else cat.profile
    if kind == "height":
        y = np.asarray([np.nan if x is None else x for x in tf.get("h", [None] * len(t))], float)
    elif kind == "latitude":
        y = np.asarray(tf["lat"], float)
        y = y - y.min() if cat.key == "farthest-north" else y.max() - y
    elif kind == "round-trip":
        lat, lon = np.asarray(tf["lat"]), np.asarray(tf["lon"])
        y = haversine_km(lat[0], lon[0], lat, lon)
    elif kind == "cumulative":
        y = np.maximum(np.asarray(tf["km"]) - tf["km"][tf["eff"][0]], 0)
    else:
        y = np.asarray(tf["kmh"], float)
    return C.profile_svg(t, y, bands=tf["night"]), PROFILE_LABEL[kind]


# ---------- replay readout (motion.js formats the live values the same way) ----------

def fmt_clock(sec: float) -> str:
    sign, sec = ("−" if sec < 0 else ""), abs(sec)
    h = sec / 3600
    if h < 48:
        return f"{sign}{int(h)}:{int(sec % 3600 // 60):02d} h"
    d = h / 24
    if d < 60:
        return f"{sign}{int(d)} d {int(h % 24):02d} h"
    if d < 730:
        return f"{sign}{d:,.0f} d"
    return f"{sign}{d / 365.25:.1f} years"


def fmt_km(km: float) -> str:
    return f"{km:.1f} km" if km < 10 else f"{km:,.0f} km"


def readout(tf: dict) -> list[tuple[str, str, str]]:
    """(key, label, final value) of each readout cell. The final values are the static state.
    Distance counts from the start of the replay; for a distance record it is the record's own distance."""
    ea, eb = tf["eff"]
    secs = tf["value"] * 3600 if tf["category"] in SEGMENT_BY_KEY else tf["t"][eb] - tf["t_zero"]
    km = tf["m"][eb] if tf["category"] in replay.DISTANCE else max(tf["km"][eb] - tf["km"][0], 0)
    approach = tf["category"] in replay.APPROACH_S
    speed = tf["kmh"][eb] if approach or secs <= 0 else (km if tf["category"] in replay.DISTANCE
                                                         else tf["km"][eb] - tf["km"][ea]) / (secs / 3600)
    cells = [("time", "Time", fmt_clock(secs)), ("dist", "Distance", fmt_km(km)),
             ("speed", "Speed", f"{speed:,.0f} km/h")]
    if "h" in tf:
        hv = tf["h"][eb]
        cells.append(("height", "Height", f"{hv:,.0f} m" if hv is not None else "-"))
    return cells


# ---------- page shell ----------

def page(rel: str, title: str, body: str, meta: str = "", cls: str = "", script: bool = False) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{rel}static/style.css">
<link rel="icon" href="{rel}static/icon.svg" type="image/svg+xml">
{meta}
{f'<script src="{rel}static/motion.js" defer></script>' if script else ""}
</head><body class="{cls}">
<header class="top"><a class="brand" href="{rel}"><span class="mark" aria-hidden="true"></span>{SITE_NAME}</a>
<nav><a href="{rel}#boards">Boards</a><a href="{rel}about/">Method</a></nav></header>
<main>{body}</main>
<footer class="foot"><p>Data: <a href="https://datarepository.movebank.org">Movebank Data Repository</a>, CC0 packages only.
Maps: Natural Earth. Terrain: NOAA ETOPO1. Names and Red List status: GBIF.</p>
<p><a href="{rel}about/#verify">How to check a record</a> · <a href="{rel}about/#errors">Report an error</a></p></footer>
</body></html>"""


def social_meta(title: str, desc: str, url: str, image: str) -> str:
    return "\n".join([
        f'<meta name="description" content="{esc(desc)}">',
        f'<meta property="og:type" content="article">', f'<meta property="og:site_name" content="{SITE_NAME}">',
        f'<meta property="og:title" content="{esc(title)}">', f'<meta property="og:description" content="{esc(desc)}">',
        f'<meta property="og:url" content="{esc(url)}">', f'<meta property="og:image" content="{esc(image)}">',
        f'<meta property="og:image:width" content="{CARD_W}">', f'<meta property="og:image:height" content="{CARD_H}">',
        f'<meta property="og:image:alt" content="{esc(title)}">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{esc(title)}">', f'<meta name="twitter:description" content="{esc(desc)}">',
        f'<meta name="twitter:image" content="{esc(image)}">', f'<link rel="canonical" href="{esc(url)}">'])


def poster(rec: dict, rel: str) -> str:
    v, u = rec["display"]["value"], rec["display"]["unit"]
    long = len(v) + len(u) > 7
    species = f"<span>{esc(rec['animal']['common'])}</span>" if rec["animal"]["name"] else ""
    return f"""<div class="poster-wrap"><article class="poster" aria-label="{esc(rec['share_title'])}">
{rec['_map']}
<div class="p-top"><span class="chip">{esc(rec['chip'])}</span></div>
<div class="p-main">
<p class="num{' long' if long else ''}">{esc(v)}<span class="u">{esc(u)}</span></p>
<p class="hook">{esc(rec['hook'])}</p>
<p class="who"><b>{esc(rec['animal']['title'])}</b>{species}<span>{esc(rec['when'])}</span></p>
</div>
<p class="p-brand"><span class="mark" aria-hidden="true"></span>{SITE_NAME}</p>
<p class="p-credit">Data: {esc(rec['dataset']['short'])} · Movebank · {esc(rec['dataset']['licence_short'])}</p>
</article></div>"""


# ---------- build ----------

def video_records(boards: dict) -> set[str]:
    """The number one of every all-species board gets a replay video."""
    return {sc["all"][0] for sc in boards.values() if "all" in sc}


def race_kind(key: str) -> str | None:
    """'map': segment boards race on one shared map. 'chart': distance against time. None: no race."""
    if key in SEGMENT_BY_KEY:
        return "map"
    return "chart" if key in CHART_RACES else None


def race_videos(boards: dict) -> set[tuple[str, str]]:
    return {(k, "all") for k in boards if k in SEGMENT_BY_KEY and "all" in boards[k]}


CHART_RACES = {"biggest-day", "biggest-week", "fastest-hour", "sea-crossing", "lifetime"}

def _scope_rank(boards: dict, rid: str) -> dict:
    return {s: ids.index(rid) + 1 for s, ids in boards.items() if rid in ids}


def build(base_url: str, png: bool = True) -> dict:
    base_url = base_url.rstrip("/")
    B = json.loads((config.AUDIT / "boards.json").read_text())
    boards, competitors = B["boards"], B["competitors"]
    v = pl.read_parquet(config.AUDIT / "verdicts.parquet")
    public_ids = {i for b in boards.values() for ids in b.values() for i in ids}
    rows = {r["id"]: r for r in v.filter(pl.col("id").is_in(list(public_ids))).iter_rows(named=True)}
    missing = sorted({r["taxon"] for r in rows.values() if r["taxon"] not in taxa.curated_names()})
    if missing:
        raise SystemExit(f"names.toml: no curated common name for {missing}")
    if any(r["state"] != "credible" for r in rows.values()):
        raise SystemExit("a board holds a record that is not credible")

    out = config.SITE
    if out.exists():
        shutil.rmtree(out)
    (out / "static").mkdir(parents=True)
    shutil.copytree(STATIC, out / "static", dirs_exist_ok=True)

    pk = packages()
    recs: dict[str, dict] = {}
    by_pkg: dict[int, list[dict]] = {}
    for r in rows.values():
        by_pkg.setdefault(r["pkg"], []).append(r)
    for pkg, rs in sorted(by_pkg.items()):
        tracks = {(t.animal.ind, t.sensor): t for t in load_tracks(pkg)}
        for r in rs:
            recs[r["id"]] = _record(r, tracks[(r["ind"], r["sensor"])], boards[r["category"]], competitors, pk[pkg])
    by_animal: dict[str, list[str]] = {}
    for rid, rec in recs.items():
        by_animal.setdefault(rec["_animal_key"], []).append(rid)

    video_ids = video_records(boards)
    for rid, rec in recs.items():
        d = out / "r" / rid
        d.mkdir(parents=True)
        public = {k: x for k, x in rec.items() if not k.startswith("_")}
        (d / "record.json").write_text(json.dumps(public, indent=1, ensure_ascii=False))
        (d / "track.json").write_text(json.dumps(rec["_track"], separators=(",", ":")))
        others = [recs[o] for o in by_animal[rec["_animal_key"]] if o != rid]
        (d / "index.html").write_text(_record_page(rec, others, base_url, rid in video_ids))
        (d / "poster.html").write_text(_poster_page(rec))
        if rid in video_ids:
            (d / "poster-bare.html").write_text(_poster_page(rec, bare=True))

    for key, scopes in boards.items():
        for scope, ids in scopes.items():
            d = out / "b" / key / ("" if scope == "all" else scope)
            d.mkdir(parents=True, exist_ok=True)
            rs = [recs[i] for i in ids]
            rc = race(BY_KEY[key], rs)
            if rc:
                (d / "race.json").write_text(json.dumps(rc[1], separators=(",", ":"), ensure_ascii=False))
            (d / "index.html").write_text(_board_page(BY_KEY[key], scope, rs, scopes, competitors[key], base_url,
                                                      rc[0] if rc else "", (key, scope) in race_videos(boards)))
    (out / "about").mkdir()
    (out / "about" / "index.html").write_text(_about_page(boards))
    (out / "index.html").write_text(_home_page(boards, recs, base_url))
    (out / "boards.json").write_text(json.dumps(
        {k: {s: [{"rank": i + 1, "id": rid, "value": recs[rid]["value"], "unit": recs[rid]["unit"]}
                 for i, rid in enumerate(ids)] for s, ids in sc.items()} for k, sc in boards.items()}, indent=1))
    n_png = 0
    if png:
        from .render import render_cards
        n_png = render_cards(out, sorted(recs), sorted(video_ids))
    for p in [*out.glob("r/*/poster.html"), *out.glob("r/*/poster-bare.html")]:
        p.unlink()
    from .video import restore_cached
    restore_cached(out)
    return {"records": len(recs), "boards": sum(len(s) for s in boards.values()), "pngs": n_png}


def _record(r: dict, tr: Track, cat_boards: dict, competitors: dict, p) -> dict:
    cat = BY_KEY[r["category"]]
    i0, i1 = int(np.searchsorted(tr.t, r["t0"])), int(np.searchsorted(tr.t, r["t1"]))
    sens = C.sensitivity(tr, i0, i1)
    if r["category"] == "round-trip":
        sens = sens or "the start of a round trip is a likely nest, roost or colony"
    coarse = sens is not None
    value, unit = fmt_value(cat, r["value"])
    ranks = _scope_rank(cat_boards, r["id"])
    scope = "all" if "all" in ranks else next(iter(ranks))
    chip_scope = "" if scope == "all" else f" {BOARD_TITLES[scope].lower()}"
    ev = json.loads(r["evidence"])
    ev_public = {k: x for k, x in ev.items() if not k.endswith(("_lat", "_lon")) and k != "far_index"}
    g = _geometry(r, tr, coarse)
    point = cat.key in POINT_LIKE
    allx, ally = np.r_[g["ctx"][0], g["eff"][0]], np.r_[g["ctx"][1], g["eff"][1]]
    v_poster = map_view(allx, ally, CARD_W, CARD_H, "right", point=point)
    v_thumb = map_view(allx, ally, 160, 90, "full", thumb=True)
    tf = replay.track_file(r["id"], cat.key, cat.unit, r["value"], tr, r["t0"], r["t1"], v_poster, g["wrap"], coarse)
    prof, prof_label = _profile(tf, cat)
    title = animal_title(r)
    lic = p.licence
    rec = {
        "id": r["id"], "pipeline_version": config.PIPELINE_VERSION,
        "category": cat.key, "category_title": cat.title, "rule": cat.rule,
        "value": round(r["value"], 3), "unit": cat.unit, "display": {"value": value, "unit": unit},
        "rank": ranks, "competitors": {s: competitors[cat.key].get(s, 0) for s in ranks},
        "state": r["state"], "state_rule": r["state_rule"], "state_reason": r["state_reason"],
        "animal": {"id": r["ind"], "title": title, "name": r["name"], "common": r["common"], "taxon": r["taxon"],
                   "iucn": r["iucn"], "class": r["board_class"], "copies_merged": int(r["copies"])},
        "start": iso(r["t0"]), "end": iso(r["t1"]), "duration_h": round((r["t1"] - r["t0"]) / 3600, 2),
        "places": {"start": r["place0"], "end": r["place1"]},
        "evidence": {"fixes_in_record": int(r["n_fixes"]), "supporting_fixes": int(r["support"]),
                     "sensor": r["sensor"], "max_leg_kmh": round(r["max_leg_kmh"], 1),
                     "max_gap_h": round(r["max_gap_h"], 2), "median_interval_min": round(r["median_interval_min"], 1),
                     "track_fixes": int(r["track_fixes"]), **({"terrain_m": r["terrain_m"], "above_terrain_m": round(r["agl_m"])}
                                                               if r.get("agl_m") is not None else {}), **ev_public},
        "geometry": {"coarsened_to_deg": C.COARSE_DEG if coarse else None, "reason": sens},
        "dataset": {"doi": p.doi, "url": p.doi_url, "title": p.title, "citation": p.citation, "licence": lic,
                    "short": short_citation(p.citation), "licence_short": "CC0" if "CC0" in lic else lic},
        "reproduce": {"doi": p.doi, "animal": r["ind"], "sensor": r["sensor"], "category": cat.key,
                      "start": iso(r["t0"]), "end": iso(r["t1"]), "t0": r["t0"], "t1": r["t1"],
                      "command": f"wildprs verify {r['id']}"},
    }
    rec["hook"] = hook(cat, r, value, unit)
    if cat.key == "countries":
        rec["countries"] = ev.get("list", [])
    rec["chip"] = f"{ordinal_rank(ranks[scope])}{chip_scope} · {cat.title}"
    rec["when"] = day(r["t0"]) if r["t1"] - r["t0"] < 86400 * 2 else f"{day(r['t0'])} to {day(r['t1'])}"
    if cat.key in WHOLE_TRACK:
        rec["when"] = f"{datetime.fromtimestamp(r['t0'], timezone.utc).year} to {datetime.fromtimestamp(r['t1'], timezone.utc).year}"
    rec["share_title"] = f"{cat.title}: {value} {unit}. {title}" + (f", {r['common']}" if r["name"] else "")
    rec["_map"] = map_svg(g, v_poster, CARD_W, CARD_H, "map", r["id"][-6:], coarse)
    rec["_thumb"] = map_svg(g, v_thumb, 160, 90, "thumb", "t" + r["id"][-6:], coarse, thumb=True)
    rec["_track"], rec["_thumb_view"], rec["_wrap"] = tf, v_thumb, g["wrap"]
    rec["_profile"], rec["_profile_label"] = prof, prof_label
    rec["_animal_key"] = r["animal"]
    return rec


def _poster_page(rec: dict, bare: bool = False) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="robots" content="noindex">
<link rel="stylesheet" href="../../static/style.css"></head><body class="card-only{' bare' if bare else ''}">{poster(rec, "../../")}</body></html>"""


def _stat(label: str, value: str) -> str:
    return f'<div class="stat"><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>'


ICON_PLAY = '<svg viewBox="0 0 20 20" aria-hidden="true"><path class="i-play" d="M6 4l10 6-10 6z"/><path class="i-pause" d="M5 4h3.5v12H5zM11.5 4H15v12h-3.5z"/></svg>'
ICON_RESTART = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4a6 6 0 1 1-5.6 3.9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3 3v5h5z"/></svg>'


def replay_block(rec: dict) -> str:
    """Poster, readout and controls. Without JS this is the static poster with the final values."""
    cells = "".join(f'<div class="rc"><span class="rl">{esc(label)}</span><b data-k="{k}">{esc(v)}</b></div>'
                    for k, label, v in readout(rec["_track"]))
    sun = '<div class="rc rsun" hidden><span class="rl">Sun</span><b data-k="sun">Day</b></div>' if rec["_track"]["night"] else ""
    return f"""<div class="replay" data-track="track.json" data-final="{esc(rec['display']['value'])}">
{poster(rec, "")}
<div class="ro">{cells}{sun}
<div class="rctl"><button type="button" class="b-play" aria-label="Play the replay">{ICON_PLAY}</button>
<button type="button" class="b-restart" aria-label="Restart the replay">{ICON_RESTART}</button>
<span class="spd" role="group" aria-label="Replay speed"><button type="button" data-s="0.5" aria-pressed="false">0.5×</button><button type="button" data-s="1" aria-pressed="true">1×</button><button type="button" data-s="2" aria-pressed="false">2×</button></span></div></div>
</div>"""


def _record_page(rec: dict, others: list[dict], base_url: str, video: bool) -> str:
    rel = "../../"
    url = f"{base_url}/r/{rec['id']}/"
    ev = rec["evidence"]
    scope_links = " ".join(
        f'<a href="{rel}b/{rec["category"]}/{"" if s == "all" else s + "/"}">{ordinal_rank(n)} of {rec["competitors"][s]:,} '
        f'{"animals" if s == "all" else BOARD_TITLES[s].lower()}</a>' for s, n in rec["rank"].items())
    stats = [_stat("Start", rec["start"].replace("T", " ").replace("Z", " UTC")),
             _stat("End", rec["end"].replace("T", " ").replace("Z", " UTC")),
             _stat("Duration", fmt_duration(rec["duration_h"] * 3600)),
             _stat("Fixes in the record", f"{ev['fixes_in_record']:,}"),
             _stat("Sensor", ev["sensor"].upper() if ev["sensor"] == "gps" else "Argos"),
             _stat("Median fix interval", f"{ev['median_interval_min']:.0f} min")]
    if "above_terrain_m" in ev:
        stats.append(_stat("Above the terrain", f"{ev['above_terrain_m']:,} m"))
    if "straight_kmh" in ev:
        stats.append(_stat("Straight-line speed", f"{ev['straight_kmh']:.0f} km/h"))
    countries = ""
    if rec.get("countries"):
        countries = f'<p class="countries">{esc(", ".join(rec["countries"]))}</p>'
    other_html = ""
    if others:
        items = "".join(f'<li><a href="{rel}r/{o["id"]}/"><span class="ov">{esc(o["display"]["value"])} {esc(o["display"]["unit"])}</span>'
                        f'<span>{esc(o["category_title"])}</span></a></li>' for o in others)
        other_html = f'<section class="block"><h2>Other records of {esc(rec["animal"]["title"])}</h2><ul class="others">{items}</ul></section>'
    coarse = ""
    if rec["geometry"]["reason"]:
        coarse = f'<p class="note">The map shows the track at {C.COARSE_DEG}° resolution with no exact endpoints: {esc(rec["geometry"]["reason"])}.</p>'
    a = rec["animal"]
    sci = f'<i>{esc(a["taxon"])}</i>' + (f' · IUCN Red List: {esc(a["iucn"])}' if a["iucn"] in ("VU", "EN", "CR", "NT") else "")
    recipe = json.dumps(rec["reproduce"], indent=1)
    body = f"""
<div class="rec">
{replay_block(rec)}
<section class="headline">
<p class="kicker">{scope_links}</p>
<h1>{esc(rec["hook"])}</h1>
<p class="sub"><b>{esc(a["title"])}</b>, {esc(a["common"].lower() if a["common"] else "")} ({sci})</p>
{countries}
</section>
<section class="block profile-block"><h2>{esc(rec["_profile_label"])}</h2><div class="scrub">{rec["_profile"]}<span class="cur" hidden></span></div>
<div class="axis"><span>{esc(day(rec["_track"]["t_start"] + rec["_track"]["t"][0]))}</span><span>{esc(day(rec["_track"]["t_start"] + rec["_track"]["t"][-1]))}</span></div>
{'<p class="dl"><a href="replay.mp4" download>Download the replay video (MP4)</a></p>' if video else ''}</section>
<section class="block"><h2>The record</h2><p class="rule">{esc(rec["category_title"])}: {esc(rec["rule"])}</p>
<dl class="stats">{"".join(stats)}</dl>
<p class="state"><span class="dot"></span>Credible. {esc(rec["state_reason"])}</p>
{coarse}</section>
{other_html}
<section class="block"><h2>Data</h2><p class="cite">{esc(rec["dataset"]["citation"])}</p>
<p><a href="{esc(rec["dataset"]["url"])}">{esc(rec["dataset"]["doi"])}</a> · {esc(rec["dataset"]["licence"])}</p></section>
<section class="block repro" id="reproduce"><h2>Reproduce</h2>
<p>Any person can recompute this record from the public data package. The command downloads the package again, applies the same cleaning and prints MATCH or MISMATCH.</p>
<pre class="cmd"><code>uv run wildprs verify {esc(rec["id"])} --site {esc(base_url)}</code></pre>
<p>The code is at <a href="{config.SOURCE_URL}">{config.SOURCE_URL.removeprefix("https://")}</a>.</p>
<p>Recipe (<a href="record.json">record.json</a>):</p>
<pre><code>{esc(recipe)}</code></pre></section>
</div>"""
    desc = f"{rec['hook']} {a['title']}, {a['common']}. Data: {rec['dataset']['short']}, Movebank."
    meta = social_meta(f"{rec['share_title']}", desc, url, f"{base_url}/r/{rec['id']}/card.png")
    return page(rel, f"{rec['share_title']} · {SITE_NAME}", body, meta, "page-record", script=True)


RACE_W, RACE_H = 1200, 675
CHART_PLOT = (70, 110, 930, 610)  # x0, y0, x1, y1 of the plot area in race units; labels go right of x1


def race_subtitle(cat: Category, n: int) -> str:
    seg = SEGMENT_BY_KEY.get(cat.key)
    if seg:
        return f"Top {n} crossings, {seg.span.split(',')[0]}, same start, true speeds"
    return f"Top {n}, distance against time, same start"


def race(cat: Category, recs: list[dict]) -> tuple[str, dict] | None:
    """Markup and race.json of a board race. The markup shows the finished race without JS."""
    kind = race_kind(cat.key)
    if kind is None:
        return None
    runners = []
    if kind == "map":
        lon = np.concatenate([np.asarray(r["_track"]["lon"])[r["_track"]["eff"][0]:r["_track"]["eff"][1] + 1] for r in recs])
        lat = np.concatenate([np.asarray(r["_track"]["lat"])[r["_track"]["eff"][0]:r["_track"]["eff"][1] + 1] for r in recs])
        v = C.frame(lon, lat, RACE_W, RACE_H, "full", min_span_deg=4.0)
    for i, r in enumerate(recs, 1):
        view = v if kind == "map" else r["_thumb_view"]
        a = r["animal"]
        runners.append({"id": r["id"], "rank": i, "name": a["title"], "species": a["common"] if a["name"] else "",
                        "display": f'{r["display"]["value"]} {r["display"]["unit"]}'} | replay.race_runner(r["_track"], view))
    data = {"v": 1, "kind": kind, "category": cat.key, "title": cat.title, "unit": cat.unit,
            "finish": max(x["finish"] for x in runners), "runners": runners}
    if kind == "map":
        seg = SEGMENT_BY_KEY[cat.key]
        gates = []
        for g in (seg.gate_a, seg.gate_b):
            gx, gy = v.xy([g[0][0], g[1][0]], [g[0][1], g[1][1]])
            gates.append([round(float(c), 1) for c in (gx[0], gy[0], gx[1], gy[1])])
        data["gates"] = gates
        routes = "".join(f'<polyline class="rr" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in zip(rn["x"], rn["y"]))}"/>'
                         for rn in runners)
        gl = "".join(f'<line class="gate" x1="{g[0]}" y1="{g[1]}" x2="{g[2]}" y2="{g[3]}"/>' for g in gates)
        art = map_base(v, RACE_W, RACE_H, "map race-map") + gl + routes + "</svg>"
    else:
        dmax = max(max(rn["d"]) for rn in runners) or 1
        data["plot"] = list(CHART_PLOT)
        data["dmax"] = dmax
        x0, y0, x1, y1 = CHART_PLOT

        def pts(rn):
            return " ".join(f"{x0 + t / data['finish'] * (x1 - x0):.1f},{y1 - d / dmax * (y1 - y0):.1f}"
                            for t, d in zip(rn["t"], rn["d"]) if t >= 0)
        lines = "".join(f'<polyline class="cr{" lead" if rn["rank"] == 1 else ""}" points="{pts(rn)}"/>' for rn in reversed(runners))
        art = (f'<svg class="map race-chart" viewBox="0 0 {RACE_W} {RACE_H}" aria-hidden="true"><rect class="sea" width="{RACE_W}" height="{RACE_H}"/>'
               f'<path class="axis-l" d="M{x0},{y0}V{y1}H{x1}"/>{lines}</svg>')
    head = (f'<p class="race-t">{esc(cat.title)}</p>'
            f'<p class="race-k">{race_subtitle(cat, len(recs))}</p>'
            f'<p class="race-clock" aria-hidden="true"></p>'
            f'<p class="race-brand"><span class="mark" aria-hidden="true"></span>{SITE_NAME}</p>')
    html_ = f"""<section class="race" data-race="race.json" data-kind="{kind}" aria-label="Race of the top {len(recs)}">
<div class="race-stage">{art}{head}</div>
<div class="rctl"><button type="button" class="b-play" aria-label="Play the race">{ICON_PLAY}</button>
<button type="button" class="b-restart" aria-label="Restart the race">{ICON_RESTART}</button>
<span class="spd" role="group" aria-label="Race speed"><button type="button" data-s="0.5" aria-pressed="false">0.5×</button><button type="button" data-s="1" aria-pressed="true">1×</button><button type="button" data-s="2" aria-pressed="false">2×</button></span></div>
</section>"""
    return html_, data


def _scope_tabs(key: str, scope: str, scopes: dict, rel: str) -> str:
    tabs = []
    for s in SCOPE_ORDER:
        if s not in scopes:
            continue
        href = f"{rel}b/{key}/" + ("" if s == "all" else f"{s}/")
        tabs.append(f'<a class="tab{" on" if s == scope else ""}" href="{href}"{" aria-current=page" if s == scope else ""}>{esc(BOARD_TITLES[s])}</a>')
    return f'<nav class="tabs" aria-label="Animal class">{"".join(tabs)}</nav>'


def _board_page(cat: Category, scope: str, recs: list[dict], scopes: dict, comp: dict, base_url: str,
                race_html: str = "", video: bool = False) -> str:
    rel = "../../" if scope == "all" else "../../../"
    rows = []
    for i, rec in enumerate(recs, 1):
        a = rec["animal"]
        rows.append(f"""<li><a href="{rel}r/{rec['id']}/">
<span class="rk">{i}</span>{rec['_thumb']}
<span class="bwho"><b>{esc(a['title'])}</b><span>{esc(a['common'] if a['name'] else a['taxon'])}</span></span>
<span class="val">{esc(rec['display']['value'])}<small>{esc(rec['display']['unit'])}</small></span>
<span class="when">{esc(rec['when'])}</span></a></li>""")
    title = cat.title + ("" if scope == "all" else f" · {BOARD_TITLES[scope]}")
    body = f"""<section class="board-head"><p class="kicker">Leaderboard · {comp.get(scope, 0):,} animals compete</p>
<h1>{esc(title)}</h1><p class="rule">{esc(cat.rule)}</p>{_scope_tabs(cat.key, scope, scopes, rel)}</section>
{race_html}{'<p class="dl"><a href="race.mp4" download>Download the race video (MP4)</a></p>' if video else ''}
<ol class="board">{"".join(rows)}</ol>
<p class="note">One entry per animal, at most {STUDY_CAP} per study. Only records in the state credible are shown. <a href="{rel}about/#states">How records are checked</a>.</p>"""
    top = recs[0]
    url = f"{base_url}/b/{cat.key}/" + ("" if scope == "all" else f"{scope}/")
    meta = social_meta(f"{title} · {SITE_NAME}", f"{cat.rule} #1: {top['animal']['title']}, {top['display']['value']} {top['display']['unit']}.",
                       url, f"{base_url}/r/{top['id']}/card.png")
    return page(rel, f"{title} · {SITE_NAME}", body, meta, "page-board", script=bool(race_html))


def _home_page(boards: dict, recs: dict, base_url: str) -> str:
    rel = ""
    cards, index = [], []
    for key, cat in BY_KEY.items():
        if "all" not in boards.get(key, {}):
            continue
        top = recs[boards[key]["all"][0]]
        cards.append(f'<a class="card" href="r/{top["id"]}/" data-track="r/{top["id"]}/track.json" data-bare="r/{top["id"]}/card-bare.jpg">'
                     f'<img src="r/{top["id"]}/card.png" width="{CARD_W}" height="{CARD_H}" '
                     f'loading="lazy" alt="{esc(top["share_title"])}"></a>')
        index.append(f'<li><a href="b/{key}/"><span class="bt">{esc(cat.title)}</span>'
                     f'<span class="bv">{esc(top["display"]["value"])} {esc(top["display"]["unit"])}</span>'
                     f'<span class="bw">{esc(top["animal"]["title"])}{" · " + esc(top["animal"]["common"]) if top["animal"]["name"] else ""}</span></a></li>')
    cov = _coverage()
    body = f"""<section class="hero"><h1>Personal records of wild animals.</h1>
<p>{esc(TAGLINE)} Computed from {cov['packages']:,} open GPS and Argos data packages, {cov['animals']:,} animals.</p></section>
<section class="cards">{"".join(cards[:8])}</section>
<section id="boards" class="boards"><h2>All boards</h2><ul class="board-index">{"".join(index)}</ul></section>
<section class="cards more">{"".join(cards[8:])}</section>"""
    first = recs[boards[next(iter(boards))]["all"][0]]
    meta = social_meta(SITE_NAME, TAGLINE, base_url + "/", f"{base_url}/r/{first['id']}/card.png")
    return page(rel, f"{SITE_NAME}: personal records of wild animals", body, meta, "page-home", script=True)


def _coverage() -> dict:
    pk = sum(1 for p in config.CLEAN.glob("*.json") if json.loads(p.read_text())["tracks"])
    animals = pl.read_parquet(config.RECORDS / "candidates.parquet", columns=["animal"])["animal"].n_unique()
    return {"packages": pk, "animals": animals}


def _about_page(boards: dict) -> str:
    rel = "../"
    from .audit import MIN_AGL_M
    rules = "".join(f"<li><b>{esc(r.name.replace('_', ' '))}</b>. {esc(r.doc)}</li>" for r in RULES)
    rules += f"<li><b>split at jumps</b>. {esc(SPLIT_DOC)}</li>"
    ceil = ", ".join(f"{g.value.replace('_', ' ')} {v:.0f} m/s" for g, v in CEILING_MS.items())
    cats = "".join(
        f"<tr><td>{esc(c.title)}</td><td>{esc(c.rule)}</td><td>{'GPS' if c.sensors == {'gps'} else 'GPS, Argos'}</td>"
        f"<td>{esc(', '.join(f'{g.value.replace(chr(95), chr(32))}: {v:g} {c.unit}' for g, v in c.review_beyond.items())) or '-'}</td></tr>"
        for c in BY_KEY.values())
    nc = json.loads((config.META / "not_covered.json").read_text()) if (config.META / "not_covered.json").exists() else []
    cov = _coverage()
    body = f"""<article class="prose">
<h1>Method</h1>
<p>Each record belongs to one tracked animal. It comes from the location data that researchers published in the
<a href="https://datarepository.movebank.org">Movebank Data Repository</a>. This site uses only the packages with the CC0 licence:
{cov['packages']:,} packages and {cov['animals']:,} animals with GPS or Argos fixes and at least one result. An animal that is in more than one package counts once.{f' {len(nc)} CC0 packages could not be downloaded and are not covered.' if nc else ''}</p>
<h2>From fixes to records</h2>
<ol><li>Download each package. Keep one fix per animal, sensor and 5-minute interval (the first visible fix).</li>
<li>Apply the cleaning rules below, in this order.</li>
<li>Compute the best effort of each animal in each category.</li>
<li>Merge animals that appear in two packages: if half or more of the fixes of the smaller copy match the other copy (same hour, same position to 10 m), the animal holds a record once.</li>
<li>Set the state of each record: credible, under review or artefact. Only credible records are on the boards.</li></ol>
<h2>Cleaning rules</h2><ol>{rules}</ol><p>Speed ceilings: {esc(ceil)}.</p>
<h2 id="states">Record states</h2>
<p>A record is <b>credible</b> when a label in <a href="{config.SOURCE_URL}/blob/main/labels.toml">labels.toml</a> marks it credible and states the evidence, or when it passes the automatic rules.
It is <b>under review</b> when it is beyond the automatic limit for its animal group, or when a label puts it on hold.
It is an <b>artefact</b> when it breaks a physical limit, sits at a pole, (highest flight) is less than {MIN_AGL_M} m above the highest terrain within 3 km,
or (speed and distance over hours or days) uses Argos positions that the data package documents as model output.
The labels and their reasons are in <code>labels.toml</code> in the source.</p>
<h2>Categories</h2><div class="tablewrap"><table><thead><tr><th>Category</th><th>Rule</th><th>Sensors</th><th>Automatic limit</th></tr></thead><tbody>{cats}</tbody></table></div>
<p>Boards show the top {BOARD_SIZE}: one entry per animal and at most {STUDY_CAP} per study.</p>
<h2>Sensitive animals</h2>
<p>For a threatened taxon (IUCN Red List VU, EN or CR) and for a record with an endpoint within 2 km of a likely nest, roost or colony, the map shows the track at 0.1° resolution and no exact endpoints. The replay of such a record uses the same 0.1° positions with times on a 15-minute grid, and its speeds are calculated from those coarse points only.
The site never publishes fix tables, only simplified track lines with a few hundred points.</p>
<h2 id="verify">How to check a record</h2>
<p>Each record page gives a recipe: the DOI of the data package, the animal id, the sensor and the time range. Get the source code at <a href="{config.SOURCE_URL}">{config.SOURCE_URL.removeprefix("https://")}</a> and run the command that the record page shows:</p>
<pre><code>uv run wildprs verify &lt;record-id&gt; --site &lt;address of this site&gt;</code></pre>
<p>The command finds the DOI in the repository, downloads the package again, applies the same cleaning and category rule, and prints MATCH or MISMATCH.</p>
<h2>Limits</h2>
<ul><li>Records depend on the sampling of each tag. A tag with one fix per day can not show a fast hour.</li>
<li>Ground speed includes wind. A bird with a strong tailwind can be much faster than its air speed.</li>
<li>Argos positions have errors of hundreds of metres to kilometres; only location classes 3, 2 and 1 are used.</li>
<li>GPS heights have errors of tens of metres, and more in fast flight.</li>
<li>Common names come from a curated table; Red List status comes from GBIF.</li></ul>
<h2 id="errors">Report an error</h2>
<p>Open an issue at <a href="{config.SOURCE_URL}/issues">{config.SOURCE_URL.removeprefix("https://")}</a>. Give the record id and the reason.</p>
</article>"""
    return page(rel, f"Method · {SITE_NAME}", body, "", "page-about")

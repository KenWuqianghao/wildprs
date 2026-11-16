"""Static site: home, one page per board, one page per record, about. URL scheme:
  /                         home
  /b/<category>/            all-species board
  /b/<category>/<class>/    board of one animal class
  /r/<record-id>/           record page; record.json and the share images (card*.png) beside it
  /about/                   data source, method, limits, verification
  /credits/                 every animal photo with its creator and licence
  /art/                     photo files (from data/art)
All links are relative, so the site works under any base path and from a local server."""
from __future__ import annotations

import html
import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import polars as pl

from . import cartography as C
from . import art, config, geo, replay, taxa, zoo
from .audit import BOARD_SIZE, BOARD_TITLES, STUDY_CAP
from .categories import BY_KEY, SEGMENT_BY_KEY, Category
from .clean import CEILING_MS, RULES, SPLIT_DOC
from .model import LANES, OG_FORMAT, SHARE_FORMATS, ArtEntry, Track, haversine_km
from .pipeline import load_tracks
from .repo import packages

STATIC = Path(__file__).parent / "site" / "static"
SITE_NAME = "Wild PRs"
HEADLINE = "Personal records of wild animals."
TAGLINE = "Personal records of individual wild animals, from open tracking data. Every record can be checked."
CARD_W, CARD_H = 1200, 675
MAP_W, MAP_H = 1200, 675
CARD_PHASE = 0.25
WHOLE_TRACK = {"lifetime", "countries"}
POINT_LIKE = {"farthest-north", "farthest-south", "highest-flight"}
MIN_SPAN, MIN_SPAN_COARSE = 0.1, 1.0


@dataclass(frozen=True)
class Group:
    label: str
    colour: str


GROUPS = {k: Group(BOARD_TITLES[k].lower(), c) for k, c in (
    ("birds", "#1d4ed8"), ("land-mammals", "#1f7a4a"), ("marine-animals", "#0f7f86"), ("bats", "#5b3fc4"),
    ("reptiles", "#b7791f"))}
SCOPE_ORDER = ["all", *GROUPS]
GROUP_CSS = "\n" + "\n".join(f".g-{k} {{ --band: {g.colour}; }}" for k, g in GROUPS.items()) + "\n"
esc = html.escape


# ---------- formatting ----------

def fmt_value(cat: Category, v: float) -> tuple[str, str]:
    if cat.unit == "deg":
        return f"{abs(v):.1f}°", "N" if v >= 0 else "S"
    if cat.unit == "h":
        return (f"{v:.1f}", "hours") if v < 48 else (f"{v / 24:.1f}", "days")
    if cat.unit == "count":
        return f"{v:.0f}", "country" if round(v) == 1 else "countries"
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


def rank_parts(rank: int, of: int, scope: str) -> tuple[str, str]:
    return f"#{rank} of {of:,}", "" if scope == "all" else GROUPS[scope].label


def rank_text(rank: int, of: int, scope: str) -> str:
    return " ".join(filter(None, rank_parts(rank, of, scope)))


def lead_scope(ranks: dict) -> str:
    return min((s for s in SCOPE_ORDER if s in ranks), key=lambda s: ranks[s])


def card_rank(rec: dict) -> tuple[str, int, str, str]:
    scope = lead_scope(rec["rank"])
    rank = rec["rank"][scope]
    return scope, rank, *rank_parts(rank, rec["competitors"][scope], scope)


def display_em(text: str) -> float:
    return sum(0.26 if c in "1Iijl.,:;'()| " else 0.76 if c in "MWmw_" else 0.5 for c in text)


def title_html(text: str) -> str:
    return re.sub(r"(\w+(?:-\w+)+)", r'<span class="nb">\1</span>', esc(text))


def breakable(text: str) -> str:
    return re.sub(r"([_/-])", r"\1<wbr>", esc(text))


def where(place: str, prep: str, sea_animal: bool, lat: float, lon: float) -> str:
    if place:
        return f"{prep} {place}"
    return "over open water" if sea_animal and not geo.on_land(np.array([lat]), np.array([lon]))[0] else ""


def short_citation(citation: str) -> str:
    authors = citation.split(". ")[0]
    names = [a.strip() for a in authors.split(",")]
    year = next((p for p in citation.split(". ")[1:3] if p.strip().isdigit()), "")
    lead = names[0] + (" et al." if len(names) > 1 else "")
    return f"{lead} {year}".strip()


# ---------- record model for the site ----------

def hook(cat: Category, r: dict, value: str, unit: str) -> str:
    seg = SEGMENT_BY_KEY.get(cat.key)
    sea = taxa.taxon(r["taxon"]).lives_at_sea
    fields = {
        "value": value, "unit": unit, "duration": fmt_duration(r["t1"] - r["t0"]),
        "start_place": r["place0"] or "open water", "end_place": r["place1"] or "open water",
        "start_where": where(r["place0"], "over", sea, r["lat0"], r["lon0"]),
        "end_where": where(r["place1"], "in", sea, r["lat1"], r["lon1"]),
        "segment": seg.title if seg else "", "agl": f"{r.get('agl_m') or 0:,.0f}",
        "countries": "",
    }
    text = cat.hook
    for key in ("start_where", "end_where"):
        if not fields[key]:
            text = text.replace(", {%s}" % key, "")
    if not (r["place0"] and r["place1"]) or r["place0"] == r["place1"]:
        text = text.replace(", from {start_place} to {end_place}", "")
    return text.format(**fields)


def animal_title(r: dict) -> str:
    if r["name"]:
        return r["name"]
    ind, words = str(r["ind"]), r["common"].split()
    for k in range(len(words), 0, -1):
        m = re.fullmatch(re.escape(" ".join(words[-k:])) + r"[\s_-]+(.+)", ind, re.I)
        if m:
            return f"{r['common']} {m.group(1)}"
    return f"{r['common']} {ind}"


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


def _lines(v: C.MapView, g: dict) -> tuple[str, str, np.ndarray, np.ndarray, int]:
    clon, clat = g["ctx"]
    elon, elat = g["eff"]
    cx, cy = v.xy(clon, clat)
    ex, ey = v.xy(elon, elat)
    ctx = " ".join(f"{a:.1f},{b:.1f}" for a, b in zip(cx, cy))
    eff = " ".join(f"{a:.1f},{b:.1f}" for a, b in zip(ex, ey))
    return ctx, eff, ex, ey, len(cx) if len(cx) != len(ex) else 0


def map_base(v: C.MapView, w: int, h: int, cls: str) -> str:
    return (f'<svg class="{cls}" viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid slice" aria-hidden="true">'
            f'<rect width="{w}" height="{h}" class="sea"/><path class="land" d="{C.land_svg(v, tolerance_px=1.0)}"/>'
            f'<path class="grat" d="{C.graticule_svg(v)}"/><path class="bord" d="{C.borders_svg(v)}"/>')


def map_svg(g: dict, v: C.MapView, coarse: bool) -> str:
    ctx, eff, ex, ey, n_ctx = _lines(v, g)
    parts = [map_base(v, MAP_W, MAP_H, "map")]
    if n_ctx > 1:
        parts.append(f'<polyline class="ctx" points="{ctx}"/>')
    parts.append(f'<polyline class="trk" points="{eff}"/>')
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


# ---------- animal images ----------

def photo_print(photo: ArtEntry | None, rel: str, alt: str) -> str:
    if not photo:
        return ""
    fs = [f for f in photo.files if f.crop == "wide"]
    srcset = ", ".join(f"{rel}art/{f.path} {f.width}w" for f in fs)
    return (f'<figure class="print"><img src="{rel}art/{fs[0].path}" srcset="{srcset}" sizes="(max-width: 860px) 86vw, 320px" '
            f'width="{fs[0].width}" height="{fs[0].height}" alt="{esc(alt)}" loading="lazy" decoding="async">'
            f'<figcaption>Photo: <a href="{esc(photo.source_url)}">{esc(photo.creator)}</a>, '
            f'<a href="{esc(photo.licence_url)}">{esc(photo.licence)}</a></figcaption></figure>')


def model(taxon: str, cls: str = "", label: str = "", **opts) -> str:
    data = "".join(f' data-{k}="{esc(str(v))}"' for k, v in opts.items())
    a11y = f'role="img" aria-label="{esc(label)}" tabindex="0"' if label else 'aria-hidden="true"'
    return f'<canvas class="{cls}" data-taxon="{esc(taxon)}"{data} {a11y}></canvas>'


def bib(a: dict, cls: str = "bib-s") -> str:
    step = sum(display_em(a["id"]) > w for w in (3.4, 5.5, 9))
    return f'<span class="{cls} g-{a["class"]}" data-fit="{step}"><i></i><b>{breakable(a["id"])}</b></span>'


def species(a: dict) -> str:
    return esc(a["common"]) if a["name"] else f'<i>{esc(a["taxon"])}</i>'


ICON_GO = ('<svg class="go" viewBox="0 0 14 22" aria-hidden="true"><path d="M2 2l9 9-9 9" fill="none" stroke="currentColor" '
           'stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def lane(rec: dict, rel: str, no: int, sub: str) -> str:
    a, d = rec["animal"], rec["display"]
    return (f'<li><a class="lane" href="{rel}r/{rec["id"]}/"><span class="lane-no">{no}</span>'
            f'<span class="runner">{model(a["taxon"], lane=1, phase=round(no * 0.37 % 1, 2))}{bib(a)}</span>'
            f'<span class="who"><strong>{esc(a["name"] or a["common"])}</strong><span>{sub}</span></span>'
            f'<span class="mark"><b>{esc(d["value"])}</b><small>{esc(d["unit"])}</small></span>{ICON_GO}</a></li>')


def results_table(rows: list[tuple[int, dict]], rel: str, here: str = "") -> str:
    body = "".join(
        f'<tr{" class=\"here\"" if rec["id"] == here else ""}><td class="place">{i}</td><td>{bib(rec["animal"], "chip")}</td>'
        f'<td class="ath"><a href="{rel}r/{rec["id"]}/"><b>{esc(rec["animal"]["name"] or rec["animal"]["common"])}</b></a>'
        f'<span>{species(rec["animal"])}</span><span class="date-s">{esc(rec["when"])}</span></td>'
        f'<td class="num">{esc(rec["display"]["value"])} <small>{esc(rec["display"]["unit"])}</small></td>'
        f'<td class="date">{esc(rec["when"])}</td></tr>' for i, rec in rows)
    return ('<table><thead><tr><th>Place</th><th>Bib</th><th>Athlete</th><th class="num">Mark</th><th class="date">Date</th></tr>'
            f'</thead><tbody>{body}</tbody></table>')


# ---------- page shell ----------

def head(rel: str, models: bool = False, motion: bool = False) -> str:
    out = f'<link rel="stylesheet" href="{rel}static/style.css">'
    if models:
        out += "".join(f'<script src="{rel}static/{f}" defer></script>' for f in ("zoo.js", "zoo-species.js", "models.js"))
    if motion:
        out += f'<script src="{rel}static/motion.js" defer></script>'
    return out


def nav(rel: str) -> str:
    return f'<nav class="nav" aria-label="Site"><a href="{rel}#boards">Boards</a><a href="{rel}about/">Method</a></nav>'


def top_bar(rel: str) -> str:
    return f'<header class="top"><a class="brand" href="{rel}">{SITE_NAME}</a>{nav(rel)}</header>'


def page(rel: str, title: str, body: str, meta: str = "", cls: str = "", models: bool = False, motion: bool = False,
         top: bool = True) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
{head(rel, models, motion)}
<link rel="icon" href="{rel}static/icon.svg" type="image/svg+xml">
{meta}
</head><body class="{cls}">
{top_bar(rel) if top else ""}<main>{body}</main>
<footer class="foot"><p>Data: <a href="https://datarepository.movebank.org">Movebank Data Repository</a>, CC0 packages only.
Maps: Natural Earth. Terrain: NOAA ETOPO1. Names and Red List status: GBIF. Photos: Wikimedia Commons.</p>
<p><a href="{rel}about/#verify">How to check a record</a> · <a href="{rel}about/#errors">Report an error</a> · <a href="{rel}credits/">Image credits</a></p></footer>
</body></html>"""


def og_image(base_url: str, rid: str) -> str:
    return f"{base_url}/r/{rid}/{OG_FORMAT.file}"


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


def poster(rec: dict) -> str:
    a, d = rec["animal"], rec["display"]
    _, rank, place, group = card_rank(rec)
    width = display_em(d["value"]) + 0.45 * display_em(d["unit"]) + 0.07
    return f"""<article class="tcard{' two' if rank > 9 else ''}" aria-label="{esc(rec['share_title'])}">
<div class="ln ln-a"></div><div class="ln ln-b"></div><div class="start"></div>
<p class="brand">{SITE_NAME}</p>
<p class="who"><strong>{esc(a['name'] or a['common'])}</strong>{f"<span>{esc(a['common'])}</span>" if a['name'] else ""}</p>
<p class="no">{rank}</p>
{model(a['taxon'], "animal", phase=CARD_PHASE, pad=1)}{bib(a)}
<div class="big"><p class="board">{esc(rec['category_title'])}</p>
<p class="n" style="--w:{width:.2f}">{esc(d['value'])}<small>{esc(d['unit'])}</small></p></div>
<div class="base"><div><p class="hook">{esc(rec['hook'])}</p>
<p class="src">Data: {esc(rec['dataset']['short'])} · Movebank · {esc(rec['dataset']['licence_short'])}</p></div>
<p class="rank"><b>{esc(place)}</b>{f"<span>{esc(group)}</span>" if group else ""}</p></div>
<div class="fin"></div></article>"""


# ---------- build ----------

def video_records(boards: dict) -> set[str]:
    """The number one of every all-species board gets a replay video."""
    return {sc["all"][0] for sc in boards.values() if "all" in sc}


LANE_RACES = {"biggest-day", "biggest-week", "fastest-hour", "sea-crossing", "lifetime"}


def race_kind(key: str) -> str | None:
    if key in SEGMENT_BY_KEY:
        return "map"
    return "lanes" if key in LANE_RACES else None


def race_videos(boards: dict) -> set[tuple[str, str]]:
    return {(k, "all") for k in boards if race_kind(k) and "all" in boards[k]}


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
    board_taxa = {r["taxon"] for r in rows.values()}
    no_model = sorted(board_taxa - zoo.model_taxa(STATIC))
    if no_model:
        raise SystemExit(f"{zoo.SPECIES_JS}: no 3D model for {no_model} (add a def(...) entry for each)")
    photos = {t: e for t, e in art.load().items() if t in board_taxa}

    out = config.SITE
    if out.exists():
        shutil.rmtree(out)
    (out / "static").mkdir(parents=True)
    shutil.copytree(STATIC, out / "static", dirs_exist_ok=True)
    with (out / "static" / "style.css").open("a") as f:
        f.write(GROUP_CSS)

    pk = packages()
    recs: dict[str, dict] = {}
    by_pkg: dict[int, list[dict]] = {}
    for r in rows.values():
        by_pkg.setdefault(r["pkg"], []).append(r)
    for pkg, rs in sorted(by_pkg.items()):
        tracks = {(t.animal.ind, t.sensor): t for t in load_tracks(pkg)}
        for r in rs:
            recs[r["id"]] = _record(r, tracks[(r["ind"], r["sensor"])], boards[r["category"]], competitors, pk[pkg])
            recs[r["id"]]["_photo"] = photos.get(r["taxon"])
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
        board = [recs[i] for i in boards[rec["category"]][lead_scope(rec["rank"])]]
        (d / "index.html").write_text(_record_page(rec, others, board, base_url, rid in video_ids))

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
    _copy_art(out, photos)
    (out / "credits").mkdir()
    (out / "credits" / "index.html").write_text(_credits_page(photos))
    (out / "about").mkdir()
    (out / "about" / "index.html").write_text(_about_page(boards))
    (out / "index.html").write_text(_home_page(boards, recs, base_url))
    (out / "boards.json").write_text(json.dumps(
        {k: {s: [{"rank": i + 1, "id": rid, "value": recs[rid]["value"], "unit": recs[rid]["unit"]}
                 for i, rid in enumerate(ids)] for s, ids in sc.items()} for k, sc in boards.items()}, indent=1))
    n_png = 0
    if png:
        from . import share
        n_png = share.render(out, recs)
    from .video import restore_cached
    restore_cached(out)
    return {"records": len(recs), "boards": sum(len(s) for s in boards.values()), "pngs": n_png}


def _copy_art(out: Path, photos: dict[str, ArtEntry]) -> None:
    (out / "art" / "photo").mkdir(parents=True)
    for e in photos.values():
        for f in e.files:
            shutil.copyfile(config.ART / f.path, out / "art" / f.path)
    (out / "art" / "manifest.json").write_text(json.dumps(
        [asdict(photos[t]) for t in sorted(photos)], indent=1, ensure_ascii=False))


def _record(r: dict, tr: Track, cat_boards: dict, competitors: dict, p) -> dict:
    cat = BY_KEY[r["category"]]
    i0, i1 = int(np.searchsorted(tr.t, r["t0"])), int(np.searchsorted(tr.t, r["t1"]))
    sens = C.sensitivity(tr, i0, i1)
    if r["category"] == "round-trip":
        sens = sens or "the start of a round trip is a likely nest, roost or colony"
    coarse = sens is not None
    value, unit = fmt_value(cat, r["value"])
    ranks = _scope_rank(cat_boards, r["id"])
    scope = lead_scope(ranks)
    chip_scope = "" if scope == "all" else f" {GROUPS[scope].label}"
    ev = json.loads(r["evidence"])
    ev_public = {k: x for k, x in ev.items() if not k.endswith(("_lat", "_lon")) and k != "far_index"}
    g = _geometry(r, tr, coarse)
    point = cat.key in POINT_LIKE
    allx, ally = np.r_[g["ctx"][0], g["eff"][0]], np.r_[g["ctx"][1], g["eff"][1]]
    v_map = C.frame(allx, ally, MAP_W, MAP_H, min_span_deg=16.0 if point else MIN_SPAN_COARSE if coarse else MIN_SPAN)
    tf = replay.track_file(r["id"], cat.key, cat.unit, r["value"], tr, r["t0"], r["t1"], v_map, g["wrap"], coarse)
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
    rec["chip"] = f"#{ranks[scope]}{chip_scope} · {cat.title}"
    rec["when"] = day(r["t0"]) if r["t1"] - r["t0"] < 86400 * 2 else f"{day(r['t0'])} to {day(r['t1'])}"
    if cat.key in WHOLE_TRACK:
        rec["when"] = f"{datetime.fromtimestamp(r['t0'], timezone.utc).year} to {datetime.fromtimestamp(r['t1'], timezone.utc).year}"
    rec["share_title"] = f"{cat.title}: {value} {unit}. {title}" + (f", {r['common']}" if r["name"] else "")
    rec["_map"] = map_svg(g, v_map, coarse)
    rec["_track"] = tf
    rec["_profile"], rec["_profile_label"] = prof, prof_label
    rec["_animal_key"] = r["animal"]
    return rec


def _stat(label: str, value: str) -> str:
    return f'<div class="stat"><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>'


ICON_PLAY = '<svg viewBox="0 0 20 20" aria-hidden="true"><path class="i-play" d="M6 4l10 6-10 6z"/><path class="i-pause" d="M5 4h3.5v12H5zM11.5 4H15v12h-3.5z"/></svg>'
ICON_RESTART = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4a6 6 0 1 1-5.6 3.9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3 3v5h5z"/></svg>'
PIN = ('<svg class="pin {}" viewBox="0 0 46 18" aria-hidden="true"><g fill="none" stroke-linecap="round">'
       '<path d="M7 6H37M7 12.5H13M31 12.5H37" stroke="#141210" stroke-width="3.6"/>'
       '<circle cx="6" cy="9.25" r="3.4" stroke="#141210" stroke-width="3.4"/>'
       '<path d="M7 6H37M7 12.5H13M31 12.5H37" stroke="#dedad0" stroke-width="1.8"/>'
       '<circle cx="6" cy="9.25" r="3.4" stroke="#dedad0" stroke-width="1.6"/></g>'
       '<rect x="34" y="2.5" width="10.5" height="13.5" rx="4.2" fill="#dedad0" stroke="#141210" stroke-width="1.2"/>'
       '<circle cx="14" cy="12.5" r="1.5" fill="#141210"/><circle cx="30" cy="12.5" r="1.5" fill="#141210"/></svg>')


def controls(what: str) -> str:
    return f"""<div class="rctl"><button type="button" class="b-play" aria-label="Play the {what}">{ICON_PLAY}</button>
<button type="button" class="b-restart" aria-label="Restart the {what}">{ICON_RESTART}</button>
<span class="spd" role="group" aria-label="Speed of the {what}"><button type="button" data-s="0.5" aria-pressed="false">0.5×</button><button type="button" data-s="1" aria-pressed="true">1×</button><button type="button" data-s="2" aria-pressed="false">2×</button></span></div>"""


def video_parts(rec: dict) -> str:
    a = rec["animal"]
    who = a["name"] or a["common"]
    return (f'<template class="vid"><p class="vbrand">{SITE_NAME}</p><div class="vband">{bib(a)}'
            f'<p class="vwho" style="--w:{display_em(who):.2f}"><strong>{esc(who)}</strong><span>{esc(rec["category_title"])}</span></p>'
            f'</div>{poster(rec)}</template>')


def replay_block(rec: dict, video: bool) -> str:
    tf = rec["_track"]
    cells = "".join(f'<div class="rc"><dt class="rl">{esc(label)}</dt><dd><b data-k="{k}">{esc(v)}</b></dd></div>'
                    for k, label, v in readout(tf))
    sun = '<div class="rc rsun" hidden><dt class="rl">Sun</dt><dd><b data-k="sun">Day</b></dd></div>' if tf["night"] else ""
    return f"""<section class="brochure paper replay" data-track="track.json" data-taxon="{esc(rec['animal']['taxon'])}" data-final="{esc(rec['display']['value'])}">
<figure class="stage">{rec['_map']}</figure>
<div class="course"><h2>Course map</h2>
<p class="num">{esc(rec['display']['value'])}<span class="u">{esc(rec['display']['unit'])}</span></p>
<dl class="ro">{cells}{sun}</dl>
{controls("replay")}</div>
<div class="prof"><h3>{esc(rec["_profile_label"])}</h3><div class="scrub">{rec["_profile"]}<span class="cur" hidden></span></div>
<div class="axis"><span>{esc(day(tf["t_start"] + tf["t"][0]))}</span><span>{esc(day(tf["t_start"] + tf["t"][-1]))}</span></div></div>
{video_parts(rec) if video else ""}</section>"""


def big_bib(rec: dict) -> str:
    a, d = rec["animal"], rec["display"]
    whole = display_em(a["id"])
    lines = 1 if whole <= 6 else 2 if whole <= 11 else 3
    words = re.split(r"\s+|(?<=[_/-])", a["id"]) if lines > 1 else []
    fit = max([whole / lines, *map(display_em, words)])
    common = f"{esc(a['common'])} · " if a["name"] else ""
    places = [x for x in dict.fromkeys(rec["places"].values()) if x]
    strip = [] if rec["category"] in POINT_LIKE or rec["unit"] == "h" else [fmt_duration(rec["duration_h"] * 3600)]
    if places:
        strip.append(" to ".join(places))
    return f"""<article class="bib paper g-{a['class']}" style="--w:{fit:.2f}">
<p class="bib-band">{title_html(rec['category_title'])}</p>
<p class="bib-tag">{breakable(a['id'])}</p>
<p class="bib-who"><strong>{esc(a['name'] or a['common'])}</strong><span>{common}<i>{esc(a['taxon'])}</i></span>
{PIN.format("bl")}{PIN.format("br")}</p>
<p class="bib-strip"><b>{esc(d['value'])} {esc(d['unit'])}</b>{"".join(f"<span>{esc(x)}</span>" for x in strip)}</p>
{PIN.format("tl")}{PIN.format("tr")}</article>"""


def share_block(rec: dict, url: str, video: bool) -> str:
    cards = "".join(
        f'<li><a href="{f.file}"><img src="{f.file}" width="{CARD_W}" height="{CARD_H}" loading="lazy" decoding="async" '
        f'alt="{esc(f.label)}: {esc(rec["share_title"])}"></a>'
        f'<p><b>{esc(f.label)}</b><a href="{f.file}" download aria-label="Download the image {esc(f.label)} (PNG)">Download PNG</a></p></li>'
        for f in SHARE_FORMATS)
    post = "https://twitter.com/intent/tweet?" + urlencode({"text": rec["share_title"], "url": url})
    video_link = '<a href="replay.mp4" download>Download the replay video (MP4)</a>' if video else ""
    return f"""<section class="share"><h2>Share this record</h2>
<ul class="share-cards">{cards}</ul>
<p class="share-links"><a href="{esc(post)}">Post on X</a>{video_link}</p></section>"""


def _record_page(rec: dict, others: list[dict], board: list[dict], base_url: str, video: bool) -> str:
    rel = "../../"
    url = f"{base_url}/r/{rec['id']}/"
    ev, a, cat = rec["evidence"], rec["animal"], rec["category"]

    def board_href(s: str) -> str:
        return f'{rel}b/{cat}/{"" if s == "all" else s + "/"}'
    scope_links = "".join(
        f'<a href="{board_href(s)}">{esc(rank_text(n, rec["competitors"][s], s))}{" animals" if s == "all" else ""}</a>'
        for s, n in rec["rank"].items())
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
    if a["iucn"] in ("VU", "EN", "CR", "NT"):
        stats.append(_stat("IUCN Red List", a["iucn"]))
    countries = f'<p class="countries">{esc(", ".join(rec["countries"]))}</p>' if rec.get("countries") else ""
    other_html = ""
    if others:
        items = "".join(f'<li><a href="{rel}r/{o["id"]}/"><b>{esc(o["display"]["value"])} {esc(o["display"]["unit"])}</b>'
                        f'<span>{esc(o["category_title"])}</span></a></li>' for o in others)
        other_html = f'<h3>Other records of {esc(a["title"])}</h3><ul class="others">{items}</ul>'
    coarse = ""
    if rec["geometry"]["reason"]:
        coarse = f'<p class="note">The map shows the track at {C.COARSE_DEG}° resolution with no exact endpoints: {esc(rec["geometry"]["reason"])}.</p>'
    scope = lead_scope(rec["rank"])
    rank = rec["rank"][scope]
    near = list(enumerate(board, 1))[max(0, rank - 3):rank + 2]
    places = f"Places {near[0][0]} to {near[-1][0]}" if len(near) > 1 else f"Place {rank}"
    board_title = rec["category_title"] + ("" if scope == "all" else f" · {BOARD_TITLES[scope]}")
    recipe = json.dumps(rec["reproduce"], indent=1)
    body = f"""<div class="wrap">
<header class="rec-head"><h1>{esc(rec["hook"])}</h1>
<p class="places">{scope_links}</p>
{countries}</header>
<div class="rec-row">{big_bib(rec)}
{model(a["taxon"], "hero", f"3D model: {a['common']}. Drag it or use the arrow keys to turn it.", drag=1, shadow="rgba(8,24,12,.34)")}</div>
{replay_block(rec, video)}
<section class="sheet paper"><header><h2>{title_html(board_title)}</h2>
<p><a href="{board_href(scope)}">{places} of {rec["competitors"][scope]:,}</a></p></header>
{results_table(near, rel, rec["id"])}</section>
<section class="sheet paper"><header><h2>The record</h2><p>{esc(rec["when"])}</p></header>
<div class="cols"><div><p class="rule"><b>{esc(rec["category_title"])}:</b> {esc(rec["rule"])}</p>
<dl class="stats">{"".join(stats)}</dl>
<p class="state"><span class="dot"></span>Credible. {esc(rec["state_reason"])}</p>
{coarse}</div>{photo_print(rec["_photo"], rel, a["common"])}</div>
{other_html}</section>
<section class="sheet paper"><header><h2>Data</h2><p>{esc(rec["dataset"]["licence"])}</p></header>
<p class="cite">{esc(rec["dataset"]["citation"])}</p>
<p class="cite"><a href="{esc(rec["dataset"]["url"])}">{esc(rec["dataset"]["doi"])}</a></p>
<h3 id="reproduce">Check this record</h3>
<p>Any person can recompute this record from the public data package. The command downloads the package again, applies the same cleaning and prints MATCH or MISMATCH.</p>
<pre class="cmd"><code>uv run wildprs verify {esc(rec["id"])} --site {esc(base_url)}</code></pre>
<p>The code is at <a href="{config.SOURCE_URL}">{config.SOURCE_URL.removeprefix("https://")}</a>.</p>
<p>Recipe (<a href="record.json">record.json</a>):</p>
<pre><code>{esc(recipe)}</code></pre></section>
{share_block(rec, url, video)}
</div>"""
    desc = f"{rec['hook']} {a['title']}, {a['common']}. Data: {rec['dataset']['short']}, Movebank."
    meta = social_meta(f"{rec['share_title']}", desc, url, og_image(base_url, rec["id"]))
    return page(rel, f"{rec['share_title']} · {SITE_NAME}", body, meta, "page-record", models=True, motion=True)


RACE_W, RACE_H = 1200, 675
LANES_PLOT = (150, 124, 1104, 640)


def race_subtitle(cat: Category, n: int) -> str:
    seg = SEGMENT_BY_KEY.get(cat.key)
    if seg:
        return f"Top {n} crossings, {seg.span.split(',')[0]}, same start, true speeds"
    if cat.key == "fastest-hour":
        return f"Top {n}, same start, one hour at the average speed of each record"
    return f"Top {n}, same start, true speeds"


def lanes_svg(runners: list[dict], dmax: float) -> str:
    x0, top, x1, bottom = LANES_PLOT
    h = (bottom - top) / LANES
    lines = "".join(f"M0,{top + i * h:.1f}H{RACE_W}" for i in range(len(runners) + 1))
    runs = "".join(f"M{x0},{top + (i + .5) * h:.1f}H{x0 + rn['d'][-1] / dmax * (x1 - x0):.1f}" for i, rn in enumerate(runners))
    y1 = top + len(runners) * h
    return (f'<svg class="race-lanes" viewBox="0 0 {RACE_W} {RACE_H}" preserveAspectRatio="none" aria-hidden="true">'
            f'<path class="ll" d="{lines}M{x0},{top}V{y1:.1f}M{x1},{top}V{y1:.1f}"/><path class="run" d="{runs}"/></svg>')


def race(cat: Category, recs: list[dict]) -> tuple[str, dict] | None:
    """Markup and race.json of a board race. The markup shows the finished race without JS."""
    kind = race_kind(cat.key)
    if kind is None:
        return None
    v = None
    if kind == "map":
        lon = np.concatenate([np.asarray(r["_track"]["lon"])[r["_track"]["eff"][0]:r["_track"]["eff"][1] + 1] for r in recs])
        lat = np.concatenate([np.asarray(r["_track"]["lat"])[r["_track"]["eff"][0]:r["_track"]["eff"][1] + 1] for r in recs])
        v = C.frame(lon, lat, RACE_W, RACE_H, min_span_deg=1.0)
    else:
        recs = recs[:LANES]
    runners = []
    for i, r in enumerate(recs, 1):
        a = r["animal"]
        runners.append({"id": r["id"], "rank": i, "name": a["name"] or a["common"], "bib": a["id"], "taxon": a["taxon"],
                        "band": GROUPS[a["class"]].colour,
                        "display": f'{r["display"]["value"]} {r["display"]["unit"]}'} | replay.race_runner(r["_track"], v))
    data = {"v": 2, "kind": kind, "category": cat.key, "title": cat.title, "unit": cat.unit,
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
        gl = "".join(f'<path class="gate{c}" d="M{g[0]},{g[1]}L{g[2]},{g[3]}"/>' for g in gates for c in ("", " dash"))
        art = map_base(v, RACE_W, RACE_H, "map race-map") + gl + routes + "</svg>"
    else:
        data["dmax"] = max(rn["d"][-1] for rn in runners) or 1
        art = lanes_svg(runners, data["dmax"])
    head = (f'<p class="race-t">{esc(cat.title)}</p>'
            f'<p class="race-clock" aria-hidden="true"></p>'
            f'<p class="race-brand">{SITE_NAME}</p>')
    html_ = f"""<section class="race" data-race="race.json" data-kind="{kind}" aria-label="Race of the top {len(recs)}">
<p class="race-k">{race_subtitle(cat, len(recs))}</p>
<div class="race-stage">{art}{head}</div>
{controls("race")}
</section>"""
    return html_, data


def _scope_tabs(key: str, scope: str, scopes: dict, rel: str) -> str:
    tabs = []
    for s in SCOPE_ORDER:
        if s not in scopes:
            continue
        href = f"{rel}b/{key}/" + ("" if s == "all" else f"{s}/")
        on = ' aria-current="page"' if s == scope else ""
        tabs.append(f'<a class="tab chip g-{s}" href="{href}"{on}><i></i><b>{esc(BOARD_TITLES[s])}</b></a>')
    return f'<nav class="tabs" aria-label="Animal class">{"".join(tabs)}</nav>'


def _board_page(cat: Category, scope: str, recs: list[dict], scopes: dict, comp: dict, base_url: str,
                race_html: str = "", video: bool = False) -> str:
    rel = "../../" if scope == "all" else "../../../"
    lanes = "".join(lane(rec, rel, i, f'{species(rec["animal"])} · {esc(rec["when"])}')
                    for i, rec in enumerate(recs[:LANES], 1))
    rest = list(enumerate(recs, 1))[LANES:]
    title = cat.title + ("" if scope == "all" else f" · {BOARD_TITLES[scope]}")
    field = "animals" if scope == "all" else GROUPS[scope].label
    body = f"""<section class="track">{top_bar(rel)}
<header class="board-head"><h1>{title_html(title)}</h1>
<p class="rule">{esc(cat.rule)} {comp.get(scope, 0):,} {field} compete.</p>{_scope_tabs(cat.key, scope, scopes, rel)}</header>
<ol class="lanes">{lanes}</ol></section>
<div class="wrap"><section class="sheet paper">{f'<header><h2>Results</h2><p>Top {len(recs)} of {comp.get(scope, 0):,}</p></header>{results_table(rest, rel)}' if rest else ''}
<p class="rule">One entry per animal, at most {STUDY_CAP} per study. Only records in the state credible are shown. <a href="{rel}about/#states">How records are checked</a>.</p></section>
{race_html}{'<p class="dl"><a href="race.mp4" download>Download the race video (MP4)</a></p>' if video else ''}</div>"""
    top = recs[0]
    url = f"{base_url}/b/{cat.key}/" + ("" if scope == "all" else f"{scope}/")
    meta = social_meta(f"{title} · {SITE_NAME}", f"{cat.rule} #1: {top['animal']['title']}, {top['display']['value']} {top['display']['unit']}.",
                       url, og_image(base_url, top["id"]))
    return page(rel, f"{title} · {SITE_NAME}", body, meta, "page-board", models=True, motion=bool(race_html), top=False)


def home_lanes(boards: dict, recs: dict, n: int = LANES) -> list[str]:
    order = [k for k in BY_KEY if k in boards]
    lanes: list[str] = []
    used: set[str] = set()

    def take(scope: str) -> bool:
        seen = {recs[i]["animal"]["taxon"] for i in lanes}
        fresh = [k for k in order if scope in boards[k] and recs[boards[k][scope][0]]["animal"]["taxon"] not in seen]
        key = next((k for k in fresh if k not in used), fresh[0] if fresh else None)
        if key is None:
            return False
        used.add(key)
        lanes.append(boards[key][scope][0])
        return True

    take("all")
    for s in GROUPS:
        if not any(recs[i]["rank"].get(s) == 1 for i in lanes):
            take(s)
    while len(lanes) < n and take("all"):
        pass
    return lanes[:n]


def _home_page(boards: dict, recs: dict, base_url: str) -> str:
    lanes = []
    for i, rid in enumerate(home_lanes(boards, recs), 1):
        rec = recs[rid]
        s = next(s for s in SCOPE_ORDER if rec["rank"].get(s) == 1)
        common = f'{esc(rec["animal"]["common"])} · ' if rec["animal"]["name"] else ""
        lanes.append(lane(rec, "", i, f'{common}{esc(rec["category_title"])} · {esc(rank_text(1, rec["competitors"][s], s))}'))
    rows = []
    for key, cat in BY_KEY.items():
        if "all" not in boards.get(key, {}):
            continue
        top = recs[boards[key]["all"][0]]
        a = top["animal"]
        who = f'{esc(a["name"] or a["common"])}{", " + esc(a["common"]) if a["name"] else ""}'
        rows.append(f'<tr><td class="ath"><a href="b/{key}/"><b>{esc(cat.title)}</b></a><span class="wide">{esc(cat.rule)}</span>'
                    f'<span class="date-s">{who}</span></td><td>{bib(a, "chip")}</td><td class="ath date">{who}</td>'
                    f'<td class="num">{esc(top["display"]["value"])} <small>{esc(top["display"]["unit"])}</small></td></tr>')
    cov = _coverage()
    body = f"""<section class="track fill"><header class="head"><h1>{SITE_NAME}</h1>
<div><p class="tagline">{esc(HEADLINE)}</p>
<p class="sub">{len(recs):,} records from {cov['packages']:,} open GPS and Argos data packages, {cov['animals']:,} animals. Every record can be checked.</p></div>
{nav("")}</header>
<ol class="lanes">{"".join(lanes)}</ol></section>
<div class="wrap"><section id="boards" class="sheet paper"><header><h2>All boards</h2><p>{len(rows)} boards</p></header>
<table><thead><tr><th>Board</th><th>Bib</th><th class="date">Leader</th><th class="num">Mark</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></section></div>"""
    first = recs[boards[next(iter(boards))]["all"][0]]
    meta = social_meta(SITE_NAME, TAGLINE, base_url + "/", og_image(base_url, first["id"]))
    return page("", f"{SITE_NAME}: personal records of wild animals", body, meta, "page-home", models=True, top=False)


def _credits_page(photos: dict[str, ArtEntry]) -> str:
    rel = "../"
    names = taxa.curated_names()
    rows = []
    for t in sorted(photos, key=lambda t: names.get(t, t)):
        e = photos[t]
        f = e.files[0]
        shows = "" if e.shows == t else f' (shows <i>{esc(e.shows)}</i>)'
        files = " ".join(x.path for x in e.files)
        rows.append(f'<li data-files="{esc(files)}"><img class="cr-prev" src="{rel}art/{f.path}" width="{f.width}" '
                    f'height="{f.height}" alt="" loading="lazy">'
                    f'<span class="cr-what"><b>{esc(names.get(t, t))}</b> <i>{esc(t)}</i>{shows}</span>'
                    f'<span class="cr-by">Photo by {esc(e.creator)}. <a href="{esc(e.licence_url)}">{esc(e.licence)}</a>. '
                    f'<a href="{esc(e.source_url)}">Source</a>. {esc(e.change)}.</span></li>')
    body = f"""<article class="wrap doc"><h1>Image credits</h1>
<div class="sheet paper prose">
<p>The photos come from <a href="https://commons.wikimedia.org">Wikimedia Commons</a>.
Each photo has a licence that permits reuse with attribution or has no conditions: CC0, public domain, CC BY or CC BY-SA.
The site shows the photos cropped and resized.</p>
<p>The 3D animal models are part of the code of this site, and their proportions and colours are approximate.</p>
<ul class="cr">{"".join(rows)}</ul></div></article>"""
    return page(rel, f"Image credits · {SITE_NAME}", body, "", "page-credits")


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
    body = f"""<article class="wrap doc"><h1>Method</h1>
<div class="sheet paper prose">
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
</div></article>"""
    return page(rel, f"Method · {SITE_NAME}", body, "", "page-about")

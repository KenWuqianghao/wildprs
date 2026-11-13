"""check: walk the built site and prove that pages, board data, cards and meta tags agree."""
from __future__ import annotations

import json
import re
import struct
from html import unescape
from pathlib import Path
from urllib.parse import urlparse


def _png_size(p: Path) -> tuple[int, int]:
    head = p.read_bytes()[:24]
    return struct.unpack(">II", head[16:24])


def _meta(html: str, attr: str, name: str) -> str | None:
    m = re.search(rf'<meta {attr}="{re.escape(name)}" content="([^"]*)"', html)
    return unescape(m.group(1)) if m else None


TRACK_MAX_BYTES = 40_000
TRACK_MAX_POINTS = 400
VIDEO_MAX_BYTES = 15_000_000


def track_errors(tf: dict, sensitive: bool) -> list[str]:
    """A track file must be small and consistent. For a sensitive record every position is on the
    0.1 degree grid, every time on the 15-minute grid, and km and kmh are a pure function of those."""
    import numpy as np

    from .replay import COARSE_TQ_S, derive
    errs = []
    n = len(tf["t"])
    cols = [c for c in ("t", "lon", "lat", "km", "kmh", "m", "h") if c in tf]
    if any(len(tf[c]) != n for c in cols) or not 2 <= n <= TRACK_MAX_POINTS:
        return [f"track columns differ in length or n={n} out of range"]
    t = np.asarray(tf["t"], float)
    if (np.diff(t) <= 0).any():
        errs.append("track times not strictly increasing")
    if not (0 <= tf["eff"][0] <= tf["eff"][1] < n):
        errs.append(f"track eff {tf['eff']} out of range")
    if not sensitive:
        return errs
    grid = np.asarray(tf["lon"] + tf["lat"], float) / 0.1
    if tf["coarse"] != 0.1 or np.abs(grid - np.round(grid)).max() > 1e-6:
        errs.append("sensitive track has positions off the 0.1 degree grid")
    tq = np.r_[t + tf["t_start"], tf["t_zero"] + tf["t_start"]] / COARSE_TQ_S
    if np.abs(tq - np.round(tq)).max() > 1e-9:
        errs.append("sensitive track has times off the 15-minute grid")
    km, kmh = derive(t + tf["t_start"], tf["lon"], tf["lat"], tf["brk"], tf["km_scale"])
    if np.abs(km - np.asarray(tf["km"])).max() > 0.11 or np.abs(kmh - np.asarray(tf["kmh"])).max() > 1.01:
        errs.append("sensitive track has km or kmh that do not follow from its coarse points")
    return errs


def check(site: Path) -> list[str]:
    errors: list[str] = []
    boards = json.loads((site / "boards.json").read_text())
    seen = set()
    for cat, scopes in boards.items():
        for scope, entries in scopes.items():
            page = site / "b" / cat / ("" if scope == "all" else scope) / "index.html"
            html = page.read_text()
            rj = page.parent / "race.json"
            if 'data-race="race.json"' in html:
                race = json.loads(rj.read_text()) if rj.exists() else {"runners": []}
                if [r["id"] for r in race["runners"]] != [e["id"] for e in entries]:
                    errors.append(f"{page}: race.json runners differ from the board")
            for e in entries:
                rec = json.loads((site / "r" / e["id"] / "record.json").read_text())
                if rec["value"] != e["value"] or rec["category"] != cat:
                    errors.append(f"{page}: {e['id']} value {e['value']} != record {rec['value']}")
                if rec["rank"].get(scope) != e["rank"]:
                    errors.append(f"{page}: {e['id']} rank {e['rank']} != record rank {rec['rank'].get(scope)}")
                if f'r/{e["id"]}/' not in html or rec["display"]["value"] not in unescape(html):
                    errors.append(f"{page}: missing link or value for {e['id']}")
                if rec["state"] != "credible":
                    errors.append(f"{e['id']}: state {rec['state']} on a public board")
                seen.add(e["id"])
    for d in sorted((site / "r").iterdir()):
        rid = d.name
        rec = json.loads((d / "record.json").read_text())
        html = (d / "index.html").read_text()
        if rid not in seen:
            errors.append(f"{rid}: record page with no board entry")
        if rec["display"]["value"] not in unescape(html):
            errors.append(f"{rid}: page does not show {rec['display']['value']}")
        png = d / "card.png"
        if not png.exists() or _png_size(png) != (1200, 675):
            errors.append(f"{rid}: card.png missing or not 1200x675")
        og, tw = _meta(html, "property", "og:image"), _meta(html, "name", "twitter:image")
        if not og or og != tw or not og.endswith(f"/r/{rid}/card.png"):
            errors.append(f"{rid}: og:image / twitter:image wrong: {og} {tw}")
        if _meta(html, "name", "twitter:card") != "summary_large_image":
            errors.append(f"{rid}: twitter:card missing")
        for k in ("og:title", "og:description", "og:url"):
            if not _meta(html, "property", k):
                errors.append(f"{rid}: {k} missing")
        if rec["geometry"]["reason"] and re.search(r'"(lat|lon)\w*"', json.dumps(rec)):
            errors.append(f"{rid}: sensitive record publishes coordinates")
        tp = d / "track.json"
        if not tp.exists() or tp.stat().st_size > TRACK_MAX_BYTES:
            errors.append(f"{rid}: track.json missing or over {TRACK_MAX_BYTES} bytes")
        else:
            errors += [f"{rid}: {e}" for e in track_errors(json.loads(tp.read_text()), bool(rec["geometry"]["reason"]))]
        mp4 = d / "replay.mp4"
        if mp4.exists() and mp4.stat().st_size > VIDEO_MAX_BYTES:
            errors.append(f"{rid}: replay.mp4 over {VIDEO_MAX_BYTES} bytes")
    for page in site.rglob("*.html"):
        for href in re.findall(r'(?:href|src)="([^"#]+)"', page.read_text()):
            if urlparse(href).scheme:
                continue
            target = (page.parent / href).resolve()
            if target.is_dir():
                target = target / "index.html"
            if not target.exists():
                errors.append(f"{page.relative_to(site)}: broken link {href}")
    return errors


def main(site: Path) -> bool:
    errors = check(site)
    n_rec = len(list((site / "r").iterdir()))
    n_board = sum(len(s) for s in json.loads((site / "boards.json").read_text()).values())
    for e in errors[:50]:
        print("ERROR", e)
    print(f"checked {n_rec} record pages, {n_board} boards: {len(errors)} errors")
    return not errors

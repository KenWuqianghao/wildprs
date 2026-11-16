from __future__ import annotations

import math
import shutil
import textwrap
from pathlib import Path

from . import site as S
from .model import SHARE_FORMATS
from .render import render_cards

esc = S.esc

ASSETS = Path(__file__).parent / "site" / "share"
BUILD_DIR = "share"


def _page(body: str, head: str, cls: str = "") -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="robots" content="noindex">\n'
            f'{head}</head><body class="{cls}">{body}</body></html>')


def _head(name: str) -> str:
    return (f'<link rel="stylesheet" href="../../{BUILD_DIR}/{name}.css">'
            + "".join(f'<script src="../../{src}" defer></script>'
                      for src in ("static/zoo.js", "static/zoo-species.js", f"{BUILD_DIR}/{name}.js")))


def _source(rec: dict) -> str:
    return f"Data: {rec['dataset']['short']} · Movebank · {rec['dataset']['licence_short']}"


def track(rec: dict) -> str:
    return _page(S.poster(rec), S.head("../../", models=True), "card-only")


def _icon(d: str) -> str:
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{d}</svg>')


GROUP_ICON = {
    "birds": _icon(
        '<path d="M2.5 14c2.6-5 6-6 9.5-2 3.5-4 6.9-3 9.5 2"/>'
        '<path d="M12 12v5"/>'),
    "land-mammals": _icon(
        '<circle cx="6.3" cy="10.4" r="1.7"/>'
        '<circle cx="10" cy="6.3" r="1.7"/>'
        '<circle cx="14.7" cy="6.3" r="1.7"/>'
        '<circle cx="18.2" cy="10.4" r="1.7"/>'
        '<path d="M7.8 17.3c0-2.6 2-4.7 4.5-4.7s4.4 2 4.4 4.6c0 1.7-1.2 2.6-2.5 2.6-.8 0-1.2-.4-1.9-.4s-1.2.4-2 '
        '.4c-1.5 0-2.5-.9-2.5-2.5z"/>'),
    "marine-animals": _icon(
        '<path d="M12 15.5c0-4.5-2.8-7.8-8-8 .4 3.6 3.2 5.4 8 5.4s7.6-1.8 8-5.4c-5.2.2-8 3.5-8 8z"/>'
        '<path d="M3 20c1.5-1.3 3-1.3 4.5 0s3 1.3 4.5 0 3-1.3 4.5 0 3 1.3 4.5 0"/>'),
    "bats": _icon(
        '<path d="M12 10l-1.5-2.5-1 2.8C7.6 8.4 5 7.4 2 7.8c.3 4.6 2.3 7.4 5.3 8.3.7-1.7 1.7-2 2.6-.9.8 1 1.3 '
        '1.7 2.1 2.4.8-.7 1.3-1.4 2.1-2.4.9-1.1 1.9-.8 2.6.9 3-.9 5-3.7 5.3-8.3-3-.4-5.6.6-7.5 2.5l-1-2.8z"/>'),
    "reptiles": _icon(
        '<ellipse cx="12" cy="13" rx="5.2" ry="6"/>'
        '<path d="M12 7V3.6M7.7 9.6L4.6 7.3M16.3 9.6l3.1-2.3M7.9 16.8L5.2 19.3M16.1 16.8l2.7 2.5"/>'),
}
BRAND_ICON = ('<svg viewBox="0 0 30 30" aria-hidden="true"><rect x="11" y="3.5" width="14" height="20" rx="2.6" transform="rotate(11 18 13.5)" '
              'fill="#f2c14e" stroke="#17130f" stroke-width="2"/><rect x="5" y="6" width="14" height="20" rx="2.6" '
              'transform="rotate(-9 12 16)" fill="#fffdf6" stroke="#17130f" stroke-width="2"/></svg>')
SEAL_STAR = " ".join(f"{60 + r * math.cos(i / 36 * math.tau):.1f},{60 + r * math.sin(i / 36 * math.tau):.1f}"
                     for i in range(36) for r in ((50 if i % 2 else 57),))


def collector(rec: dict) -> str:
    a, d = rec["animal"], rec["display"]
    scope, rank, place, group = S.card_rank(rec)
    no, of = place.split(" ", 1)
    frame = "plain" if rank > 1 else "gold" if scope == "all" else "silver"
    who = a["name"] or a["common"]
    common = f'<p class="common">{esc(a["common"])}</p>' if a["name"] else ""
    body = f"""<article class="cposter {frame}" style="--g:{S.GROUPS[a['class']].colour}" aria-label="{esc(rec['share_title'])}">
<div class="card"><div class="face"><div class="cbody">
<div class="banner"><h1 class="name" data-fit="2">{esc(who)}</h1><span class="gicon">{GROUP_ICON[a['class']]}</span></div>
<div class="artwrap"><div class="art"><svg viewBox="0 0 100 64" preserveAspectRatio="xMidYMax slice" aria-hidden="true"></svg></div>
{S.model(a['taxon'], "model")}</div>
<p class="ribbon">{esc(rec['category_title'])}</p>
<div class="plate"><p class="num" data-fit="1"><span>{esc(d['value'])}</span><span class="unit">{esc(d['unit'])}</span></p></div>
<p class="fine"><span>{esc(rec['dataset']['short'])} · {esc(rec['dataset']['licence_short'])}</span><span class="tag">{esc(a['id'])}</span></p>
</div><div class="sheen"></div></div></div>
<p class="brand">{BRAND_ICON}{S.SITE_NAME}</p>
<p class="of">{esc(of)}<span>{esc(group or "animals")}</span></p>
<svg class="seal" viewBox="0 0 120 120" aria-hidden="true"><polygon points="{SEAL_STAR}" stroke="#17130f" stroke-width="2.5" stroke-linejoin="round"/>
<circle cx="60" cy="60" r="41" fill="none" stroke="#17130f" stroke-width="1.6" stroke-dasharray="1.5 4" stroke-linecap="round"/>
<text x="60" y="{74 if rank < 10 else 72}" text-anchor="middle" font-family="Bagel Fat One" font-size="{46 if rank < 10 else 38}" fill="#17130f">{esc(no)}</text></svg>
<div class="txt"><p class="big" data-fit="1"><span>{esc(d['value'])}</span><small>{esc(d['unit'])}</small></p>
<p class="rib">{esc(rec['category_title'])}</p>
<p class="who" data-fit="2">{esc(who)}</p>{common}
<p class="hook">{esc(rec['hook'])}</p></div>
<p class="src">{esc(_source(rec))}</p></article>"""
    return _page(body, _head("collector"))


TEXT_COLS = 90
TEXT_ROWS = 40


def _lines(text: str, m: int) -> list[str]:
    def wrap(width: int) -> list[str]:
        return textwrap.wrap(text, width, break_long_words=False)
    full = TEXT_COLS // m
    return next(ls for w in range(1, full + 1) if len(ls := wrap(w)) <= len(wrap(full)))


def scoreboard(rec: dict) -> str:
    a, d = rec["animal"], rec["display"]
    _, _, place, group = S.card_rank(rec)
    board = _lines(rec["category_title"].upper(), 5)
    unit = f" {d['unit']}"
    m_value = min(14, (TEXT_COLS - 5 * len(unit)) // len(d["value"]))
    m_who, who = 4, _lines(a["name"] or a["common"], 4)
    if len(who) > 1:
        m_who, who = 3, _lines(a["name"] or a["common"], 3)
    common = _lines(a["common"], 3) if a["name"] else []
    used = 5 * len(board) + 1 + 12 + m_who * len(who) + 3 * len(common) + 1
    m_hook, hook = 3, _lines(rec["hook"], 3)
    if used + 3 * len(hook) > TEXT_ROWS:
        m_hook, hook = 2, _lines(rec["hook"], 2)
    spare = max(0, TEXT_ROWS - used - m_hook * len(hook)) // 2

    def p(cls: str, m: int, lines: list[str]) -> str:
        return f'<p class="{cls}" style="--m:{m}">{esc(chr(10).join(lines))}</p>'
    body = f"""<article class="sposter" aria-label="{esc(rec['share_title'])}">
<div class="view" data-taxon="{esc(a['taxon'])}" aria-hidden="true"></div>
<div class="txt" style="--spare:{spare}">{p("b", 4, [S.SITE_NAME])}{p("b board", 5, board)}
<p class="b num" style="--m:{m_value}"><span class="v">{esc(d['value'])}</span><span class="u">{esc(unit)}</span></p>
{p("b", m_who, who)}{p("common", 3, common) if common else ""}{p("hook", m_hook, hook)}</div>
{p("b rank", 4, [place.upper()])}{p("rank", 3, [group.upper()]) if group else ""}
{p("src", 2, [_source(rec)])}</article>"""
    return _page(body, _head("scoreboard"))


POSTERS = {"track": track, "collector": collector, "ascii": scoreboard}


def render(site: Path, recs: dict[str, dict]) -> int:
    pages = {site / "r" / rid / f.poster: POSTERS[f.key](rec) for rid, rec in recs.items() for f in SHARE_FORMATS}
    shutil.copytree(ASSETS, site / BUILD_DIR)
    try:
        for page, html in pages.items():
            page.write_text(html)
        return render_cards(site, sorted(recs))
    finally:
        shutil.rmtree(site / BUILD_DIR)
        for page in pages:
            page.unlink(missing_ok=True)

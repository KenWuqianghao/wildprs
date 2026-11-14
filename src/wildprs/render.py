"""Card PNGs (1200x675) rendered with headless Chromium from each record's poster page."""
from __future__ import annotations

import functools
import http.server
import os
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

CHROMIUM = os.environ.get("WILDPRS_CHROMIUM") or next(
    (str(p) for p in sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell"))),
    None)


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(root: Path):
    """Local static server on a free port; returns (server, base URL)."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def browser(p):
    return p.chromium.launch(executable_path=CHROMIUM) if CHROMIUM else p.chromium.launch()


# Fonts, photos and the silhouette masks (CSS mask images do not delay the load event).
READY = """Promise.all([document.fonts.ready,
  ...[...document.images].map(i => i.decode().catch(() => {})),
  ...[...document.querySelectorAll('.sil')].map(e => new Promise(ok => {
    const im = new Image(); im.onload = im.onerror = ok;
    im.src = getComputedStyle(e).getPropertyValue('--sil').trim().replace(/^url\\(["']?|["']?\\)$/g, '');
  }))])"""


def render_cards(site: Path, ids: list[str], bare_ids: list[str] = ()) -> int:
    """card.png for each record; card-bare.jpg (the card without the route) for the home page cards,
    which draw their route on top of it."""
    srv, base = serve(site)
    n = 0
    try:
        with sync_playwright() as p:
            b = browser(p)
            pg = b.new_page(viewport={"width": 1200, "height": 675}, device_scale_factor=1)
            for rid in ids:
                pg.goto(f"{base}/r/{rid}/poster.html", wait_until="load")
                pg.evaluate(READY)
                pg.screenshot(path=str(site / "r" / rid / "card.png"), clip={"x": 0, "y": 0, "width": 1200, "height": 675})
                n += 1
            for rid in bare_ids:
                pg.goto(f"{base}/r/{rid}/poster-bare.html", wait_until="load")
                pg.evaluate(READY)
                pg.screenshot(path=str(site / "r" / rid / "card-bare.jpg"), type="jpeg", quality=82,
                              clip={"x": 0, "y": 0, "width": 1200, "height": 675})
            b.close()
    finally:
        srv.shutdown()
    return n

from __future__ import annotations

import functools
import http.server
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from playwright.sync_api import sync_playwright

from .model import SHARE_FORMATS, ShareFormat

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


CARD_FREEZE_S = 0
MODELS_READY = "document.documentElement.dataset.models === 'ready'"


# Browsers that render at the same time. Each one runs in its own thread with its own Playwright driver.
WORKERS = 4


def _render(base: str, site: Path, jobs: list[tuple[str, ShareFormat]]) -> None:
    with sync_playwright() as p:
        b = browser(p)
        pg = b.new_page(viewport={"width": 1200, "height": 675}, device_scale_factor=1)
        for rid, f in jobs:
            pg.goto(f"{base}/r/{rid}/{f.poster}?freeze={CARD_FREEZE_S}", wait_until="load")
            pg.evaluate("document.fonts.ready.then(() => true)")
            try:
                pg.wait_for_function(MODELS_READY)
            except Exception as e:
                raise RuntimeError(f"share image {f.key} of record {rid}: the models did not draw") from e
            pg.screenshot(path=str(site / "r" / rid / f.file), clip={"x": 0, "y": 0, "width": 1200, "height": 675})
        b.close()


def render_cards(site: Path, ids: list[str]) -> int:
    jobs = [(rid, f) for rid in ids for f in SHARE_FORMATS]
    srv, base = serve(site)
    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            for done in [pool.submit(_render, base, site, jobs[i::WORKERS]) for i in range(WORKERS)]:
                done.result()
    finally:
        srv.shutdown()
    return len(jobs)

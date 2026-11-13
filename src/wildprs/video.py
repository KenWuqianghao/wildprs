"""Share videos: one MP4 per record page or board page that links one (replay.mp4, race.mp4).

Frames are deterministic: the capture script sets the animation time of each frame through
window.wildprsVideo.frame(seconds) (see motion.js) and takes a screenshot; ffmpeg encodes the frames.
Videos are kept in data/video under a hash of their inputs, so `wildprs site` (which rebuilds the
tree from zero) restores them, and `wildprs video` renders only what is missing or changed."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config

FPS = 30
W, H = 1280, 720
CACHE = config.DATA / "video"
LINK = re.compile(r'href="(replay|race)\.mp4"')


def wanted(site: Path) -> list[tuple[Path, Path]]:
    """(page, mp4 path) for every page that links a video."""
    out = []
    for page in sorted([*site.glob("r/*/index.html"), *site.glob("b/**/index.html")]):
        m = LINK.search(page.read_text())
        if m:
            out.append((page, page.parent / f"{m.group(1)}.mp4"))
    return out


def _key(site: Path, page: Path) -> str:
    h = hashlib.sha1()
    for f in (page, page.parent / "track.json", page.parent / "race.json", page.parent / "record.json",
              site / "static" / "motion.js", site / "static" / "style.css"):
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def _cache_path(site: Path, page: Path) -> Path:
    name = page.parent.relative_to(site).as_posix().replace("/", "_")
    return CACHE / f"{name}-{_key(site, page)}.mp4"


def restore_cached(site: Path) -> int:
    n = 0
    for page, mp4 in wanted(site):
        c = _cache_path(site, page)
        if c.exists():
            shutil.copyfile(c, mp4)
            n += 1
    return n


def render(site: Path, force: bool = False, only: list[str] | None = None) -> list[dict]:
    from playwright.sync_api import sync_playwright

    from .render import browser, serve
    todo = [(p, m) for p, m in wanted(site) if not only or any(o in str(p) for o in only)]
    CACHE.mkdir(parents=True, exist_ok=True)
    done = []
    srv, base = serve(site)
    try:
        with sync_playwright() as pw:
            b = browser(pw)
            pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            errors: list[str] = []
            pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            pg.on("pageerror", lambda e: errors.append(str(e)))
            for page, mp4 in todo:
                cache = _cache_path(site, page)
                if cache.exists() and not force:
                    shutil.copyfile(cache, mp4)
                    continue
                t0 = time.time()
                url = f"{base}/{page.parent.relative_to(site).as_posix()}/?video=1"
                pg.goto(url, wait_until="load")
                pg.wait_for_function("window.wildprsReady === true || window.wildprsError", timeout=30000)
                if pg.evaluate("window.wildprsError || ''"):
                    raise RuntimeError(f"{url}: {pg.evaluate('window.wildprsError')}")
                duration = pg.evaluate("window.wildprsVideo.duration")
                n = int(round(duration * FPS))
                part = cache.with_suffix(".part.mp4")
                ff = subprocess.Popen(
                    ["ffmpeg", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "png",
                     "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                     "-r", str(FPS), "-movflags", "+faststart", "-an", str(part)], stdin=subprocess.PIPE)
                for i in range(n):
                    pg.evaluate(f"window.wildprsVideo.frame({i / FPS})")
                    ff.stdin.write(pg.screenshot(type="png", clip={"x": 0, "y": 0, "width": W, "height": H}))
                ff.stdin.close()
                if ff.wait() != 0:
                    raise RuntimeError(f"ffmpeg failed for {page}")
                if errors:
                    raise RuntimeError(f"{url}: console errors {errors[:3]}")
                for old in CACHE.glob(cache.name.rsplit("-", 1)[0] + "-*.mp4"):
                    if old != part:
                        old.unlink()
                part.replace(cache)
                shutil.copyfile(cache, mp4)
                done.append({"video": str(mp4.relative_to(site)), "frames": n, "bytes": mp4.stat().st_size,
                             "seconds": round(time.time() - t0)})
                config.log(f"video {mp4.relative_to(site)}: {n} frames, {mp4.stat().st_size / 1e6:.1f} MB")
            b.close()
    finally:
        srv.shutdown()
    return done

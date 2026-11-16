from __future__ import annotations

import hashlib
import io
import json
import re
import time
import tomllib
from dataclasses import asdict, dataclass
from html import unescape
from pathlib import Path

import requests

from . import config, taxa
from .model import ArtEntry, ArtFile

COMMONS = "https://commons.wikimedia.org/w/api.php"
MIN_LONG_SIDE = 1600
# Wikimedia serves thumbnails only at standard widths; other widths return the rate-limited original.
FETCH_WIDTH = 1920
# crop name -> (aspect ratio w/h, output widths)
CROPS = {"wide": (3 / 2, (480, 960, 1600)), "square": (1.0, (160, 400, 800))}


class ArtError(Exception):
    pass


@dataclass(frozen=True)
class Licence:
    name: str
    url: str


@dataclass(frozen=True)
class Row:
    """One entry of art.toml."""
    taxon: str
    photo: str
    photo_taxon: str = ""
    focus: tuple[float, float] = (0.5, 0.5)

    @property
    def key(self) -> str:
        fields = (self.photo, self.focus, self.photo_taxon, CROPS)
        return hashlib.sha1(json.dumps(fields).encode()).hexdigest()[:8]


def slug(taxon: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", taxon.lower()).strip("-")


# ---------- licences ----------

_CC = re.compile(r"^(?:cc[- ])?(by|by-sa)[- ](\d(?:\.\d)?)(?:[- ]([a-z]{2,}))?$")


def licence(code: str, url: str = "") -> Licence:
    """Allowed licences: CC0, public domain, CC BY and CC BY-SA of any version. `code` is the Commons
    licence code (cc-by-sa-3.0, cc0, pd)."""
    c = code.strip().lower()
    if c == "cc0":
        return Licence("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/")
    if c == "pd" or c.startswith("pd-"):
        return Licence("Public domain", url or "https://creativecommons.org/publicdomain/mark/1.0/")
    m = _CC.match(c)
    if m:
        name, ver, port = m.groups()
        ver = ver if "." in ver else ver + ".0"
        return Licence(f"CC {name.upper()} {ver}" + (f" {port.upper()}" if port else ""),
                       url or f"https://creativecommons.org/licenses/{name}/{ver}/" + (f"{port}/" if port else ""))
    raise ArtError(f"licence not allowed: {code!r}")


def plain(fragment: str) -> str:
    """Creator text from an HTML fragment: no tags, no entities, one line."""
    text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()
    return re.sub(r"\(\s+|\s+\)", lambda m: m.group().strip(), text)


# ---------- art.toml ----------

def read_table(path: Path | None = None) -> list[Row]:
    rows = []
    path = path or config.ART_FILE
    names = taxa.curated_names()
    for t, v in tomllib.loads(path.read_text()).items():
        try:
            row = Row(taxon=t, photo=v["photo"], photo_taxon=v.get("photo_taxon", ""),
                      focus=tuple(v.get("focus", (0.5, 0.5))))
        except KeyError as e:
            raise ArtError(f"art.toml: {t}: missing {e}") from None
        unknown = set(v) - {"photo", "photo_taxon", "focus"}
        if unknown or len(row.focus) != 2 or not all(0 <= f <= 1 for f in row.focus):
            raise ArtError(f"art.toml: {t}: bad entry {sorted(unknown) or v}")
        if t not in names:
            raise ArtError(f"art.toml: {t} is not in names.toml")
        rows.append(row)
    return rows


# ---------- HTTP ----------

def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = config.USER_AGENT
    return s


def _get(s: requests.Session, url: str, **params) -> requests.Response:
    for attempt in range(6):
        r = s.get(url, params=params or None, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(float(r.headers.get("Retry-After", 0) or 0) or 4 * (attempt + 1))
            continue
        r.raise_for_status()
        time.sleep(0.3)
        return r
    raise ArtError(f"{url}: HTTP {r.status_code} after retries")


# ---------- photos ----------

def _crop_box(w: int, h: int, ratio: float, focus: tuple[float, float]) -> tuple[int, int, int, int]:
    cw, ch = (w, round(w / ratio)) if w / h <= ratio else (round(h * ratio), h)
    x = min(max(round(focus[0] * w - cw / 2), 0), w - cw)
    y = min(max(round(focus[1] * h - ch / 2), 0), h - ch)
    return x, y, x + cw, y + ch


def _photo(s: requests.Session, row: Row, out: Path) -> ArtEntry:
    from PIL import Image, ImageOps
    d = _get(s, COMMONS, action="query", format="json", titles=row.photo, prop="imageinfo",
             iiprop="extmetadata|url|size", iiurlwidth=FETCH_WIDTH).json()
    page = next(iter(d["query"]["pages"].values()))
    if "imageinfo" not in page:
        raise ArtError(f"{row.taxon}: Commons has no file {row.photo!r}")
    ii = page["imageinfo"][0]
    meta = {k: str(v.get("value", "")) for k, v in ii.get("extmetadata", {}).items()}
    try:
        lic = licence(meta.get("License", ""), meta.get("LicenseUrl", ""))
    except ArtError as e:
        raise ArtError(f"{row.taxon}: {row.photo}: {e} ({meta.get('LicenseShortName', 'no licence field')})") from None
    creator = plain(meta.get("Artist", "")) or plain(meta.get("Attribution", ""))
    if not creator and lic.name != "Public domain":
        raise ArtError(f"{row.taxon}: {row.photo}: no creator in the file metadata")
    if max(ii["width"], ii["height"]) < MIN_LONG_SIDE:
        raise ArtError(f"{row.taxon}: {row.photo}: {ii['width']}x{ii['height']} is under {MIN_LONG_SIDE} px")
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(_get(s, ii.get("thumburl") or ii["url"]).content))).convert("RGB")
    files = []
    for crop, (ratio, widths) in CROPS.items():
        part = im.crop(_crop_box(im.width, im.height, ratio, row.focus))
        for w in widths:
            if w > part.width:
                continue
            path = f"photo/{slug(row.taxon)}-{row.key}-{crop}-{w}.webp"
            small = part.resize((w, round(w / ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            small.save(buf, "WEBP", quality=80, method=6)
            config.atomic_write(out / path, buf.getvalue())
            files.append(ArtFile(path, crop, small.width, small.height))
    if not any(f.crop == "wide" for f in files):
        raise ArtError(f"{row.taxon}: {row.photo}: too small for the crops")
    return ArtEntry(taxon=row.taxon, files=tuple(files), licence=lic.name, licence_url=lic.url,
                    creator=creator or "Unknown", source_url=ii["descriptionurl"], shows=row.photo_taxon or row.taxon,
                    change="Cropped and resized")


# ---------- stage ----------

def manifest_path() -> Path:
    return config.ART / "manifest.json"


def load() -> dict[str, ArtEntry]:
    p = manifest_path()
    if not p.exists():
        return {}
    return {e["taxon"]: ArtEntry(**{**e, "files": tuple(ArtFile(**f) for f in e["files"])})
            for e in json.loads(p.read_text())}


def _write_manifest(entries) -> None:
    config.atomic_write(manifest_path(), json.dumps(
        [asdict(e) for e in sorted(entries, key=lambda e: e.taxon)], indent=1, ensure_ascii=False))


def run(force: bool = False, only: list[str] | None = None) -> dict:
    rows = read_table()
    out = config.ART
    have = load()
    s = _session()
    done: dict[str, ArtEntry] = {}
    fetched = 0
    for row in rows:
        old = have.get(row.taxon)
        again = force or (only is not None and row.taxon in only)
        if old and not again and all(f"-{row.key}-" in f.path and (out / f.path).exists() for f in old.files):
            done[row.taxon] = old
            continue
        done[row.taxon] = _photo(s, row, out)
        fetched += 1
        _write_manifest({**have, **done}.values())
        print(f"art photo {row.taxon}", flush=True)
    _write_manifest(done.values())
    used = {f.path for e in done.values() for f in e.files}
    for p in out.glob("photo/*"):
        if p.relative_to(out).as_posix() not in used:
            p.unlink()
    return {"photos": len(done), "fetched": fetched}

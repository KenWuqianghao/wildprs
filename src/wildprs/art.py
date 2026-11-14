"""Animal images: a photo (Wikimedia Commons) and a silhouette (PhyloPic) per taxon.

`art.toml` names the files. `wildprs art` fetches them with their licence facts, crops and resizes the
photos, normalises the silhouettes (one path, facing right, longest side 100 units) and writes
data/art/manifest.json. The site reads only the manifest."""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from html import unescape
from pathlib import Path

import requests

from . import config, taxa
from .model import ArtEntry, ArtFile, ArtKind

COMMONS = "https://commons.wikimedia.org/w/api.php"
PHYLOPIC = "https://api.phylopic.org"
MIN_LONG_SIDE = 1600
# Wikimedia serves thumbnails only at standard widths; other widths return the rate-limited original.
FETCH_WIDTH = 1920
# crop name -> (aspect ratio w/h, output widths)
CROPS = {"wide": (3 / 2, (480, 960, 1600)), "square": (1.0, (160, 400, 800))}
SIL_UNITS = 100.0


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
    silhouette: str
    faces: str
    photo: str = ""
    photo_taxon: str = ""
    focus: tuple[float, float] = (0.5, 0.5)
    silhouette_taxon: str = ""

    def key(self, kind: ArtKind) -> str:
        """Short hash of the fields (and output format) a file depends on: part of its file name, so a
        changed entry is fetched again."""
        fields = ((self.photo, self.focus, self.photo_taxon, CROPS) if kind is ArtKind.PHOTO
                  else (self.silhouette, self.faces, self.silhouette_taxon, SIL_UNITS, 2))
        return hashlib.sha1(json.dumps(fields).encode()).hexdigest()[:8]


def slug(taxon: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", taxon.lower()).strip("-")


# ---------- licences ----------

_CC = re.compile(r"^(?:cc[- ])?(by|by-sa)[- ](\d(?:\.\d)?)(?:[- ]([a-z]{2,}))?$")


def licence(code: str, url: str = "") -> Licence:
    """Allowed licences: CC0, public domain, CC BY and CC BY-SA of any version. `code` is the Commons
    licence code (cc-by-sa-3.0, cc0, pd) or a creativecommons.org licence URL (PhyloPic)."""
    c = code.strip().lower()
    m = re.match(r"^https?://creativecommons\.org/(licenses|publicdomain)/([a-z-]+)/(\d(?:\.\d)?)", c)
    if m:
        kind, name, ver = m.groups()
        if kind == "publicdomain":
            if name == "zero":
                return Licence("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/")
            if name == "mark":
                return Licence("Public domain", "https://creativecommons.org/publicdomain/mark/1.0/")
            raise ArtError(f"licence not allowed: {code!r}")
        c = f"cc-{name}-{ver}"
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
            row = Row(taxon=t, silhouette=v["silhouette"], faces=v["faces"], photo=v.get("photo", ""),
                      photo_taxon=v.get("photo_taxon", ""), focus=tuple(v.get("focus", (0.5, 0.5))),
                      silhouette_taxon=v.get("silhouette_taxon", ""))
        except KeyError as e:
            raise ArtError(f"art.toml: {t}: missing {e}") from None
        unknown = set(v) - {"silhouette", "faces", "photo", "photo_taxon", "focus", "silhouette_taxon"}
        if unknown or row.faces not in ("left", "right") or len(row.focus) != 2 or not all(0 <= f <= 1 for f in row.focus):
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
            path = f"photo/{slug(row.taxon)}-{row.key(ArtKind.PHOTO)}-{crop}-{w}.webp"
            small = part.resize((w, round(w / ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            small.save(buf, "WEBP", quality=80, method=6)
            config.atomic_write(out / path, buf.getvalue())
            files.append(ArtFile(path, crop, small.width, small.height))
    if not any(f.crop == "wide" for f in files):
        raise ArtError(f"{row.taxon}: {row.photo}: too small for the crops")
    return ArtEntry(taxon=row.taxon, kind=ArtKind.PHOTO, files=tuple(files), licence=lic.name, licence_url=lic.url,
                    creator=creator or "Unknown", source_url=ii["descriptionurl"], shows=row.photo_taxon or row.taxon,
                    change="Cropped and resized")


# ---------- silhouettes ----------

_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "Z": 0}


def _transform(text: str) -> tuple[float, ...]:
    """SVG transform list -> affine (a, b, c, d, e, f)."""
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for fn, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text or ""):
        v = [float(x) for x in _NUM.findall(args)]
        if fn == "matrix":
            t = tuple(v)
        elif fn == "translate":
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif fn == "scale":
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        else:
            raise ArtError(f"SVG transform {fn} is not supported")
        m = _mul(m, t)
    return m


def _mul(m, t):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = t
    return (a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D, a * E + c * F + e, b * E + d * F + f)


def _subpaths(d: str) -> list[list[tuple[str, list[tuple[float, float]]]]]:
    """Path data -> subpaths of absolute segments ('M'|'L'|'C'|'Q', points); Z closes a subpath."""
    toks = re.findall(r"[MmLlHhVvCcSsQqTtZzAa]|" + _NUM.pattern, d)
    subs, cur = [], []
    x = y = sx = sy = 0.0
    last_c = last_q = None
    i, cmd = 0, None
    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
        if cmd is None or cmd.upper() == "A":
            raise ArtError("SVG path arcs are not supported")
        n = _ARGS[cmd.upper()]
        v = [float(t) for t in toks[i:i + n]]
        i += n
        rel = cmd.islower()
        C = cmd.upper()
        if C == "Z":
            if cur:
                subs.append(cur)
            cur, x, y = [], sx, sy
            last_c = last_q = None
            continue
        if C == "M":
            if cur:
                subs.append(cur)
            x, y = (x + v[0], y + v[1]) if rel else (v[0], v[1])
            sx, sy, cur = x, y, [("M", [(x, y)])]
            cmd = "l" if rel else "L"
            last_c = last_q = None
            continue
        pts = [(v[k] + (x if rel else 0), v[k + 1] + (y if rel else 0)) for k in range(0, len(v) - 1, 2)] if C not in "HV" else []
        if C == "H":
            pts, C = [((x if rel else 0) + v[0], y)], "L"
        elif C == "V":
            pts, C = [(x, (y if rel else 0) + v[0])], "L"
        elif C == "S":
            p1 = (2 * x - last_c[0], 2 * y - last_c[1]) if last_c else (x, y)
            pts, C = [p1, *pts], "C"
        elif C == "T":
            p1 = (2 * x - last_q[0], 2 * y - last_q[1]) if last_q else (x, y)
            pts, C = [p1, *pts], "Q"
        if not cur:
            cur = [("M", [(x, y)])]
        cur.append((C, pts))
        last_c = pts[-2] if C == "C" else None
        last_q = pts[0] if C == "Q" else None
        x, y = pts[-1]
    if cur:
        subs.append(cur)
    return subs


def normalise_svg(svg: str, faces: str) -> tuple[str, float, float, bool]:
    """Any single-colour SVG of paths -> (path data, width, height, evenodd), facing right, with the
    longest side SIL_UNITS long."""
    root = ET.fromstring(re.sub(r"<!DOCTYPE[^>]*>", "", svg))
    ns = "{http://www.w3.org/2000/svg}"
    shapes, evenodd = [], False

    def walk(el, m):
        nonlocal evenodd
        tag = el.tag.removeprefix(ns)
        m = _mul(m, _transform(el.get("transform", "")))
        if tag == "path":
            if (el.get("fill") or "").lower() in ("none", "#fff", "#ffffff", "white"):
                raise ArtError("SVG has a path that is not filled black")
            evenodd |= (el.get("fill-rule") or "") == "evenodd" or "evenodd" in (el.get("style") or "")
            shapes.extend((m, sp) for sp in _subpaths(el.get("d", "")))
        elif tag in ("svg", "g"):
            evenodd |= (el.get("fill-rule") or "") == "evenodd"
            for ch in el:
                walk(ch, m)
        elif tag not in ("metadata", "title", "desc", "defs"):
            raise ArtError(f"SVG element <{tag}> is not supported")

    walk(root, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
    subs = []
    for m, sp in shapes:
        a, b, c, d, e, f = m
        subs.append([(C, [(a * px + c * py + e, b * px + d * py + f) for px, py in pts]) for C, pts in sp])
    allp = [p for sp in subs for _, pts in sp for p in pts]
    if not allp:
        raise ArtError("SVG has no path")
    x0, x1 = min(p[0] for p in allp), max(p[0] for p in allp)
    y0, y1 = min(p[1] for p in allp), max(p[1] for p in allp)
    k = SIL_UNITS / max(x1 - x0, y1 - y0)
    w, h = round((x1 - x0) * k, 1), round((y1 - y0) * k, 1)

    def tx(p):
        x = (p[0] - x0) * k
        return (round(w - x if faces == "left" else x, 1), round((p[1] - y0) * k, 1))

    out = []
    for sp in subs:
        pts = [tx(p) for _, ps in sp for p in ps]
        if max(p[0] for p in pts) - min(p[0] for p in pts) < 0.8 and max(p[1] for p in pts) - min(p[1] for p in pts) < 0.8:
            continue  # tracing speck
        cx, cy = pts[0]
        parts = [f"M{cx:g} {cy:g}"]
        for C, ps in sp[1:]:
            rel = []
            for p in (tx(q) for q in ps):
                rel.append(f"{round(p[0] - cx, 1):g} {round(p[1] - cy, 1):g}")
            cx, cy = tx(ps[-1])
            parts.append(C.lower() + " ".join(rel))
        parts.append("z")
        out.append("".join(parts))
    return "".join(out).replace(" -", "-"), w, h, evenodd


def silhouette_svg(d: str, w: float, h: float, evenodd: bool) -> str:
    rule = ' fill-rule="evenodd"' if evenodd else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:g}" height="{h:g}" viewBox="0 0 {w:g} {h:g}">'
            f'<path{rule} d="{d}"/></svg>')


def read_silhouette(path: Path) -> tuple[str, float, float, bool]:
    """(path data, width, height, evenodd) of a normalised silhouette file."""
    t = path.read_text()
    w, h = (float(x) for x in re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', t).groups())
    return re.search(r' d="([^"]+)"', t).group(1), w, h, 'fill-rule="evenodd"' in t


def _silhouette(s: requests.Session, row: Row, out: Path, build: int) -> ArtEntry:
    im = _get(s, f"{PHYLOPIC}/images/{row.silhouette}", build=build).json()
    L = im["_links"]
    try:
        lic = licence(L["license"]["href"])
    except ArtError as e:
        raise ArtError(f"{row.taxon}: PhyloPic {row.silhouette}: {e}") from None
    shows = L["specificNode"]["title"]
    want = row.silhouette_taxon or row.taxon
    if not shows.lower().startswith(want.lower()):
        raise ArtError(f"{row.taxon}: PhyloPic {row.silhouette} shows {shows!r}; art.toml says {want!r}")
    creator = plain(im.get("attribution") or "") or plain(L.get("contributor", {}).get("title", ""))
    try:
        d, w, h, eo = normalise_svg(_get(s, L["vectorFile"]["href"]).text, row.faces)
    except (ArtError, ET.ParseError, KeyError) as e:
        raise ArtError(f"{row.taxon}: PhyloPic {row.silhouette}: {e}") from None
    path = f"sil/{slug(row.taxon)}-{row.key(ArtKind.SILHOUETTE)}.svg"
    config.atomic_write(out / path, silhouette_svg(d, w, h, eo))
    change = "Mirrored, recoloured and resized" if row.faces == "left" else "Recoloured and resized"
    return ArtEntry(taxon=row.taxon, kind=ArtKind.SILHOUETTE, files=(ArtFile(path, "full", round(w), round(h)),),
                    licence=lic.name, licence_url=lic.url, creator=creator or "Unknown",
                    source_url=f"https://www.phylopic.org/images/{row.silhouette}", shows=shows, change=change)


# ---------- stage ----------

def manifest_path() -> Path:
    return config.ART / "manifest.json"


def load() -> dict[str, dict[ArtKind, ArtEntry]]:
    """Manifest -> taxon -> kind -> entry. Missing manifest: no art."""
    p = manifest_path()
    if not p.exists():
        return {}
    out: dict[str, dict[ArtKind, ArtEntry]] = {}
    for e in json.loads(p.read_text()):
        entry = ArtEntry(taxon=e["taxon"], kind=ArtKind(e["kind"]), files=tuple(ArtFile(**f) for f in e["files"]),
                         licence=e["licence"], licence_url=e["licence_url"], creator=e["creator"],
                         source_url=e["source_url"], shows=e["shows"], change=e["change"])
        out.setdefault(entry.taxon, {})[entry.kind] = entry
    return out


def _write_manifest(entries) -> None:
    config.atomic_write(manifest_path(), json.dumps(
        [asdict(e) for e in sorted(entries, key=lambda e: (e.taxon, e.kind))], indent=1, ensure_ascii=False))


def run(force: bool = False, only: list[str] | None = None) -> dict:
    """Fetch what is missing or changed. The manifest is written after each file, so a stage that stops
    on an error keeps what it fetched."""
    rows = read_table()
    out = config.ART
    have = {(e.taxon, e.kind): e for kinds in load().values() for e in kinds.values()}
    s = _session()
    build = int(_get(s, f"{PHYLOPIC}/").json()["build"])
    done: dict[tuple[str, ArtKind], ArtEntry] = {}
    fetched = 0
    for row in rows:
        again = force or (only is not None and row.taxon in only)
        for kind, wanted, fn in ((ArtKind.SILHOUETTE, True, lambda: _silhouette(s, row, out, build)),
                                 (ArtKind.PHOTO, bool(row.photo), lambda: _photo(s, row, out))):
            if not wanted:
                continue
            old = have.get((row.taxon, kind))
            if old and not again and all(f"-{row.key(kind)}-" in f.path.replace(".svg", "-")
                                         and (out / f.path).exists() for f in old.files):
                done[(row.taxon, kind)] = old
                continue
            done[(row.taxon, kind)] = fn()
            fetched += 1
            _write_manifest({**have, **done}.values())
            print(f"art {kind.value} {row.taxon}", flush=True)
    _write_manifest(done.values())
    used = {f.path for e in done.values() for f in e.files}
    for p in [*out.glob("photo/*"), *out.glob("sil/*")]:
        if p.relative_to(out).as_posix() not in used:
            p.unlink()
    return {"taxa": len(rows), "photos": sum(k is ArtKind.PHOTO for _, k in done),
            "silhouettes": sum(k is ArtKind.SILHOUETTE for _, k in done), "fetched": fetched,
            "no photo": sorted(r.taxon for r in rows if not r.photo)}

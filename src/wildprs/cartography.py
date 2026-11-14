"""Map and profile drawings as SVG strings. The site never ships fix tables: only simplified
track geometry, coarsened to 0.1 degree for sensitive records."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import LineString, box

from . import geo
from .model import Track, haversine_km

COARSE_DEG = 0.1
MAX_TRACK_POINTS = 320


@dataclass
class MapView:
    """Equirectangular frame with a cos(latitude) x-scale. Coordinates in SVG units."""
    lon0: float
    lat1: float
    kx: float
    scale: float
    w: float
    h: float

    def xy(self, lon, lat):
        return (np.asarray(lon) - self.lon0) * self.kx * self.scale, (self.lat1 - np.asarray(lat)) * self.scale


Box = tuple[float, float, float, float]
RIGHT: Box = (0.42, 0.11, 0.94, 0.89)  # the left 40 % stays free for the poster text
FULL: Box = (0.08, 0.10, 0.92, 0.90)


def frame(lon: np.ndarray, lat: np.ndarray, w: float, h: float, box: Box = RIGHT, min_span_deg: float = 3.0) -> MapView:
    """Fit the track into box, given as (left, top, right, bottom) fractions of the frame."""
    lat_c = float(np.clip((lat.min() + lat.max()) / 2, -70, 70))
    kx = math.cos(math.radians(lat_c))
    tw = max((lon.max() - lon.min()) * kx, min_span_deg)
    th = max(lat.max() - lat.min(), min_span_deg * 0.6)
    x0, y0, x1, y1 = box
    scale = min((x1 - x0) * w / tw, (y1 - y0) * h / th)
    lon0 = (lon.min() + lon.max()) / 2 - (x0 + x1) / 2 * w / (kx * scale)
    lat_top = (lat.max() + lat.min()) / 2 + (y0 + y1) / 2 * h / scale
    return MapView(lon0, lat_top, kx, scale, w, h)


def _path(coords_xy) -> str:
    return "M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in coords_xy)


def land_svg(v: MapView, tolerance_px: float = 1.2) -> str:
    lon_min, lon_max = v.lon0, v.lon0 + v.w / (v.kx * v.scale)
    lat_max, lat_min = v.lat1, v.lat1 - v.h / v.scale
    tol = tolerance_px / v.scale
    level = next((t for t in (0.2, 0.05, 0.01, 0.0) if t <= tol), 0.0)
    parts, tree = geo.land_parts(level)
    out = []
    for shift in (0, 360, -360):
        win = box(lon_min - shift, max(lat_min, -90), lon_max - shift, min(lat_max, 90))
        idx = tree.query(win)
        if not len(idx):
            continue
        clip = shapely.intersection(parts[idx], win)
        for poly in shapely.get_parts(shapely.simplify(clip, tol)):
            if poly.geom_type != "Polygon" or poly.is_empty:
                continue
            for ring in (poly.exterior, *poly.interiors):
                c = np.asarray(ring.coords)
                if len(c) < 4:
                    continue
                x, y = v.xy(c[:, 0] + shift, c[:, 1])
                out.append(_path(zip(x, y)) + "Z")
    return "".join(out)


def borders_svg(v: MapView) -> str:
    lon_min, lon_max = v.lon0, v.lon0 + v.w / (v.kx * v.scale)
    lat_max, lat_min = v.lat1, v.lat1 - v.h / v.scale
    lines, tree = geo.border_parts()
    out = []
    for shift in (0, 360, -360):
        win = box(lon_min - shift, max(lat_min, -90), lon_max - shift, min(lat_max, 90))
        idx = tree.query(win)
        for g in shapely.get_parts(shapely.intersection(lines[idx], win)) if len(idx) else ():
            if g.geom_type != "LineString" or g.is_empty:
                continue
            c = np.asarray(g.coords)
            x, y = v.xy(c[:, 0] + shift, c[:, 1])
            out.append(_path(zip(x, y)))
    return "".join(out)


def graticule_svg(v: MapView) -> str:
    lon_min, lon_max = v.lon0, v.lon0 + v.w / (v.kx * v.scale)
    lat_min, lat_max = v.lat1 - v.h / v.scale, v.lat1
    span = max(lon_max - lon_min, lat_max - lat_min)
    step = next(s for s in (1, 2, 5, 10, 20, 30, 60) if span / s <= 14)
    d = []
    for lo in np.arange(math.ceil(lon_min / step) * step, lon_max, step):
        x, _ = v.xy(lo, 0)
        d.append(f"M{x:.1f},0V{v.h:.0f}")
    for la in np.arange(math.ceil(lat_min / step) * step, lat_max, step):
        _, y = v.xy(0, la)
        d.append(f"M0,{y:.1f}H{v.w:.0f}")
    return "".join(d)


def simplify(lon: np.ndarray, lat: np.ndarray, coarse: bool) -> tuple[np.ndarray, np.ndarray]:
    if coarse:
        lon, lat = np.round(lon / COARSE_DEG) * COARSE_DEG, np.round(lat / COARSE_DEG) * COARSE_DEG
        keep = np.r_[True, (np.diff(lon) != 0) | (np.diff(lat) != 0)]
        lon, lat = lon[keep], lat[keep]
    if len(lon) < 3:
        return lon, lat
    line = LineString(np.c_[lon, lat])
    span = max(np.ptp(lon), np.ptp(lat), 1e-3)
    tol = span / 1500
    for _ in range(30):
        s = line.simplify(tol, preserve_topology=False)
        if len(s.coords) <= MAX_TRACK_POINTS:
            break
        tol *= 1.6
    c = np.asarray(s.coords)
    if len(c) > MAX_TRACK_POINTS:
        c = c[np.linspace(0, len(c) - 1, MAX_TRACK_POINTS).astype(int)]
    return c[:, 0], c[:, 1]


def sensitivity(tr: Track, i0: int, i1: int) -> str | None:
    """Why this record's geometry must be coarsened, or None."""
    tx = tr.animal.taxon
    if tx.threatened:
        return f"threatened taxon (IUCN Red List {tx.iucn})"
    cells = np.round(tr.lat * 100).astype(np.int64) * 100000 + np.round(tr.lon * 100).astype(np.int64)
    vals, counts = np.unique(cells, return_counts=True)
    k = int(np.argmax(cells == vals[np.argmax(counts)]))
    homes = [(tr.lat[0], tr.lon[0]), (tr.lat[k], tr.lon[k])]  # tagging site, most used 1 km cell
    for i in (i0, i1):
        for hl, ho in homes:
            if haversine_km(tr.lat[i], tr.lon[i], hl, ho) < 2.0:
                return "an endpoint lies within 2 km of a likely nest, roost or colony"
    return None


def profile_svg(x: np.ndarray, y: np.ndarray, w: int = 640, h: int = 160, cls: str = "",
                bands: list | None = None) -> str:
    """Area chart. x in seconds, y in display units. bands: [start, end] spans in seconds from x[0]
    (night), drawn behind the area."""
    if len(x) < 2:
        return ""
    x0, x1 = float(x.min()), float(x.max())
    fin = np.isfinite(y)
    ymax = float(np.nanmax(y)) if fin.any() else 1.0
    ymax = ymax or 1.0
    ymin = min(0.0, float(np.nanmin(y))) if fin.any() else 0.0
    px = (x - x0) / max(x1 - x0, 1) * w
    py = h - (np.nan_to_num(y, nan=ymin) - ymin) / max(ymax - ymin, 1e-9) * (h - 8)
    line = "M" + "L".join(f"{a:.1f},{b:.1f}" for a, b in zip(px, py))
    area = line + f"L{w},{h}L0,{h}Z"
    night = "".join(f'<rect class="nb" x="{a / max(x1 - x0, 1) * w:.1f}" y="0" width="{(b - a) / max(x1 - x0, 1) * w:.1f}" height="{h}"/>'
                    for a, b in (bands or []))
    return (f'<svg class="profile {cls}" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
            f'{night}<path class="pa" d="{area}"/><path class="pl" d="{line}" vector-effect="non-scaling-stroke"/></svg>')

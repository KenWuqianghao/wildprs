"""Natural Earth 1:50m land and countries (public domain) and NOAA ETOPO terrain height."""
from __future__ import annotations

import json
import pickle
import zipfile
from functools import cache

import numpy as np
import requests
import shapefile
import shapely
from shapely.geometry import shape

from . import config

NE = {
    "land": "https://naciscdn.org/naturalearth/50m/physical/ne_50m_land.zip",
    "countries": "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_countries.zip",
}
COAST_TOLERANCE_DEG = 0.03  # about 3 km: a fix this close to land counts as on land
ETOPO = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/etopo180.json"  # ETOPO1, 1 arc-minute


def ensure_natural_earth() -> None:
    for key, url in NE.items():
        d = config.GEO / key
        if any(d.glob("*.shp")):
            continue
        z = config.GEO / url.rsplit("/", 1)[1]
        if not z.exists():
            r = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=300)
            r.raise_for_status()
            config.atomic_write(z, r.content)
        d.mkdir(parents=True, exist_ok=True)
        zipfile.ZipFile(z).extractall(d)


@cache
def _shapes():
    cache_file = config.GEO / "wildprs_cache.pkl"
    if cache_file.exists():
        land_raw, land_buf, names, geoms = pickle.loads(cache_file.read_bytes())
        return (shapely.from_wkb(land_raw), shapely.from_wkb(land_buf), np.array(names),
                shapely.from_wkb(geoms))
    ensure_natural_earth()
    land_raw = shapely.union_all([shape(s.__geo_interface__) for s in
                                  shapefile.Reader(str(next((config.GEO / "land").glob("*.shp")))).shapes()])
    land_buf = land_raw.buffer(COAST_TOLERANCE_DEG)
    r = shapefile.Reader(str(next((config.GEO / "countries").glob("*.shp"))))
    fields = [f[0] for f in r.fields[1:]]
    names, geoms = [], []
    for sr in r.shapeRecords():
        rec = dict(zip(fields, sr.record))
        names.append(rec.get("NAME") or rec.get("ADMIN"))
        geoms.append(shape(sr.shape.__geo_interface__))
    geoms = np.array(geoms, dtype=object)
    config.atomic_write(cache_file, pickle.dumps((shapely.to_wkb(land_raw), shapely.to_wkb(land_buf), names,
                                                  shapely.to_wkb(geoms))))
    return land_raw, land_buf, np.array(names), geoms


@cache
def _prepared():
    land_raw, land_buf, names, geoms = _shapes()
    shapely.prepare(land_buf)
    return land_buf, names, shapely.STRtree(geoms)


def land_geometry():
    return _shapes()[0]


@cache
def land_parts(tolerance_deg: float):
    """Land polygons simplified once per tolerance, with a spatial index for clipping."""
    parts = shapely.get_parts(_shapes()[0])
    if tolerance_deg > 0:
        parts = shapely.simplify(parts, tolerance_deg, preserve_topology=True)
    parts = parts[~shapely.is_empty(parts)]
    return parts, shapely.STRtree(parts)


@cache
def border_parts():
    """Country outlines as lines, simplified to about 1 km, with a spatial index."""
    lines = shapely.simplify(shapely.boundary(_shapes()[3]), 0.01)
    lines = np.array([p for g in lines for p in shapely.get_parts(g)], dtype=object)
    return lines, shapely.STRtree(lines)


def on_land(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    return shapely.contains_xy(_prepared()[0], lon, lat)


def country(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Country name per fix ('' at sea). Lookups are shared per 0.01 degree cell."""
    _, names, tree = _prepared()
    lat, lon = np.atleast_1d(lat), np.atleast_1d(lon)
    key = (np.round(lat * 100).astype(np.int64) + 9000) * 40000 + np.round(lon * 100).astype(np.int64) + 18000
    uk, first, inv = np.unique(key, return_index=True, return_inverse=True)
    pi, gi = tree.query(shapely.points(lon[first], lat[first]), predicate="within")
    out = np.full(len(uk), "", dtype=object)
    out[pi] = names[gi]
    return out.astype(str)[inv]


def place(lat: float, lon: float) -> str:
    return str(country(np.array([lat]), np.array([lon]))[0])


_TERRAIN_FILE = config.GEO / "terrain_cache.json"


@cache
def _terrain_cache() -> dict:
    return json.loads(_TERRAIN_FILE.read_text()) if _TERRAIN_FILE.exists() else {}


def terrain_max_m(lat: float, lon: float, radius_deg: float = 0.03) -> float:
    """Highest ETOPO1 elevation within about 3 km of the point. Cached on disk."""
    key = f"{lat:.3f},{lon:.3f}"
    c = _terrain_cache()
    if key not in c:
        q = (f"altitude[({lat - radius_deg:.4f}):({lat + radius_deg:.4f})]"
             f"[({lon - radius_deg:.4f}):({lon + radius_deg:.4f})]")
        r = requests.get(f"{ETOPO}?{q}", headers={"User-Agent": config.USER_AGENT}, timeout=60)
        r.raise_for_status()
        c[key] = max(row[2] for row in r.json()["table"]["rows"])
        config.atomic_write(_TERRAIN_FILE, json.dumps(c))
    return float(c[key])

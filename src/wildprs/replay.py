"""Motion data for the replays, races and videos.

track.json (one per record) holds the part of the track that the replay draws, simplified to a few
hundred points, with time. For a sensitive record every published column is a pure function of
positions on the 0.1 degree grid and times on a 15-minute grid (`derive`), so the file holds no more
than the coarse map already shows. `sitecheck` recomputes those columns to enforce it."""
from __future__ import annotations

import math

import numpy as np
from shapely.geometry import LineString

from . import geo
from .cartography import COARSE_DEG, MapView
from .model import Group, Track, haversine_km
from .segments import gate_crossings
from .categories import SEGMENT_BY_KEY

MAX_POINTS = 360
COARSE_TQ_S = 900          # time grid of a sensitive track
NIGHT_MAX_DAYS = 5         # longer windows get no night bands: they become stripes
APPROACH_S = {"farthest-north": 3 * 86400, "farthest-south": 3 * 86400, "highest-flight": 6 * 3600}
WHOLE_TRACK = {"lifetime", "countries"}
DISTANCE = {"lifetime", "biggest-day", "biggest-week", "sea-crossing"}


def window(cat_key: str, tr: Track, t0: float, t1: float) -> tuple[int, int, int, int]:
    """Fix indices (a, b) that the replay covers and (i0, i1) of the effort itself."""
    t = tr.t
    i0, i1 = int(np.searchsorted(t, t0)), min(int(np.searchsorted(t, t1)), len(t) - 1)
    if cat_key in WHOLE_TRACK:
        return 0, len(t) - 1, i0, i1
    a = int(np.searchsorted(t, t0 - APPROACH_S.get(cat_key, 0)))
    return a, i1, i0, i1


# ---------- derived columns: pure functions of published data ----------

def cumulative_km(lon, lat, brk) -> np.ndarray:
    legs = haversine_km(lat[:-1], lon[:-1], lat[1:], lon[1:])
    return np.r_[0.0, np.cumsum(np.where(brk, 0.0, legs))]


def speed_kmh(t, km) -> np.ndarray:
    """Average speed over a centred window of 1/40 of the span (10 min to 1 day)."""
    if len(t) < 2:
        return np.zeros(len(t))
    w = float(np.clip((t[-1] - t[0]) / 40, 600, 86400))
    lo = np.searchsorted(t, t - w / 2, side="left")
    hi = np.minimum(np.searchsorted(t, t + w / 2, side="right") - 1, len(t) - 1)
    same = hi == lo
    lo = np.where(same, np.maximum(lo - 1, 0), lo)
    hi = np.where(same & (lo == hi), np.minimum(hi + 1, len(t) - 1), hi)
    return (km[hi] - km[lo]) / np.maximum(t[hi] - t[lo], 1) * 3600


def derive(t, lon, lat, brk_legs, km_scale: float) -> tuple[np.ndarray, np.ndarray]:
    """(km, kmh) of a coarse track, from its published columns only."""
    brk = np.zeros(max(len(t) - 1, 0), bool)
    brk[np.asarray(brk_legs, int)] = True
    km = cumulative_km(np.asarray(lon, float), np.asarray(lat, float), brk) * km_scale
    return np.round(km, 1), np.round(speed_kmh(np.asarray(t, float), km), 0)


# ---------- the record's own measure up to each point ----------

def value_so_far(cat_key: str, value: float, t, lon, lat, km, h, eff0: int,
                 t_cross: float | None, n_countries: np.ndarray | None) -> np.ndarray:
    """It ends at the record value. Before the effort starts it is 0 (or the start value)."""
    if cat_key in DISTANCE:
        k = np.maximum(km - km[eff0], 0)
        return np.minimum(k * (value / k[-1] if k[-1] > 0 else 1.0), value)
    if cat_key == "fastest-hour":
        hours = (t - t[eff0]) / 3600
        return np.clip(np.where(hours > 5 / 60, (km - km[eff0]) / np.maximum(hours, 1e-9), 0.0), 0, value)
    if cat_key == "round-trip":
        return np.minimum(np.maximum.accumulate(haversine_km(lat[0], lon[0], lat, lon)), value)
    if cat_key == "farthest-north":
        return np.minimum(np.maximum.accumulate(lat), value)
    if cat_key == "farthest-south":
        return np.maximum(np.minimum.accumulate(lat), value)
    if cat_key == "highest-flight":
        return np.minimum(np.maximum.accumulate(np.nan_to_num(h, nan=0.0)), value)
    if cat_key == "countries":
        return np.minimum(n_countries, value)
    return np.clip((t - t_cross) / 3600, 0, value)  # segment: hours since the first gate


def countries_so_far(tr: Track, idx: np.ndarray, min_fixes: int = 3) -> np.ndarray:
    """Number of countries with min_fixes fixes up to each selected fix (as in categories.countries)."""
    c = geo.country(tr.lat, tr.lon)
    reached = np.zeros(len(c), int)
    for name in set(c[c != ""].tolist()):
        hits = np.flatnonzero(c == name)
        if len(hits) >= min_fixes:
            reached[hits[min_fixes - 1]] += 1
    return np.cumsum(reached)[idx]


def gate_time(tr: Track, cat_key: str, i0: int) -> float | None:
    seg = SEGMENT_BY_KEY.get(cat_key)
    if seg is None:
        return None
    times = [tt for g in (seg.gate_a, seg.gate_b) for k, tt in zip(*gate_crossings(tr, g)) if k == i0]
    return float(min(times)) if times else float(tr.t[i0])


# ---------- sun ----------

def sun_elevation_deg(t, lat, lon) -> np.ndarray:
    """Approximate solar elevation (about 0.5 degree), from position and UTC time."""
    d = np.asarray(t, float) / 86400 - 10957.5           # days since J2000.0
    g = np.radians(357.529 + 0.98560028 * d)
    q = 280.459 + 0.98564736 * d
    L = np.radians(q + 1.915 * np.sin(g) + 0.020 * np.sin(2 * g))
    e = np.radians(23.439 - 0.00000036 * d)
    ra = np.arctan2(np.cos(e) * np.sin(L), np.cos(L))
    dec = np.arcsin(np.sin(e) * np.sin(L))
    gmst = np.radians((280.46061837 + 360.98564736629 * d) % 360)
    ha = gmst + np.radians(lon) - ra
    la = np.radians(lat)
    return np.degrees(np.arcsin(np.sin(la) * np.sin(dec) + np.cos(la) * np.cos(dec) * np.cos(ha)))


def night_spans(t, lon, lat) -> list[list[int]]:
    """[start, end] (seconds, relative to t[0]) when the sun is below the horizon at the animal."""
    if len(t) < 2 or t[-1] - t[0] > NIGHT_MAX_DAYS * 86400:
        return []
    ts = np.arange(t[0], t[-1] + 1, 300.0)
    el = sun_elevation_deg(ts, np.interp(ts, t, lat), np.interp(ts, t, lon))
    dark = el < -0.833
    edges = np.flatnonzero(np.diff(dark.astype(np.int8))) + 1
    bounds = np.r_[0, edges, len(ts)]
    return [[int(ts[a] - t[0]), int(min(ts[b - 1] + 300, t[-1]) - t[0])]
            for a, b in zip(bounds[:-1], bounds[1:]) if dark[a]]


# ---------- point selection ----------

def _pick(t, lon, lat, keep: list[int], n_max: int = MAX_POINTS) -> np.ndarray:
    """Indices: the shape (Douglas-Peucker) plus a time grid, so the replay keeps both form and pace."""
    n = len(t)
    if n <= n_max:
        return np.arange(n)
    span = max(np.ptp(lon), np.ptp(lat), 1e-3)
    pts = np.c_[lon, lat]
    tol = span / 2000
    shape_idx = np.arange(n)
    for _ in range(40):
        s = LineString(pts).simplify(tol, preserve_topology=False)
        sc = np.asarray(s.coords)
        if len(sc) <= n_max * 0.6:
            break
        tol *= 1.5
    # map simplified vertices back to fix indices (vertices are a subset of the input points)
    lookup = {(round(x, 9), round(y, 9)): i for i, (x, y) in enumerate(pts)}
    shape_idx = np.array(sorted({lookup.get((round(x, 9), round(y, 9)), 0) for x, y in sc}))
    grid = np.searchsorted(t, np.linspace(t[0], t[-1], int(n_max * 0.4)))
    idx = np.unique(np.r_[shape_idx, np.clip(grid, 0, n - 1), keep, 0, n - 1])
    if len(idx) > n_max:
        inner = np.setdiff1d(idx, np.r_[keep, 0, n - 1])
        drop = inner[np.linspace(0, len(inner) - 1, len(idx) - n_max).astype(int)]
        idx = np.setdiff1d(idx, drop)
    return idx


def _coarse_fixes(t, lon, lat) -> np.ndarray:
    """First fix of each new 0.1 degree cell."""
    clon, clat = np.round(lon / COARSE_DEG), np.round(lat / COARSE_DEG)
    return np.flatnonzero(np.r_[True, (np.diff(clon) != 0) | (np.diff(clat) != 0)])


# ---------- the track file ----------

def track_file(rec_id: str, cat_key: str, unit: str, value: float, tr: Track, t0: float, t1: float,
               view: MapView, wrap: bool, coarse: bool) -> dict:
    a, b, i0, i1 = window(cat_key, tr, t0, t1)
    sl = slice(a, b + 1)
    t, lat = tr.t[sl], tr.lat[sl]
    lon = np.where(tr.lon[sl] < 0, tr.lon[sl] + 360, tr.lon[sl]) if wrap else tr.lon[sl]
    h = tr.h[sl]
    brk_full = tr.brk[a:b]
    km_full = cumulative_km(lon, lat, brk_full)
    e0, e1 = i0 - a, i1 - a
    t_cross = gate_time(tr, cat_key, i0)
    flyer = tr.animal.taxon.group in (Group.BIRD, Group.BAT)
    has_h = cat_key == "highest-flight" or (flyer and np.isfinite(h).mean() > 0.5)

    if coarse:
        sel = _coarse_fixes(t, lon, lat)
        if len(sel) > MAX_POINTS:
            sel = sel[np.linspace(0, len(sel) - 1, MAX_POINTS).astype(int)]
        t_start = math.floor(t[0] / COARSE_TQ_S) * COARSE_TQ_S
        ts = np.round(t[sel] / COARSE_TQ_S) * COARSE_TQ_S
        ts = np.maximum.accumulate(ts)
        ok = np.r_[True, np.diff(ts) > 0]   # one point per time step
        sel, ts = sel[ok], ts[ok]
        if len(sel) < 2:  # the whole replay fits in one cell or one time step: show start and end
            sel = np.array([0, len(t) - 1])
            ts = np.array([t_start, max(t_start + COARSE_TQ_S, round(t[-1] / COARSE_TQ_S) * COARSE_TQ_S)], float)
        plon = np.round(np.round(lon[sel] / COARSE_DEG) * COARSE_DEG, 1)
        plat = np.round(np.round(lat[sel] / COARSE_DEG) * COARSE_DEG, 1)
        brk_legs = [k for k in range(len(sel) - 1) if brk_full[sel[k]:sel[k + 1]].any()]
        raw_km = cumulative_km(plon, plat, np.isin(np.arange(len(sel) - 1), brk_legs))
        eff_km = raw_km[np.searchsorted(sel, e1, side="right") - 1] - raw_km[np.searchsorted(sel, e0, side="right") - 1]
        scale = float(value / eff_km) if cat_key in DISTANCE and eff_km > 0 else 1.0
        km, kmh = derive(ts, plon, plat, brk_legs, scale)
        rel_t = (ts - t_start).astype(int)
        ph = np.round(h[sel], -1) if has_h else None
    else:
        sel = _pick(t, lon, lat, [e0, e1])
        t_start = int(t[0])
        rel_t = np.round(t[sel] - t_start).astype(int)
        plon, plat = np.round(lon[sel], 3), np.round(lat[sel], 3)
        km = np.round(km_full[sel], 1)
        kmh = np.round(speed_kmh(t, km_full)[sel], 0)
        brk_legs = [k for k in range(len(sel) - 1) if brk_full[sel[k]:sel[k + 1]].any()]
        scale = 1.0
        ph = np.round(h[sel], 0) if has_h else None

    ea = int(np.searchsorted(sel, e0, side="right") - 1)
    eb = int(min(np.searchsorted(sel, e1, side="left"), len(sel) - 1))
    nc = countries_so_far(tr, sel + a) if cat_key == "countries" else None
    src_lat = plat if coarse else lat[sel]
    src_lon = plon if coarse else lon[sel]
    abs_t = rel_t + t_start
    m = value_so_far(cat_key, value, abs_t.astype(float), src_lon, src_lat, km, ph if ph is not None else np.full(len(sel), np.nan),
                     ea, t_cross, nc)
    t_zero = (t_cross if t_cross is not None else float(t[e0])) - t_start
    if coarse:
        t_zero = round(t_zero / COARSE_TQ_S) * COARSE_TQ_S
    out = {
        "v": 1, "id": rec_id, "category": cat_key, "unit": unit, "value": round(value, 3),
        "t_start": int(t_start), "coarse": COARSE_DEG if coarse else None, "tq": COARSE_TQ_S if coarse else 1,
        "km_scale": round(scale, 6),
        "eff": [ea, eb], "t_zero": int(t_zero),
        "view": [round(view.lon0, 6), round(view.lat1, 6), round(view.kx, 6), round(view.scale, 6)],
        "t": rel_t.tolist(), "lon": plon.tolist(), "lat": plat.tolist(),
        "km": km.tolist(), "kmh": kmh.tolist(), "m": np.round(m, 3).tolist(), "brk": brk_legs,
        "night": night_spans(abs_t.astype(float), np.asarray(src_lon, float), np.asarray(src_lat, float)),
    }
    if ph is not None:
        out["h"] = [None if not np.isfinite(x) else int(x) for x in ph]
    return out


# ---------- races ----------

RACE_POINTS = 150


def race_runner(tf: dict, view: MapView | None) -> dict:
    t = np.asarray(tf["t"], float) - tf["t_zero"]
    ea, eb = tf["eff"]
    lo, hi = max(ea, 0), eb
    if tf["category"] in SEGMENT_BY_KEY:
        finish = tf["value"] * 3600
    else:
        finish = float(t[eb])
    idx = np.arange(lo, hi + 1)
    if len(idx) > RACE_POINTS:
        idx = np.unique(np.r_[idx[np.linspace(0, len(idx) - 1, RACE_POINTS).astype(int)], lo, hi])
    t = t[idx]
    if tf["category"] == "fastest-hour":
        km = np.maximum(np.asarray(tf["km"])[idx] - tf["km"][ea], 0)
        measure = km * (tf["value"] / km[-1] if km[-1] > 0 else 0.0)
        t, finish = t * (3600 / finish if finish > 0 else 1.0), 3600.0
    else:
        measure = np.asarray(tf["m"])[idx]
    out = {"t": np.round(t).astype(int).tolist(), "d": np.round(measure, 1).tolist(), "finish": int(round(finish))}
    if view is not None:
        x, y = view.xy(np.asarray(tf["lon"])[idx], np.asarray(tf["lat"])[idx])
        out |= {"x": np.round(x, 1).tolist(), "y": np.round(y, 1).tolist()}
    return out

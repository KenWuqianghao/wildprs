"""Category registry. Each category is a pure function Track -> list[Effort] (the best effort
of that track, or nothing) plus the facts the audit and the site need."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import geo
from .model import Effort, Group, Track, haversine_km
from .segments import SEGMENTS, Segment, fastest_passage

# Families that can rest on water: a sea crossing is not a non-stop flight for them.
WATER_RESTING = {"Laridae", "Alcidae", "Stercorariidae", "Procellariidae", "Diomedeidae", "Hydrobatidae",
                 "Oceanitidae", "Spheniscidae", "Sulidae", "Phalacrocoracidae", "Phaethontidae", "Anatidae",
                 "Gaviidae", "Podicipedidae", "Pelecanidae", "Anhingidae", "Fregatidae"}
FLYERS = {Group.BIRD, Group.BAT}


@dataclass(frozen=True)
class Category:
    key: str
    title: str
    rule: str                       # one-line definition shown on the site
    unit: str                       # km, km/h, m, deg, count, h
    better: int                     # +1: higher wins, -1: lower wins
    sensors: frozenset[str]
    fn: Callable[[Track], list[Effort]]
    hook: str                       # sentence template, see site.hook()
    profile: str                    # speed | height | cumulative | latitude
    care: bool = False              # no automatic pass beyond review_beyond (in the 'better' direction)
    review_beyond: dict[Group, float] = field(default_factory=dict)
    artefact_beyond: float | None = None
    groups: frozenset[Group] | None = None  # None: all groups

    def eligible(self, tr: Track) -> bool:
        return tr.sensor in self.sensors and (self.groups is None or tr.animal.taxon.group in self.groups)


def _window_distance(tr: Track, window_s: float, min_fixes: int, max_gap_s: float):
    if len(tr) < min_fixes:
        return None
    cum = np.r_[0, np.cumsum(tr.legs_km)]
    bad = np.r_[0, np.cumsum(tr.brk | (tr.dt > max_gap_s))]
    i = np.arange(len(tr))
    j = np.searchsorted(tr.t, tr.t + window_s, side="right") - 1
    ok = (j - i + 1 >= min_fixes) & (bad[j] - bad[i] == 0)
    if not ok.any():
        return None
    val = np.where(ok, cum[j] - cum[i], -1.0)
    k = int(np.argmax(val))
    return k, int(j[k]), float(val[k]), int(j[k] - k + 1)


def distance_in(window_s: float, min_fixes: int, max_gap_s: float, key: str):
    def fn(tr: Track) -> list[Effort]:
        r = _window_distance(tr, window_s, min_fixes, max_gap_s)
        if not r:
            return []
        i, j, v, n = r
        return [Effort(key, v, i, j, n)]
    return fn


def lifetime(tr: Track) -> list[Effort]:
    """Distance along fixes at least one hour apart: dense GPS jitter at rest does not count."""
    if len(tr) < 10:
        return []
    sel = np.flatnonzero(np.r_[True, np.diff(np.floor(tr.t / 3600)) > 0])
    brk_cum = np.r_[0, np.cumsum(tr.brk)]
    legs = haversine_km(tr.lat[sel[:-1]], tr.lon[sel[:-1]], tr.lat[sel[1:]], tr.lon[sel[1:]])
    legs = np.where(brk_cum[sel[1:]] - brk_cum[sel[:-1]] == 0, legs, 0.0)
    return [Effort("lifetime", float(legs.sum()), 0, len(tr) - 1, len(sel),
                   {"days": float((tr.t[-1] - tr.t[0]) / 86400)})]


def round_trip(tr: Track, home_km: float = 5.0) -> list[Effort]:
    if len(tr) < 10:
        return []
    dh = haversine_km(tr.lat[0], tr.lon[0], tr.lat, tr.lon)
    home = np.flatnonzero(dh < home_km)
    last_home = int(home[-1])
    if last_home < 2:
        return []
    far = int(np.argmax(dh[: last_home + 1]))
    if far == 0 or dh[far] < 2 * home_km:
        return []
    return [Effort("round-trip", float(dh[far]), 0, last_home, last_home + 1,
                   {"far_index": far, "far_lat": float(tr.lat[far]), "far_lon": float(tr.lon[far]),
                    "days": float((tr.t[last_home] - tr.t[0]) / 86400)})]


def extreme_latitude(sign: int, key: str, plateau: int = 3):
    def fn(tr: Track) -> list[Effort]:
        if len(tr) < plateau:
            return []
        x = np.lib.stride_tricks.sliding_window_view(sign * tr.lat, plateau).min(axis=1)
        i = int(np.argmax(x))
        return [Effort(key, float(sign * x[i]), i, i + plateau - 1, plateau)]
    return fn


def countries(tr: Track, min_fixes: int = 3) -> list[Effort]:
    if len(tr) < 10:
        return []
    c = geo.country(tr.lat, tr.lon)
    names, counts = np.unique(c[c != ""], return_counts=True)
    names = sorted(names[counts >= min_fixes].tolist())
    if not names:
        return []
    return [Effort("countries", float(len(names)), 0, len(tr) - 1, len(tr), {"list": names})]


def sea_crossing(tr: Track, min_sea_fixes: int = 3, max_gap_s: float = 24 * 3600) -> list[Effort]:
    """Longest run of at-sea fixes between two land fixes."""
    if tr.animal.taxon.family in WATER_RESTING or len(tr) < 3:
        return []
    land = geo.on_land(tr.lat, tr.lon)
    cum = np.r_[0, np.cumsum(tr.legs_km)]
    best = None
    edges = np.flatnonzero(np.diff(land.astype(np.int8)))  # index before each land/sea change
    for a, b in zip(edges[:-1], edges[1:]):
        if land[a] and not land[a + 1]:          # land a -> sea a+1 .. b -> land b+1
            s, e = int(a), int(b) + 1
            if e - s - 1 < min_sea_fixes or tr.brk[s:e].any() or tr.dt[s:e].max() > max_gap_s:
                continue
            km = float(cum[e] - cum[s])
            if best is None or km > best.value:
                best = Effort("sea-crossing", km, s, e, e - s - 1, {"max_gap_h": float(tr.dt[s:e].max() / 3600)})
    return [best] if best else []


def fastest_hour(tr: Track, min_fixes: int = 4) -> list[Effort]:
    if len(tr) < min_fixes:
        return []
    cum = np.r_[0, np.cumsum(tr.legs_km)]
    bad = np.r_[0, np.cumsum(tr.brk)]
    i = np.arange(len(tr))
    j = np.minimum(np.searchsorted(tr.t, tr.t + 3600, side="left"), len(tr) - 1)
    span = tr.t[j] - tr.t[i]
    # A bad fix makes an out-and-back spike. Extend the window by one fix on each side (so that a
    # window that starts or ends on the spike also shows it); the straight line from start to end
    # must then be at least half the distance along the track.
    straight_km = haversine_km(tr.lat[i], tr.lon[i], tr.lat[j], tr.lon[j])
    i1, j1 = np.maximum(i - 1, 0), np.minimum(j + 1, len(tr) - 1)
    straight_ext = haversine_km(tr.lat[i1], tr.lon[i1], tr.lat[j1], tr.lon[j1])
    ok = (span >= 3600) & (span <= 7200) & (j - i + 1 >= min_fixes) & (bad[j] - bad[i] == 0) \
        & (straight_ext >= 0.5 * (cum[j1] - cum[i1]))
    if not ok.any():
        return []
    v = np.where(ok, (cum[j] - cum[i]) / np.maximum(span, 1) * 3600, -1.0)
    k = int(np.argmax(v))
    straight = straight_km[k] / span[k] * 3600
    return [Effort("fastest-hour", float(v[k]), k, int(j[k]), int(j[k] - k + 1),
                   {"straight_kmh": float(straight)})]


def highest_flight(tr: Track, plateau: int = 3) -> list[Effort]:
    """Highest height held by 3 fixes in a row, while moving at 2 m/s or more, with climb
    rates of 5 m/s or less from 30 minutes before to 30 minutes after, and fixes at most 30 minutes apart."""
    h = tr.h
    if len(h) < plateau or np.isnan(h).all():
        return []
    hh = np.lib.stride_tricks.sliding_window_view(np.nan_to_num(h, nan=-1e9), plateau).min(axis=1)
    dt = np.maximum(tr.dt, 1)
    climb = np.abs(np.diff(np.nan_to_num(h, nan=0))) / dt
    horiz = tr.legs_km * 1000 / dt
    bad = np.r_[True, (climb > 5) | (tr.dt > 1800) | tr.brk]
    bad_w = np.lib.stride_tricks.sliding_window_view(bad, plateau)[:, 1:].any(axis=1)
    # A GPS height error is often a block of fixes kilometres too high: a jump up, then a smooth decay
    # back to the true height (seen on e-obs tags after a cold start). No leg within 30 minutes of the
    # window may climb or sink faster than 5 m/s.
    steep_t = tr.t[1:][climb > 5]
    n_steep = np.searchsorted(steep_t, tr.t[plateau - 1:] + 1800, side="right") - \
        np.searchsorted(steep_t, tr.t[:len(tr.t) - plateau + 1] - 1800, side="left")
    bad_w |= n_steep > 0
    moving = np.lib.stride_tricks.sliding_window_view(np.r_[0, horiz], plateau)[:, 1:].mean(axis=1) >= 2
    hh = np.where(bad_w | ~moving, -1e9, hh)
    i = int(np.argmax(hh))
    if hh[i] <= 0:
        return []
    k = i + int(np.argmax(h[i:i + plateau]))
    return [Effort("highest-flight", float(hh[i]), i, i + plateau - 1, plateau,
                   {"peak_lat": float(tr.lat[k]), "peak_lon": float(tr.lon[k]), "raw_max_m": float(np.nanmax(h))})]


def segment_fn(seg: Segment):
    def fn(tr: Track) -> list[Effort]:
        r = fastest_passage(tr, seg)
        if r is None:
            return []
        hours, k0, k1, between, max_gap, direction = r
        return [Effort(seg.key, hours, k0, k1, between, {"max_gap_h": max_gap, "direction": direction})]
    return fn


GPS_ARGOS = frozenset({"gps", "argos"})
GPS = frozenset({"gps"})
BIRD_SPEED_REVIEW = {Group.BIRD: 1500.0}

CATEGORIES: tuple[Category, ...] = (
    Category("sea-crossing", "Longest non-stop sea crossing",
             "Land to land with every fix in between over sea. Only birds and bats that cannot rest on water.",
             "km", +1, GPS_ARGOS, sea_crossing,
             "Flew {value} km over open water without a stop, in {duration}.", "speed",
             groups=frozenset(FLYERS), artefact_beyond=13000),
    Category("biggest-week", "Biggest week", "Longest distance along the track in any 7 days, with no gap over 24 hours.",
             "km", +1, GPS_ARGOS, distance_in(7 * 86400, 7, 24 * 3600, "biggest-week"),
             "Covered {value} km in one week, from {start_place} to {end_place}.", "cumulative",
             review_beyond={Group.BIRD: 10000.0}, artefact_beyond=13000),
    Category("lifetime", "Lifetime distance",
             "Distance along the whole GPS track, between fixes at least one hour apart.",
             "km", +1, GPS, lifetime, "Travelled {value} km in {duration} of tracking.", "cumulative",
             artefact_beyond=300000),
    Category("round-trip", "Farthest round trip",
             "Farthest distance from the first fix on a trip that comes back to within 5 km of it.",
             "km", +1, GPS_ARGOS, round_trip, "Went {value} km from where it started, and came back.", "latitude",
             artefact_beyond=20000),
    Category("farthest-north", "Farthest north", "Most northern latitude held by 3 fixes in a row.",
             "deg", +1, GPS_ARGOS, extreme_latitude(+1, "farthest-north"), "Reached {value} {unit}, {end_where}.",
             "latitude"),
    Category("farthest-south", "Farthest south", "Most southern latitude held by 3 fixes in a row.",
             "deg", -1, GPS_ARGOS, extreme_latitude(-1, "farthest-south"), "Reached {value} {unit}, {end_where}.",
             "latitude"),
    Category("countries", "Most countries", "Countries with 3 or more fixes inside their borders.",
             "count", +1, GPS_ARGOS, countries, "Visited {value} {unit} in {duration}.", "cumulative",
             artefact_beyond=40),
    Category("biggest-day", "Biggest day", "Longest distance along the track in any 24 hours, with no gap over 6 hours.",
             "km", +1, GPS_ARGOS, distance_in(86400, 6, 6 * 3600, "biggest-day"),
             "Covered {value} km in 24 hours, from {start_place} to {end_place}.", "speed",
             care=True, review_beyond=BIRD_SPEED_REVIEW | {Group.LAND_MAMMAL: 150.0, Group.MARINE: 250.0,
                                                          Group.BAT: 600.0, Group.TURTLE: 120.0},
             artefact_beyond=2600),
    Category("fastest-hour", "Fastest hour", "Highest average speed along the track over at least one hour.",
             "km/h", +1, GPS, fastest_hour, "Held {value} km/h for a full hour, {start_where}.", "speed",
             care=True, review_beyond={Group.BIRD: 100.0, Group.BAT: 60.0, Group.LAND_MAMMAL: 30.0,
                                      Group.MARINE: 15.0, Group.TURTLE: 8.0}, artefact_beyond=160),
    Category("highest-flight", "Highest flight",
             "Highest height above sea level held by 3 GPS fixes in a row, while flying, above the terrain below.",
             "m", +1, GPS, highest_flight, "Flew at {value} m above sea level, {agl} m above the ground below.",
             "height", care=True, review_beyond={Group.BIRD: 6000.0, Group.BAT: 3000.0}, artefact_beyond=9000,
             groups=frozenset(FLYERS)),
) + tuple(
    Category(s.key, s.title, f"Fastest passage between two lines: {s.span}.", "h", -1, GPS_ARGOS, segment_fn(s),
             "Crossed the {segment} in {value} {unit}.", "speed", care=s.care,
             review_beyond={Group.BIRD: 24.0} if s.care else {})
    for s in SEGMENTS
)

BY_KEY = {c.key: c for c in CATEGORIES}
SEGMENT_BY_KEY = {s.key: s for s in SEGMENTS}


def efforts(tr: Track) -> list[Effort]:
    return [e for c in CATEGORIES if c.eligible(tr) for e in c.fn(tr)]

"""Segments: two gate lines. A crossing of gate A followed by gate B (or B then A) is one effort;
the value is the time between the two crossings. Gates are ((lon1, lat1), (lon2, lat2))."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .model import Track

Gate = tuple[tuple[float, float], tuple[float, float]]


@dataclass(frozen=True)
class Segment:
    key: str
    title: str
    gate_a: Gate
    gate_b: Gate
    span: str  # plain description of the two gates
    care: bool = False


SEGMENTS: tuple[Segment, ...] = (
    Segment("strait-of-gibraltar", "Strait of Gibraltar", ((-6.3, 36.2), (-5.0, 36.2)), ((-6.3, 35.7), (-5.0, 35.7)),
            "36.2°N to 35.7°N, between 6.3°W and 5.0°W"),
    Segment("bosphorus-to-sinai", "Bosphorus to Sinai", ((26.0, 41.5), (30.0, 41.5)), ((32.0, 29.5), (35.5, 29.5)),
            "41.5°N (26°E to 30°E) to 29.5°N (32°E to 35.5°E)"),
    Segment("mediterranean", "Mediterranean", ((-1.0, 43.5), (28.0, 43.5)), ((-1.0, 36.8), (28.0, 36.8)),
            "43.5°N to 36.8°N, between 1°W and 28°E"),
    Segment("alps", "Alps", ((5.0, 47.5), (16.0, 47.5)), ((5.0, 45.5), (16.0, 45.5)),
            "47.5°N to 45.5°N, between 5°E and 16°E"),
    Segment("tropics-of-africa", "Tropics of Africa", ((-20.0, 10.0), (45.0, 10.0)), ((-20.0, -10.0), (45.0, -10.0)),
            "10°N to 10°S, between 20°W and 45°E"),
    Segment("sahara", "Sahara", ((-17.5, 30.0), (33.0, 30.0)), ((-17.5, 16.0), (33.0, 16.0)),
            "30°N to 16°N, between 17.5°W and 33°E", care=True),
)

MAX_SEGMENT_DAYS = 60
MAX_GAP_H = 12
MIN_FIXES_BETWEEN = 2


def gate_crossings(tr: Track, gate: Gate) -> tuple[np.ndarray, np.ndarray]:
    """Leg indices k (fix k -> k+1) that cross the gate, and the interpolated crossing times."""
    (x1, y1), (x2, y2) = gate
    px, py, qx, qy = tr.lon[:-1], tr.lat[:-1], tr.lon[1:], tr.lat[1:]

    def side(ax, ay, bx, by, cx, cy):
        return np.sign((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
    hit = ((np.abs(qx - px) < 180) & ~tr.brk
           & (side(px, py, qx, qy, x1, y1) != side(px, py, qx, qy, x2, y2))
           & (side(x1, y1, x2, y2, px, py) != side(x1, y1, x2, y2, qx, qy))
           & (side(x1, y1, x2, y2, px, py) != 0))
    k = np.flatnonzero(hit)
    a3 = (x2 - x1) * (py[k] - y1) - (y2 - y1) * (px[k] - x1)
    a4 = (x2 - x1) * (qy[k] - y1) - (y2 - y1) * (qx[k] - x1)
    frac = a3 / (a3 - a4)
    return k, tr.t[k] + frac * (tr.t[k + 1] - tr.t[k])


def fastest_passage(tr: Track, seg: Segment):
    """(hours, k0, k1, fixes_between, max_gap_h, direction) of the fastest passage, or None."""
    if len(tr) < 3:
        return None
    ka, ta = gate_crossings(tr, seg.gate_a)
    kb, tb = gate_crossings(tr, seg.gate_b)
    events = sorted([(t, "a", k) for k, t in zip(ka, ta)] + [(t, "b", k) for k, t in zip(kb, tb)])
    best = None
    for (t0, g0, k0), (t1, g1, k1) in zip(events, events[1:]):
        if g0 == g1 or not (0 < t1 - t0 <= MAX_SEGMENT_DAYS * 86400):
            continue
        between = int(k1 - k0)
        span = slice(int(k0), int(k1) + 1)
        if between < MIN_FIXES_BETWEEN or tr.brk[span].any():
            continue
        max_gap = float(np.diff(tr.t[int(k0):int(k1) + 2]).max() / 3600)
        if max_gap > MAX_GAP_H:
            continue
        hours = (t1 - t0) / 3600
        if best is None or hours < best[0]:
            best = (hours, int(k0), int(k1) + 1, between, max_gap, "north to south" if
                    (g0 == "a") == (np.mean(seg.gate_a, axis=0)[1] > np.mean(seg.gate_b, axis=0)[1]) else "south to north")
    return best

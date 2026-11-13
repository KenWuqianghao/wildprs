"""One test per category on a synthetic track with a known answer."""
import numpy as np
import pytest

from conftest import track
from wildprs.categories import BY_KEY, SEGMENT_BY_KEY
from wildprs.model import Taxon, haversine_km
from wildprs.segments import gate_crossings

KM_PER_DEG = 111.195


def best(key, tr):
    (e,) = BY_KEY[key].fn(tr)
    return e


def test_biggest_day_is_the_fastest_24_hours(straight):
    t, lat, lon = straight
    lat = lat.copy()
    fast = (t >= 86400) & (t < 2 * 86400)
    lat[t >= 86400] += np.cumsum(fast[t >= 86400]) * 600 * 10 / 3600 / KM_PER_DEG  # doubled speed on day 2
    e = best("biggest-day", track(t, lat, lon))
    assert e.value == pytest.approx(480, rel=0.01)


def test_biggest_week_sums_seven_days():
    t = np.arange(0, 10 * 86400, 3600)
    lat = 10 + t / 86400 * 100 / KM_PER_DEG  # 100 km per day
    assert best("biggest-week", track(t, lat, np.zeros(len(t)))).value == pytest.approx(700, rel=0.01)


def test_lifetime_ignores_jitter_inside_an_hour():
    t = np.arange(0, 48 * 3600, 300)
    lat = 0 + (t // 3600) * 1 / KM_PER_DEG + np.where(np.arange(len(t)) % 2, 0.0005, 0)  # 1 km/h plus 55 m jitter
    assert best("lifetime", track(t, lat, np.zeros(len(t)))).value == pytest.approx(47, abs=1)


def test_round_trip_is_the_farthest_point_before_return():
    t = np.arange(0, 21 * 86400, 86400)
    lat = 30 + np.r_[np.arange(11), np.arange(9, -1, -1)] * 1.0
    e = best("round-trip", track(t, lat, np.zeros(len(t))))
    assert e.value == pytest.approx(10 * KM_PER_DEG, rel=0.01)


def test_farthest_north_needs_three_fixes():
    lat = np.array([70, 71, 79, 72, 72.5, 72.2, 71])
    e = best("farthest-north", track(np.arange(7) * 3600, lat, np.zeros(7)))
    assert e.value == pytest.approx(72.0)


def test_farthest_south_needs_three_fixes():
    lat = -np.array([60, 61, 69, 62, 62.5, 62.2, 61])
    e = best("farthest-south", track(np.arange(7) * 3600, lat, np.zeros(7)))
    assert e.value == pytest.approx(-62.0)


def test_countries_counts_three_fixes_each():
    # 5 fixes in France, 5 in Spain, 2 in Portugal
    lat = [46.5] * 5 + [40.0] * 5 + [39.5] * 2
    lon = [2.5] * 5 + [-3.7] * 5 + [-8.0] * 2
    e = best("countries", track(np.arange(12) * 3600, lat, lon))
    assert e.value == 2 and e.evidence["list"] == ["France", "Spain"]


def test_sea_crossing_runs_land_to_land():
    # Portugal coast to the Azores is about 1,400 km of sea; fixes every 2 hours
    lon = np.linspace(-9.2, -25.6, 30)
    lat = np.linspace(38.7, 37.8, 30)
    tr = track(np.arange(30) * 7200, lat, lon)
    e = best("sea-crossing", tr)
    assert e.value == pytest.approx(haversine_km(38.7, -9.2, 37.8, -25.6), rel=0.03)


def test_sea_crossing_skips_birds_that_rest_on_water():
    gull = Taxon("Larus fuscus", "Aves", "Charadriiformes", "Laridae", "Gull")
    lon = np.linspace(-9.2, -25.6, 30)
    tr = track(np.arange(30) * 7200, np.linspace(38.7, 37.8, 30), lon, taxon=gull)
    assert BY_KEY["sea-crossing"].fn(tr) == []


def test_fastest_hour_is_mean_speed_over_an_hour():
    t = np.arange(0, 6 * 3600, 300)
    speed = np.where((t >= 7200) & (t < 10800), 80.0, 20.0)  # km/h
    lat = np.r_[0, np.cumsum(speed[:-1] * 300 / 3600)] / KM_PER_DEG
    assert best("fastest-hour", track(t, lat, np.zeros(len(t)))).value == pytest.approx(80, rel=0.02)


def test_fastest_hour_ignores_an_out_and_back_spike():
    t = np.arange(0, 6 * 3600, 600.0)
    lat = np.arange(len(t)) * 1 / KM_PER_DEG  # 6 km/h
    lat[10] += 10 / KM_PER_DEG  # one fix 10 km off the track
    assert best("fastest-hour", track(t, lat, np.zeros(len(t)))).value == pytest.approx(6, rel=0.02)


def test_highest_flight_holds_for_three_fixes():
    t = np.arange(20) * 300
    lat = np.arange(20) * 0.02  # 7 m/s
    h = np.full(20, 200.0)
    h[2:6] = [500, 520, 510, 450]
    h[17] = 3000  # one height spike, reached at 9 m/s
    assert best("highest-flight", track(t, lat, np.zeros(20), h=h)).value == pytest.approx(500)


def test_highest_flight_ignores_a_decaying_block_after_a_jump():
    t = np.arange(30) * 300
    lat = np.arange(30) * 0.02
    h = np.full(30, 300.0)
    h[10:15] = [9000, 8800, 8600, 8400, 8200]  # jump up 8.7 km in 5 min, then a smooth decay
    assert best("highest-flight", track(t, lat, np.zeros(30), h=h)).value == pytest.approx(300)


def test_segment_time_is_between_gate_crossings():
    seg = SEGMENT_BY_KEY["strait-of-gibraltar"]
    t = np.arange(8) * 1800.0
    lat = np.linspace(36.5, 35.4, 8)  # crosses 36.2 N and 35.7 N
    tr = track(t, lat, np.full(8, -5.6))
    assert len(gate_crossings(tr, seg.gate_a)[0]) == 1 and len(gate_crossings(tr, seg.gate_b)[0]) == 1
    # 0.5 degrees of latitude at 1.1 degrees per 3.5 hours
    assert best("strait-of-gibraltar", tr).value == pytest.approx(0.5 / 1.1 * 3.5, rel=0.01)

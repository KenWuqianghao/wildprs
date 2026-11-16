import numpy as np
import pytest

from conftest import STORK, track
from wildprs import cartography as C
from wildprs import replay, sitecheck
from wildprs.categories import BY_KEY
from wildprs.model import Taxon

VULTURE = Taxon("Neophron percnopterus", "Aves", "Accipitriformes", "Accipitridae", "Egyptian vulture", "EN")


def _view(tr):
    return C.frame(tr.lon, tr.lat, 1200, 675)


def test_sensitive_track_file_holds_only_coarse_positions_and_times():
    t = 1.6e9 + np.arange(0, 2 * 86400, 300.0)
    tr = track(t, 40 + t / 86400 * 2.5 - 1.6e9 / 86400 * 2.5, np.full(len(t), 20.0), taxon=VULTURE)
    (e,) = BY_KEY["biggest-day"].fn(tr)
    tf = replay.track_file("x", "biggest-day", "km", e.value, tr, tr.t[e.i0], tr.t[e.i1], _view(tr), False, coarse=True)
    assert sitecheck.track_errors(tf, sensitive=True) == []
    assert tf["m"][tf["eff"][1]] == pytest.approx(e.value, rel=1e-3)
    exact = dict(tf, t=[x + 7 for x in tf["t"]])
    assert "sensitive track has times off the 15-minute grid" in sitecheck.track_errors(exact, sensitive=True)
    moved = dict(tf, lat=[x + 0.0123 for x in tf["lat"]])
    assert "sensitive track has positions off the 0.1 degree grid" in sitecheck.track_errors(moved, sensitive=True)


def test_race_runners_start_at_their_first_gate_and_finish_at_their_record_time():
    def crossing(t_offset, kmh):
        t = t_offset + np.arange(0, 5 * 3600, 300.0)
        lat = 36.5 - (t - t_offset) / 3600 * kmh / 111.2
        tr = track(t, lat, np.full(len(t), -5.6), taxon=STORK)
        (e,) = BY_KEY["strait-of-gibraltar"].fn(tr)
        tf = replay.track_file("x", e.category, "h", e.value, tr, tr.t[e.i0], tr.t[e.i1], _view(tr), False, False)
        return e.value, replay.race_runner(tf, _view(tr))

    fast_h, fast = crossing(0, 60)
    slow_h, slow = crossing(5e6, 30)
    assert slow_h == pytest.approx(2 * fast_h, rel=0.01)
    for hours, rn in ((fast_h, fast), (slow_h, slow)):
        assert rn["finish"] == pytest.approx(hours * 3600, abs=5)
        assert rn["t"][0] <= 0 < rn["t"][1]          # the race clock starts at the first gate
        assert rn["t"][-1] >= rn["finish"]


def test_fastest_hour_runner_ends_after_one_hour_at_the_distance_of_its_record_speed():
    t = 1.6e9 + np.arange(0, 4 * 3600, 1500.0)
    tr = track(t, 40 + (t - t[0]) / 3600 * 60 / 111.2, np.full(len(t), 20.0), taxon=STORK)
    (e,) = BY_KEY["fastest-hour"].fn(tr)
    assert tr.t[e.i1] - tr.t[e.i0] == 4500
    tf = replay.track_file("x", e.category, "km/h", e.value, tr, tr.t[e.i0], tr.t[e.i1], _view(tr), False, False)
    rn = replay.race_runner(tf, None)
    assert rn["finish"] == 3600 and rn["t"][-1] == 3600
    assert rn["d"][-1] == pytest.approx(e.value, abs=0.1)
    assert "x" not in rn

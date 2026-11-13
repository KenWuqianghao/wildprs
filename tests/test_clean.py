"""One test per cleaning rule: a synthetic track with one planted error."""
import numpy as np

from conftest import STORK, clean, fixes_frame
from wildprs.clean import Deployment
from wildprs.model import Taxon


def walk(n=50, step_s=600):
    t = np.arange(n) * step_s
    return t, 50 + np.arange(n) * 0.001, np.full(n, 8.0)


def test_pet_is_dropped():
    t, lat, lon = walk()
    cat = Taxon("Felis catus", "Mammalia", "Carnivora", "Felidae", "Cat")
    assert clean(fixes_frame(t, lat, lon, taxon=["Felis catus"] * len(t)), taxon=cat) == []


def test_relocated_animal_is_dropped():
    t, lat, lon = walk()
    assert clean(fixes_frame(t, lat, lon), dep=Deployment(manipulation=frozenset({"relocated"}))) == []


def test_geolocator_fixes_are_dropped():
    t, lat, lon = walk()
    assert clean(fixes_frame(t, lat, lon, sensor=["other"] * len(t))) == []


def test_flagged_outlier_is_dropped():
    t, lat, lon = walk()
    flags = np.zeros(len(t), np.int8)
    flags[10] = 1
    (tr,) = clean(fixes_frame(t, lat, lon, flags=flags))
    assert t[10] not in tr.t and len(tr) == len(t) - 1


def test_poor_gps_fix_is_dropped():
    t, lat, lon = walk()
    hdop = np.ones(len(t))
    hdop[5] = 30
    (tr,) = clean(fixes_frame(t, lat, lon, hdop=hdop))
    assert t[5] not in tr.t


def test_poor_argos_class_is_dropped():
    t, lat, lon = walk()
    lc = ["2"] * len(t)
    lc[7] = "B"
    (tr,) = clean(fixes_frame(t, lat, lon, sensor=["argos"] * len(t), lc=lc))
    assert t[7] not in tr.t and len(tr) == len(t) - 1


def test_fixes_outside_deployment_are_dropped():
    t, lat, lon = walk()
    (tr,) = clean(fixes_frame(t, lat, lon), dep=Deployment(on=float(t[3])))
    assert tr.t[0] == t[3]


def test_fixes_closer_than_two_minutes_are_thinned():
    t = np.r_[0, 30, 60, 600, 1200, 1800]
    (tr,) = clean(fixes_frame(t, 50 + t * 1e-6, np.full(6, 8.0)))
    assert list(tr.t) == [0, 600, 1200, 1800]


def test_out_and_back_excursion_is_dropped():
    t, lat, lon = walk(40)
    lat = lat.copy()
    lat[20:23] += 0.5  # 55 km out and back within 30 minutes
    (tr,) = clean(fixes_frame(t, lat, lon))
    assert not np.isin(t[20:23], tr.t).any()
    assert len(tr) == 37


def test_single_speed_spike_is_dropped():
    t, lat, lon = walk(40)
    lon = lon.copy()
    lon[15] += 3.0  # 200 km jump for one fix
    (tr,) = clean(fixes_frame(t, lat, lon))
    assert t[15] not in tr.t


def test_jump_splits_the_track():
    t, lat, lon = walk(40)
    lat = lat.copy()
    lat[20:] += 2.0  # the animal is 220 km away from fix 20 on: one leg at 370 m/s
    (tr,) = clean(fixes_frame(t, lat, lon))
    assert tr.brk.sum() == 1 and tr.brk[np.searchsorted(tr.t, t[20]) - 1]

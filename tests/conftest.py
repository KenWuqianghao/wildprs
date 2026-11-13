import numpy as np
import polars as pl
import pytest

from wildprs.clean import Deployment, clean_package
from wildprs.model import Animal, Taxon, Track

STORK = Taxon("Ciconia ciconia", "Aves", "Ciconiiformes", "Ciconiidae", "White stork", "LC")


def fixes_frame(t, lat, lon, **cols) -> pl.DataFrame:
    n = len(t)
    base = {"ind": ["a1"] * n, "taxon": ["Ciconia ciconia"] * n, "sensor": ["gps"] * n,
            "t": np.asarray(t, float), "lat": np.asarray(lat, float), "lon": np.asarray(lon, float),
            "h": np.full(n, np.nan), "vis": np.ones(n, bool), "flags": np.zeros(n, np.int8),
            "hdop": np.full(n, 1.0), "sats": np.full(n, 8.0), "lc": [""] * n}
    base.update(cols)
    return pl.DataFrame(base)


def clean(df: pl.DataFrame, taxon: Taxon = STORK, dep: Deployment | None = None):
    tracks, counts = clean_package(df, {"a1": dep} if dep else {}, lambda _n: taxon, 0)
    return tracks


def track(t, lat, lon, h=None, taxon: Taxon = STORK, sensor="gps") -> Track:
    t = np.asarray(t, float)
    return Track(Animal(0, "a1", taxon), sensor, t, np.asarray(lat, float), np.asarray(lon, float),
                 np.asarray(h if h is not None else np.full(len(t), np.nan), float), np.zeros(len(t) - 1, bool))


@pytest.fixture
def straight():
    """A walk north at 10 km/h with one fix per 10 minutes for 3 days."""
    t = np.arange(0, 3 * 86400, 600)
    lat = 40 + (t / 3600) * 10 / 111.2
    return t, lat, np.full(len(t), 10.0)

import numpy as np
import polars as pl

from conftest import STORK, track
from wildprs import config, pipeline
from wildprs.cartography import COARSE_DEG, sensitivity, simplify
from wildprs.clean import fingerprints
from wildprs.model import Taxon


def test_animal_in_two_packages_is_merged(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CLEAN", tmp_path / "clean")
    monkeypatch.setattr(config, "RECORDS", tmp_path / "records")
    config.CLEAN.mkdir()
    config.RECORDS.mkdir()
    t = np.arange(0, 30 * 86400, 600.0)
    rng = np.random.default_rng(1)
    lat, lon = 48 + rng.normal(0, 0.1, len(t)), 9 + rng.normal(0, 0.1, len(t))
    same = track(t, lat, lon)
    other = track(t, lat + 0.3, lon)
    fingerprints([same]).write_parquet(config.CLEAN / "022.fp.parquet")
    copy = fingerprints([same]).with_columns(pl.lit("Sierit (DER AN858)").alias("ind"))
    pl.concat([copy, fingerprints([other]).with_columns(pl.lit("b2").alias("ind"))]).write_parquet(
        config.CLEAN / "039.fp.parquet")
    ids = pipeline.identities()
    animal = dict(zip(zip(ids["pkg"], ids["ind"]), ids["animal"]))
    assert animal[(22, "a1")] == animal[(39, "Sierit (DER AN858)")]
    assert animal[(39, "b2")] != animal[(22, "a1")]


def test_short_track_with_the_same_id_in_two_packages_is_merged(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CLEAN", tmp_path / "clean")
    monkeypatch.setattr(config, "RECORDS", tmp_path / "records")
    config.CLEAN.mkdir()
    config.RECORDS.mkdir()
    t = np.arange(0, 2 * 86400, 1800.0)  # 96 fixes: too few sampled keys for the 20-key rule
    rng = np.random.default_rng(2)
    bat = track(t, 0.3 + rng.normal(0, 0.1, len(t)), 32 + rng.normal(0, 0.1, len(t)))
    fp = fingerprints([bat]).with_columns(pl.lit("1608").alias("ind"))
    assert 2 <= fp.height < 20
    fp.write_parquet(config.CLEAN / "137.fp.parquet")
    fp.write_parquet(config.CLEAN / "271.fp.parquet")
    ids = pipeline.identities()
    assert ids.filter(pl.col("ind") == "1608")["animal"].n_unique() == 1


def test_threatened_taxon_gets_coarse_geometry():
    vulture = Taxon("Neophron percnopterus", "Aves", "Accipitriformes", "Accipitridae", "Egyptian vulture", "EN")
    t = np.arange(100) * 3600.0
    tr = track(t, 40 + np.arange(100) * 0.05, 20 + np.arange(100) * 0.033, taxon=vulture)
    assert sensitivity(tr, 10, 50) is not None
    lon, lat = simplify(tr.lon, tr.lat, coarse=True)
    assert np.allclose(np.round(lat / COARSE_DEG) * COARSE_DEG, lat)
    assert np.allclose(np.round(lon / COARSE_DEG) * COARSE_DEG, lon)


def test_record_ending_at_the_colony_gets_coarse_geometry():
    t = np.arange(100) * 3600.0
    lat = np.r_[np.linspace(40, 45, 50), np.linspace(45, 40, 50)]
    tr = track(t, lat, np.full(100, 5.0), taxon=STORK)
    assert sensitivity(tr, 20, 30) is None
    assert sensitivity(tr, 50, 99) is not None


def test_a_species_word_or_a_tag_code_is_not_shown_as_a_name():
    from wildprs.audit import display_name
    assert display_name("Curlew-4163152-Wursten", "", "Eurasian curlew") == ""
    assert display_name("Bat 5", "", "Common noctule") == ""
    assert display_name("Ich-2_tu96-3-16", "", "Loggerhead sea turtle") == ""
    assert display_name("x", "KO-PTT-66", "Upland sandpiper") == ""
    assert display_name("x", "Sara AU017 3941", "White stork") == "Sara"
    assert display_name("Thang Kaar Dorje (4014)", "", "Himalayan griffon vulture") == "Thang Kaar Dorje"
    assert display_name("Mal Julgada_O105", "", "White stork") == "Mal Julgada"

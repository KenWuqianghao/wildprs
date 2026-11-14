import json

import polars as pl
import pytest

from wildprs import art, config, site
from wildprs.model import ArtKind


@pytest.mark.parametrize("code, name", [
    ("cc0", "CC0 1.0"), ("pd", "Public domain"), ("cc-by-2.0", "CC BY 2.0"), ("cc-by-sa-3.0-de", "CC BY-SA 3.0 DE"),
    ("https://creativecommons.org/licenses/by/4.0/", "CC BY 4.0"),
    ("https://creativecommons.org/publicdomain/zero/1.0/", "CC0 1.0"),
])
def test_allowed_licences(code, name):
    assert art.licence(code).name == name


@pytest.mark.parametrize("code", ["cc-by-nc-2.0", "cc-by-nd-4.0", "cc-by-nc-sa-3.0", "gfdl", "", "arr",
                                  "https://creativecommons.org/licenses/by-nc/3.0/"])
def test_other_licences_are_rejected(code):
    with pytest.raises(art.ArtError):
        art.licence(code)


def test_left_facing_silhouette_is_mirrored_to_face_right():
    # A triangle with its point (the head) on the left.
    d, w, h, _ = art.normalise_svg('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 5 L10 0 L10 10 Z"/></svg>', "left")
    assert (w, h) == (100, 100)
    assert d.startswith("M100 50")


def test_manifest_is_read_per_taxon_and_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ART", tmp_path)
    entry = {"taxon": "Ciconia ciconia", "kind": "silhouette", "files": [{"path": "sil/c.svg", "crop": "full", "width": 81, "height": 100}],
             "licence": "CC BY 4.0", "licence_url": "https://creativecommons.org/licenses/by/4.0/", "creator": "A. Person",
             "source_url": "https://www.phylopic.org/images/x", "shows": "Ciconia ciconia", "change": "Recoloured and resized"}
    (tmp_path / "manifest.json").write_text(json.dumps([entry]))
    got = art.load()["Ciconia ciconia"][ArtKind.SILHOUETTE]
    assert got.files[0].path == "sil/c.svg" and got.creator == "A. Person"
    assert ArtKind.PHOTO not in art.load()["Ciconia ciconia"]


def test_site_stops_when_a_board_taxon_has_no_silhouette(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUDIT", tmp_path / "audit")
    monkeypatch.setattr(config, "ART", tmp_path / "art")
    config.AUDIT.mkdir()
    (config.AUDIT / "boards.json").write_text(json.dumps(
        {"boards": {"biggest-day": {"all": ["r1"]}}, "competitors": {"biggest-day": {"all": 1}}}))
    pl.DataFrame({"id": ["r1"], "taxon": ["Ciconia ciconia"], "state": ["credible"]}).write_parquet(
        config.AUDIT / "verdicts.parquet")
    with pytest.raises(SystemExit, match="no silhouette for \\['Ciconia ciconia'\\]"):
        site.build("https://example.org")

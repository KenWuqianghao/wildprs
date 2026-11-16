import json

import pytest

from wildprs import art, config


@pytest.mark.parametrize("code, name", [
    ("cc0", "CC0 1.0"), ("pd", "Public domain"), ("cc-by-2.0", "CC BY 2.0"), ("cc-by-sa-3.0-de", "CC BY-SA 3.0 DE"),
])
def test_allowed_licences(code, name):
    assert art.licence(code).name == name


@pytest.mark.parametrize("code", ["cc-by-nc-2.0", "cc-by-nd-4.0", "cc-by-nc-sa-3.0", "gfdl", "", "arr"])
def test_other_licences_are_rejected(code):
    with pytest.raises(art.ArtError):
        art.licence(code)


def test_manifest_gives_the_photo_of_each_taxon(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ART", tmp_path)
    entry = {"taxon": "Ciconia ciconia", "files": [{"path": "photo/c-wide-480.webp", "crop": "wide", "width": 480, "height": 320}],
             "licence": "CC BY 4.0", "licence_url": "https://creativecommons.org/licenses/by/4.0/", "creator": "A. Person",
             "source_url": "https://commons.wikimedia.org/wiki/File:C.jpg", "shows": "Ciconia ciconia", "change": "Cropped and resized"}
    (tmp_path / "manifest.json").write_text(json.dumps([entry]))
    got = art.load()
    assert list(got) == ["Ciconia ciconia"]
    assert got["Ciconia ciconia"].files[0].path == "photo/c-wide-480.webp" and got["Ciconia ciconia"].creator == "A. Person"

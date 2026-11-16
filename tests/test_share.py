import json
import re

import polars as pl
import pytest

from wildprs import config, share, site, sitecheck, taxa, zoo
from wildprs.model import SHARE_FORMATS

NO_MODEL = "Anas platyrhynchos"


def test_every_model_entry_is_found_and_has_a_curated_name():
    models = zoo.model_taxa(site.STATIC)
    assert {"Ciconia ciconia", "Canis", "Chelonoidis hoodensis", "Manta", "Aptenodytes forsteri"} <= models
    assert models <= set(taxa.curated_names())
    assert NO_MODEL not in models


def test_site_stops_when_a_board_taxon_has_no_model(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUDIT", tmp_path / "audit")
    config.AUDIT.mkdir()
    (config.AUDIT / "boards.json").write_text(json.dumps(
        {"boards": {"biggest-day": {"all": ["r1"]}}, "competitors": {"biggest-day": {"all": 1}}}))
    pl.DataFrame({"id": ["r1"], "taxon": [NO_MODEL], "state": ["credible"]}).write_parquet(config.AUDIT / "verdicts.parquet")
    with pytest.raises(SystemExit, match=rf"no 3D model for \['{NO_MODEL}'\]"):
        site.build("https://example.org")


def test_record_page_shows_and_links_the_file_of_every_share_format():
    rec = {"share_title": "Lifetime distance: 158,982 km. Leo, Turkey vulture"}
    html = site.share_block(rec, "https://example.org/r/r1/", video=False)
    assert re.findall(r'<img src="([^"]+)" width="1200" height="675"', html) == [f.file for f in SHARE_FORMATS]
    assert re.findall(r'href="([^"]+)" download', html) == [f.file for f in SHARE_FORMATS]
    assert "https://twitter.com/intent/tweet?text=Lifetime+distance%3A+158%2C982+km" in html
    assert set(share.POSTERS) == {f.key for f in SHARE_FORMATS}


def test_check_reports_a_record_without_one_of_the_share_images(tmp_path):
    (tmp_path / "card.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + (1200).to_bytes(4) + (675).to_bytes(4))
    assert sitecheck.share_errors(tmp_path) == [f"{tmp_path.name}: {f.file} missing or not 1200x675" for f in SHARE_FORMATS[1:]]

import pytest

from wildprs.categories import BY_KEY
from wildprs.site import animal_title, hook, lead_scope


@pytest.mark.parametrize("ind, common, title", [
    ("elephant 8", "African bush elephant", "African bush elephant 8"),
    ("sea lion 11", "California sea lion", "California sea lion 11"),
    ("Elephant-8", "African bush elephant", "African bush elephant 8"),
    ("Sealion 3", "California sea lion", "California sea lion Sealion 3"),
    ("2014CA-Bph-05838", "Blue whale", "Blue whale 2014CA-Bph-05838"),
])
def test_a_tag_id_does_not_repeat_the_common_name(ind, common, title):
    assert animal_title({"name": "", "ind": ind, "common": common}) == title


@pytest.mark.parametrize("taxon, lat, lon, place, sentence", [
    ("Caretta caretta", 40.1, 11.15, "", "Reached 40.1° N, over open water."),
    ("Wallabia bicolor", -38.56, 145.35, "", "Reached 40.1° N."),
    ("Halichoerus grypus", 45.5, -61.07, "", "Reached 40.1° N."),
    ("Ciconia ciconia", 40.1, -4.0, "Spain", "Reached 40.1° N, in Spain."),
])
def test_only_a_sea_animal_off_the_coast_is_over_open_water(taxon, lat, lon, place, sentence):
    r = {"taxon": taxon, "t0": 0, "t1": 3600, "place0": place, "place1": place,
         "lat0": lat, "lon0": lon, "lat1": lat, "lon1": lon}
    assert hook(BY_KEY["farthest-north"], r, "40.1°", "N") == sentence


def test_a_record_is_shown_in_the_scope_where_it_ranks_best():
    assert lead_scope({"all": 10, "marine-animals": 1}) == "marine-animals"
    assert lead_scope({"all": 1, "birds": 1}) == "all"

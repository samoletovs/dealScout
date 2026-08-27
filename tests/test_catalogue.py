"""Unit tests for the tier catalogue.

The engine used to decide "is this the flagship?" with ``"elite" in title``. Every test
here is a case that answer got wrong, or a case the replacement must not get wrong.
"""

from __future__ import annotations

import textwrap

import pytest

from dealscout.catalogue import (
    ADULT_FLAGSHIP,
    JUNIOR_FLAGSHIP,
    MANAGED_ATTRS,
    TAKEDOWN,
    UNKNOWN,
    build,
    classify,
    load,
)

CATEGORY = "football_boots"


def _tier(title: str, brand: str = "", rrp: float | None = None) -> str:
    return classify(title, CATEGORY, brand, rrp).tier


# ------------------------------------------------------------------- the false friends


def test_elite_inside_a_range_name_should_not_make_a_takedown_a_flagship():
    # "Maximus Elite" is the range; "Academy" is the tier. RRP €70.
    assert _tier("Diadora Maximus Elite Academy FG") == TAKEDOWN


def test_a_hyphenated_range_name_should_be_read_the_same_way():
    assert _tier("Diadora Kids B-Elite Pro FG") == TAKEDOWN


def test_a_qualifier_after_elite_should_be_able_to_raise_the_tier_too():
    # The same later-word-wins rule, pointing the other way: "Made in Japan" sits ABOVE
    # "Elite" for Mizuno. One rule settles both traps, with no per-brand branch.
    assert _tier("Mizuno Morelia II Elite Made in Japan FG") == ADULT_FLAGSHIP


def test_mizuno_elite_without_the_japan_qualifier_should_be_second_tier():
    assert _tier("Mizuno Morelia II Elite FG") == TAKEDOWN


def test_a_brand_whose_flagship_is_not_called_elite_should_not_be_flattered_by_the_word():
    # Puma's top line is "Ultimate"; "Elite" is not a Puma tier word at all.
    assert _tier("Puma Future 8 Elite FG") == UNKNOWN


def test_that_brands_actual_flagship_word_should_still_be_recognised():
    assert _tier("Puma Future Ultimate FG/AG") == ADULT_FLAGSHIP


def test_a_brand_with_no_reliable_tier_naming_should_produce_no_opinion():
    # Umbro puts "Elite" in model names at several price points; borrowing Nike's meaning
    # of the word would be a confident guess.
    assert _tier("Umbro Medusae Elite FG") == UNKNOWN


def test_an_unrecognised_brand_should_produce_no_opinion_rather_than_a_default():
    assert _tier("Kipsta Viralto Elite FG") == UNKNOWN


# ------------------------------------------------------------------ junior versus adult


def test_a_junior_marker_should_make_a_flagship_a_junior_flagship():
    assert _tier("Nike Kids Tiempo Legend VII Elite FG") == JUNIOR_FLAGSHIP


def test_the_same_boot_without_a_junior_marker_should_be_the_adult_flagship():
    assert _tier("Nike Tiempo Legend VII Elite FG") == ADULT_FLAGSHIP


def test_a_junior_flagship_should_still_be_a_flagship():
    # Not a lesser tier — the top of its own range, and a legitimate purchase.
    assert classify("Nike Kids Mercurial Superfly 10 Elite AG", CATEGORY).is_flagship is True


def test_a_stated_rrp_in_the_junior_band_should_settle_a_title_that_is_silent():
    # komanda.lv lists a genuine junior boot as "adidas Predator Elite LL FG" with nothing
    # in the title to say so. €130 is squarely the junior band and far below the adult one.
    assert _tier("adidas Predator Elite LL FG", rrp=130.0) == JUNIOR_FLAGSHIP


def test_a_discounted_adult_flagship_should_not_be_demoted_by_its_price():
    # Price never decides tier. An adult flagship at €130 keeps its €280 RRP, and the
    # discount is exactly what this tool exists to find.
    assert _tier("adidas Predator Elite LL FG", rrp=280.0) == ADULT_FLAGSHIP


def test_a_missing_rrp_should_infer_nothing_about_who_the_boot_is_for():
    assert _tier("adidas Predator Elite LL FG", rrp=None) == ADULT_FLAGSHIP


# ------------------------------------------------------------------------ normalisation


def test_a_soleplate_suffix_should_not_be_read_as_a_tier():
    # "AG-Pro" and "SG-Pro" contain the Pro tier word and would demote every one of them.
    assert _tier("Nike Mercurial Superfly XI Elite SE AG-Pro") == ADULT_FLAGSHIP


def test_a_special_edition_badge_should_not_be_read_as_a_tier():
    assert _tier("Nike Phantom 6 Elite NN FG") == ADULT_FLAGSHIP


def test_the_legacy_plus_notation_should_still_read_as_the_flagship():
    # adidas marked the top tier with "+" before July 2024; still on clearance shelves.
    assert _tier("adidas Predator+ FG") == ADULT_FLAGSHIP


def test_a_roman_generation_should_resolve_to_the_same_boot_as_the_arabic_one():
    roman = classify("Nike Tiempo Legend X Elite FG", CATEGORY)
    arabic = classify("Nike Tiempo Legend 10 Elite FG", CATEGORY)

    assert roman.generation == arabic.generation == "10"


def test_a_single_brand_retailer_omitting_its_own_name_should_still_be_recognised():
    # teamsport.lv is Nike's Latvian distributor and lists "ZM SUPERFLY 10 ELITE SG-PRO"
    # with the word Nike nowhere in it. Only one brand here makes a Superfly.
    reading = classify("ZM SUPERFLY 10 ELITE SG-PRO", CATEGORY)

    assert reading.brand == "nike"
    assert reading.tier == ADULT_FLAGSHIP


def test_the_declared_brand_should_win_over_the_title():
    assert classify("Superfly 10 Elite AG", CATEGORY, brand="Nike").brand == "nike"


# -------------------------------------------------------------------------- generations


def test_a_current_generation_should_be_reported_as_current():
    assert classify("Nike Mercurial Superfly 11 Elite FG", CATEGORY).status == "current"


def test_a_one_generation_old_flagship_should_read_as_superseded():
    # The best find this tool can make: a genuine flagship at a legitimate discount.
    assert classify("adidas Predator 24 Elite FG", CATEGORY).status == "superseded"


def test_an_eight_year_old_boot_should_read_as_discontinued_with_its_year():
    reading = classify("Nike Kids Tiempo Legend VII Elite FG", CATEGORY)

    assert reading.status == "discontinued"
    assert reading.year == 2018


def test_a_title_that_names_no_generation_should_report_none():
    # "Predator Elite" alone cannot be dated — it may be a 24, 25 or 26. Saying so beats
    # picking one.
    reading = classify("adidas Predator Elite FG", CATEGORY)

    assert reading.tier == ADULT_FLAGSHIP
    assert reading.generation == ""
    assert reading.status == ""


def test_a_more_specific_model_name_should_win_over_a_shorter_one():
    # "Copa" would otherwise claim Copa Mundial, which sits outside the tier ladder.
    assert classify("adidas Copa Mundial", CATEGORY).line == "copa mundial"


# ------------------------------------------------------------------------ engine attrs


def test_an_unknown_tier_should_be_absent_from_the_attributes_not_present_as_a_word():
    # Absence is how this engine represents unknown, and `_check` relies on it. Emitting
    # the literal string "unknown" would make an unanswerable question look answered.
    assert "tier" not in classify("Puma Future 8 Elite FG", CATEGORY).as_attrs()


def test_a_known_reading_should_publish_the_attributes_a_hunt_can_require():
    attrs = classify("Nike Kids Tiempo Legend VII Elite FG", CATEGORY).as_attrs()

    assert attrs["tier"] == JUNIOR_FLAGSHIP
    assert attrs["generation_status"] == "discontinued"
    assert attrs["generation_year"] == "2018"
    assert set(attrs) <= MANAGED_ATTRS


# ---------------------------------------------------------------------------- loading


def test_a_category_with_no_catalogue_should_load_as_none_rather_than_raise():
    # The ordinary case: knitwear and running shoes have no tier ladder worth tabulating.
    assert load("knitwear") is None


def test_a_category_with_no_catalogue_should_classify_as_unknown():
    assert classify("Nike Vaporfly 3 Elite", "running_shoes").tier == UNKNOWN


def test_a_corrupt_catalogue_should_degrade_to_no_opinion_rather_than_break_a_hunt(tmp_path):
    (tmp_path / "boots.yaml").write_text("{ not: [valid", encoding="utf-8")

    assert load("boots", tmp_path) is None


def test_a_catalogue_that_is_not_a_mapping_should_be_ignored(tmp_path):
    (tmp_path / "boots.yaml").write_text("- just\n- a list\n", encoding="utf-8")

    assert load("boots", tmp_path) is None


def test_an_empty_catalogue_should_classify_everything_as_unknown():
    assert build({}).classify("Nike Mercurial Superfly 11 Elite FG").tier == UNKNOWN


def test_the_shipped_catalogue_should_declare_when_it_was_last_verified():
    # Generation status is a snapshot and rots silently; the date is how a stale row is
    # told apart from a parser bug.
    catalogue = load(CATEGORY)

    assert catalogue is not None
    assert catalogue.last_verified


@pytest.mark.parametrize(
    "title",
    ["", "   ", "!!!", "FG", "Elite", "1234"],
)
def test_odd_input_should_never_raise(title: str):
    assert classify(title, CATEGORY).tier in {UNKNOWN, TAKEDOWN, ADULT_FLAGSHIP}


def test_a_data_file_can_teach_a_new_brand_without_touching_code(tmp_path):
    # The point of holding this as data: a new brand is an edit, not a release.
    (tmp_path / "boots.yaml").write_text(
        textwrap.dedent(
            """
            category: boots
            brands:
              acme:
                flagship: [supreme]
                takedown: [basic]
                lines: {}
            """
        ),
        encoding="utf-8",
    )

    catalogue = load("boots", tmp_path)

    assert catalogue is not None
    assert catalogue.classify("Acme Rocket Supreme FG").tier == ADULT_FLAGSHIP
    assert catalogue.classify("Acme Rocket Basic FG").tier == TAKEDOWN


# ------------------------------------------------- a generation newer than this file


def _gen(title: str, brand: str = "adidas"):
    return classify(title, CATEGORY, brand, None)


def test_a_generation_newer_than_the_catalogue_should_not_be_called_the_oldest_one():
    """Found in a live email: "Copa Pure IV Elite ... discontinued generation (2022)".

    `copa pure` is listed as the 2022 original, and being a prefix of every later name
    it answered for all of them. The failure direction is the bad one — it argues the
    owner out of a current boot — and it arrives silently on the day adidas ships a
    number this file has not seen.
    """
    boot = _gen("adidas Copa Pure IV Elite FG Chaos vs Control Kids")

    assert boot.year is None
    assert not boot.status


def test_that_boot_should_still_be_classified_as_a_flagship():
    """Only the generation claim is withdrawn; the tier is read from the title and stands."""
    assert _gen("adidas Copa Pure IV Elite FG Chaos vs Control Kids").tier == JUNIOR_FLAGSHIP


def test_a_generation_the_catalogue_does_know_should_be_unaffected():
    known = _gen("adidas Copa Pure III Elite FG")

    assert str(known.generation) == "3"
    assert known.year == 2024
    assert known.status == "current"


def test_an_unnumbered_title_should_still_match_the_unnumbered_generation():
    """The 2022 Copa Pure really is the one with no number, so it must keep answering."""
    original = _gen("adidas Copa Pure Elite FG")

    assert original.year == 2022
    assert original.status == "discontinued"


def test_a_named_generation_without_a_number_should_still_match():
    """Regression guard: `predator accuracy` and `phantom gx` are names, not catch-alls."""
    assert _gen("adidas Predator Accuracy+ FG").year == 2023
    assert _gen("Nike Phantom GX Elite FG", brand="Nike").year == 2023

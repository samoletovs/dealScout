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

    The example moved on 2026-08-28: the Copa Pure IV that first exposed this is now IN
    the catalogue (it shipped in January 2026 and is the current generation), so it can
    no longer stand for a number the file has never seen. Copa Pure 5 does that job now
    — and will keep doing it, because the point of the case is a number nobody has
    catalogued rather than any particular boot.
    """
    boot = _gen("adidas Copa Pure V Elite FG Kids")

    assert boot.year is None
    assert not boot.status


def test_that_boot_should_still_be_classified_as_a_flagship():
    """Only the generation claim is withdrawn; the tier is read from the title and stands."""
    assert _gen("adidas Copa Pure V Elite FG Kids").tier == JUNIOR_FLAGSHIP


def test_a_generation_the_catalogue_does_know_should_be_unaffected():
    known = _gen("adidas Copa Pure III Elite FG")

    assert str(known.generation) == "3"
    assert known.year == 2024
    # Superseded since January 2026, when the Copa Pure 4 shipped. This assertion is the
    # canary for that rot: it is the line that fails the next time adidas moves the silo
    # on and this file has not been refreshed.
    assert known.status == "superseded"


def test_an_unnumbered_title_should_still_match_the_unnumbered_generation():
    """The 2022 Copa Pure really is the one with no number, so it must keep answering."""
    original = _gen("adidas Copa Pure Elite FG")

    assert original.year == 2022
    assert original.status == "discontinued"


def test_a_named_generation_without_a_number_should_still_match():
    """Regression guard: `predator accuracy` and `phantom gx` are names, not catch-alls."""
    assert _gen("adidas Predator Accuracy+ FG").year == 2023
    assert _gen("Nike Phantom GX Elite FG", brand="Nike").year == 2023


# ------------------------------------- how sportsdirect.lv writes a name (the main shop)
#
# Every title in this block is verbatim from
# www.sportsdirect.lv/football/football-boots/kids-football-boots, read 2026-08-28. They
# are here because that shop is the one the owner actually orders from, and until it was
# wired in nothing was reading them.


def test_a_plural_junior_marker_should_still_mark_a_boot_as_junior():
    # "Juniors", not "Junior" — and tokens match on a word boundary, so the trailing "s"
    # defeated the marker. This €61.20 boot was being reported as an ADULT flagship, the
    # single error the catalogue exists to prevent.
    assert _tier("Predator Elite Juniors Firm Ground Football Boots", "adidas") == JUNIOR_FLAGSHIP


def test_the_plural_of_children_should_be_read_the_same_way():
    assert (
        _tier("Predator Accuracy Injection+ Childrens Elite Firm Ground Football Boots", "adidas")
        == JUNIOR_FLAGSHIP
    )


def test_a_soleplate_pro_orphaned_from_its_ground_should_not_demote_a_flagship():
    # SportsDirect spells the ground out in words, which strands the "Pro" of "SG-Pro":
    # "Superfly 10 Elite SG-Pro" is listed as "Elite Pro … Soft Ground". `soleplate_suffixes`
    # cannot catch it — the "SG" it anchors on is gone — so the stray Pro fell later than
    # "Elite" and the later-word-wins rule demoted a genuine €96 junior Elite to a takedown.
    assert (
        _tier("Kids' Superfly 10 Elite Pro Soft Ground Football Boot", "Nike") == JUNIOR_FLAGSHIP
    )


def test_an_elite_pro_with_no_ground_named_should_stay_a_takedown():
    """The guard on the rule above, and the reason it insists on the spelled-out ground.

    Diadora's range really is called "B-Elite" and its tier really is "Pro", so here the
    later word is the truth. Rewriting every "Elite Pro" promoted this €70 boot to a
    flagship — caught by the existing suite, and kept caught by this.
    """
    assert _tier("Diadora Kids B-Elite Pro FG") == TAKEDOWN
    assert _tier("Diadora Kids B-Elite Pro Firm Ground") == TAKEDOWN


def test_a_truncated_tier_word_should_still_be_read_as_the_tier():
    # SportsDirect truncates to a fixed width: "Pred Elt" is Predator Elite.
    assert (
        _tier("Unisex Kids' Pred Elt Predator Soft Ground Football Boots", "adidas")
        == JUNIOR_FLAGSHIP
    )


def test_a_name_truncated_without_spaces_should_still_be_read():
    # "CopaP3Elt" is Copa Pure 3 Elite — a genuine junior Elite at €70.20 that carried no
    # recognisable tier word at all, so `require_stated: [tier]` dropped it every run.
    assert (
        _tier("Unisex Kids CopaP3Elt Soft Ground Football Boots", "adidas") == JUNIOR_FLAGSHIP
    )


def test_the_truncated_name_should_also_recover_its_generation():
    """Expanding the abbreviation must restore the model line too, not just the tier."""
    read = classify("Unisex Kids CopaP3Elt Soft Ground Football Boots", CATEGORY, "adidas")

    assert read.line == "copa"
    assert str(read.generation) == "3"


# ----------------------------------------------- adidas before it renamed its tiers (2024)
#
# Until mid-2024 adidas marked the tier with a NUMBER: Predator 20.1, X Ghosted.1,
# Copa Sense.1. "+" (laceless) and ".1" (laced) were the SAME top tier — the confusion
# adidas renamed to Elite to end. ".2" was Pro, ".3" League, ".4" Club. These boots are
# exactly what a clearance shelf is full of, and every one of them read as `unknown`,
# which `require_stated: [tier]` then rejected in silence.


def test_a_legacy_dot_one_should_be_read_as_a_flagship():
    assert _tier("adidas Predator Accuracy.1 FG", "adidas") == ADULT_FLAGSHIP


def test_a_legacy_year_numbered_dot_one_should_be_read_the_same_way():
    assert _tier("adidas Predator 20.1 FG", "adidas") == ADULT_FLAGSHIP


def test_a_legacy_dot_one_on_a_retired_silo_should_be_read_too():
    assert _tier("adidas X Ghosted.1 FG", "adidas") == ADULT_FLAGSHIP


def test_a_legacy_dot_one_with_a_junior_marker_should_be_the_junior_flagship():
    assert _tier("adidas Copa Sense.1 Kids FG", "adidas") == JUNIOR_FLAGSHIP


@pytest.mark.parametrize(
    "title",
    [
        "adidas Predator Edge.2 FG",
        "adidas X Speedflow.3 FG",
        "adidas Nemeziz 19.4 FG",
    ],
)
def test_the_legacy_numbers_below_one_should_be_takedowns(title: str):
    assert _tier(title, "adidas") == TAKEDOWN


def test_a_generation_number_must_not_be_mistaken_for_a_legacy_tier():
    """The whole reason the legacy read insists on the dot.

    `Copa Pure 3` is a current GENERATION; `.3` was a League takedown. Normalisation turns
    the dot into a space, after which the two are the same characters — so reading the
    number off the normalised title would demote a current flagship, which is the worst
    direction to be wrong in.
    """
    assert _tier("adidas Copa Pure 3 Elite FG", "adidas") == ADULT_FLAGSHIP


def test_a_word_tier_should_outrank_the_legacy_number_reading():
    """A title that names its tier in words is never second-guessed by the number rules."""
    assert _tier("adidas Predator League FG", "adidas") == TAKEDOWN


def test_a_brand_with_no_legacy_numbering_should_not_borrow_adidas_rules():
    # Nike never used the dotted system, so a stray number must not invent a tier for it.
    assert _tier("Nike Phantom 6.3 FG", "Nike") == UNKNOWN


# ------------------------------------------- Copa Pure 4 (launched Jan 2026), and bands
#
# `generation.status` is a season snapshot and rots silently — the file says so. It had:
# Copa Pure 3 "current", when adidas shipped the Copa Pure 4 in January 2026. Measured
# 2026-08-28 on komanda.lv, adidas's own Baltic dealer: Copa Pure IV Elite at €240 across
# a full 39-44 grid, Copa Pure 2 Elite down to one size at €184.


def test_the_current_copa_generation_should_be_the_fourth():
    read = classify("adidas Copa Pure IV Elite FG", CATEGORY, "adidas")

    assert str(read.generation) == "4"
    assert read.status == "current"
    assert read.year == 2026


def test_the_third_copa_should_no_longer_claim_to_be_current():
    """The half of the change that is easy to forget: the old current must be demoted."""
    read = classify("adidas Copa Pure 3 Elite FG", CATEGORY, "adidas")

    assert str(read.generation) == "3"
    assert read.status == "superseded"


def test_an_adult_copa_flagship_should_fall_inside_the_adult_band():
    """Copa is adidas's cheapest flagship line at €240, and the band used to start at 250.

    The band is corroboration the email prints, never a gate — but a band that excludes a
    whole current silo is simply wrong about the world.
    """
    read = classify("adidas Copa Pure IV Elite FG", CATEGORY, "adidas", rrp=240.0)

    assert read.tier == ADULT_FLAGSHIP
    assert read.rrp_band is not None
    assert read.rrp_band[0] <= 240.0 <= read.rrp_band[1]


def test_the_cheapest_adult_nike_flagship_should_fall_inside_its_band():
    # €250 is the measured RRP floor across 116 adult Nike Elites (Tiempo Legend X Elite
    # FG/AG-Pro). NOT €210 — that was the same boot's discounted selling price, and an
    # earlier version of this test enshrined it. Bands are RRP.
    read = classify("Nike Tiempo Legend X Elite FG", CATEGORY, "Nike", rrp=250.0)

    assert read.tier == ADULT_FLAGSHIP
    assert read.rrp_band is not None
    assert read.rrp_band[0] <= 250.0 <= read.rrp_band[1]


def test_a_discounted_adult_flagship_must_not_be_read_as_a_junior_one():
    """The trap the band floors are built to survive.

    A Tiempo Legend X Elite sells at €210 while its RRP stays €250. Only the RRP is ever
    passed here, but if a band floor were ever set from selling prices it would drift
    down every sale until an adult flagship fell inside the junior band. Nike's junior
    band tops out at €175 and the adult floor is €250, so there is 75 euros of daylight.
    """
    assert _tier("Nike Tiempo Legend X Elite FG", "Nike", rrp=250.0) == ADULT_FLAGSHIP


def test_a_junior_nike_elite_at_its_real_rrp_should_be_caught():
    """Nike's junior Elites are all discontinued generations at €150-175.

    The old junior band stopped at 150 and so could not infer a junior from any of them.
    A Superfly VI Elite at €175 is unambiguously a kids boot — it is the only thing that
    price means for that model.
    """
    assert _tier("Nike Superfly VI Elite FG", "Nike", rrp=175.0) == JUNIOR_FLAGSHIP


def test_an_adidas_junior_elite_at_the_bottom_of_its_range_should_be_caught():
    """Copa Pure IV Elite Kids lists at €110 — below the old 120 floor, so it was missed."""
    assert _tier("adidas Copa Pure IV Elite FG", "adidas", rrp=110.0) == JUNIOR_FLAGSHIP


def test_lowering_the_adult_floor_must_not_stop_a_silent_junior_being_caught():
    """The guard on the band change.

    `_priced_as_junior` requires the RRP to sit below the adult floor, so lowering that
    floor can only ever make the inference stricter. This is the real boot it must keep
    catching: komanda.lv lists it at €130 in EU 36-38 with nothing in the title to say
    it is a junior.
    """
    assert _tier("adidas Predator Elite LL FG", "adidas", rrp=130.0) == JUNIOR_FLAGSHIP


def test_an_adult_flagship_priced_between_the_bands_should_stay_adult():
    """€162-216 on that shop is a superseded adult flagship on a broken size run.

    It sits above the junior band and below the adult one, and must not be dragged into
    either — being in no band is a truthful answer.
    """
    assert _tier("adidas Predator Elite LL FG", "adidas", rrp=162.0) == ADULT_FLAGSHIP


# ----------------- why a junior marker is NEVER overridden by a reference price
#
# This was attempted on 2026-08-28 and reverted the same day. The reasoning is recorded
# because the idea is a natural one and will be had again.
#
# Both brands' adult Elite ranges start at EU 35-36, which fits a child — measured on
# prodirectsport.ie: 116 adult Nike Elites span EU 35-49 at RRP 250-300, and 84 adult
# adidas Elites span EU 36-48 at 240-350. So retailers shelve small sizes of the ADULT
# flagship under "Kids": sportsdirect.lv lists "Kids' Superfly 10 Elite SG", EU 36-39, at
# a reference price of 317.99, which is the adult RRP for a genuinely adult-spec boot.
# That argues for reading an adult-band RRP as evidence the junior word is a shelf label.
#
# It cannot be done, because the SAME shop also does the opposite. Which? analysed 160
# Sports Direct products in 2025, found 58 whose reference price no other retailer
# matched, and reported the company to the CMA. Its own listings show the practice
# plainly: a "Predator Accuracy Injection+ CHILDRENS Elite" in EU 28.5 - a small child's
# boot - carries a reference price of 300, which is the ADULT RRP. The junior boots in
# the golden set below are the same shape: "Predator Elite Juniors" at a stated 260.
#
# So "junior word + adult-band reference price" means BOTH "adult boot on a kids shelf"
# AND "junior boot with an inflated was-price", and title plus price cannot separate
# them. The size grid could - a junior boot stops at EU 38/38.5 - but the catalogue is
# given a title, a brand and a price, and none of those carry it. An explicit junior
# marker is a statement by the retailer about the product; a reference price is the least
# trustworthy number on the page. The explicit statement wins.


def test_a_junior_marker_should_survive_an_adult_looking_reference_price():
    """sportsdirect.lv states 260 against a boot whose title says "Juniors". Which? found
    that shop inflating reference prices to the point of a CMA referral, so the title is
    the better evidence and must not be overridden."""
    assert (
        _tier("Predator Elite Juniors Firm Ground Football Boots", "adidas", rrp=260.0)
        == JUNIOR_FLAGSHIP
    )


def test_the_same_should_hold_for_a_kids_shelf_label_on_a_nike_elite():
    """The case that motivated the attempt. It really may be the adult boot in EU 36-39 -
    but nothing in a title, brand and price can establish that, so the catalogue says what
    the retailer said rather than guessing past it."""
    assert (
        _tier("Kids' Superfly 10 Elite Soft Ground Football Boot", "Nike", rrp=317.99)
        == JUNIOR_FLAGSHIP
    )


def test_the_price_inference_should_still_work_in_the_direction_it_can():
    """The surviving rule is the one where the title is SILENT, so nothing is overridden:
    komanda.lv lists "adidas Predator Elite LL FG" at 130 in EU 36-38 with no junior word
    anywhere. Inferring from price adds information; it does not contradict any."""
    assert _tier("adidas Predator Elite LL FG", "adidas", rrp=130.0) == JUNIOR_FLAGSHIP

"""Tests for the hunt-aware golden case type.

The golden set gates CI, so its own failure mode matters: a case that reaches the right
band for the wrong reason certifies an accident. These tests pin the two mechanisms that
prevent that — routing a case to the *hunt* judge, and pinning the attributes the verdict
was built from.
"""

from __future__ import annotations

import textwrap
from dataclasses import replace

import pytest

from dealscout.eval import (
    UNWANTED_SIZE,
    WANTED_SIZE,
    CaseResult,
    load_golden,
    resolve_sizes,
    DEFAULT_CONFIG,
    DEFAULT_GOLDEN,
    GoldenCase,
    evaluate,
    format_scorecard,
    load_golden,
    load_hunts,
    main,
)
from dealscout.config import load_config
from dealscout.models import Product

# A hunt with the same shape as the real one: top tier only, in one size, under €100.
HUNT_CONFIG = {
    "hunts": [
        {
            "id": "boots-test",
            "label": "Boots (test)",
            "category": "football_boots",
            "sizes": ["37.5"],
            "brands": ["Nike", "adidas"],
            "brands_only": True,
            "require": {"tier": ["adult-flagship", "junior-flagship"], "soleplate": ["AG", "FG"]},
            "require_stated": ["tier"],
            "price": {"must_buy": 70, "good_offer": 100, "never_above": 100},
        },
        {"id": "boots-disabled", "enabled": False, "category": "football_boots"},
    ]
}


def _boot(title: str = "Nike Jr. Mercurial Superfly 10 Elite AG", price: float = 65.0) -> Product:
    return Product(
        title=title,
        category="football_boots",
        price=price,
        reference_price=130.0,
        currency="EUR",
        url="https://example.com/boot",
        brand="Nike",
        source="komanda.lv",
        sizes=frozenset({"37.5"}),
        sizes_known=True,
    )


def _case(**overrides) -> GoldenCase:
    base = dict(id="c", product=_boot(), expected_band="must-buy", hunt_id="boots-test")
    base.update(overrides)
    return GoldenCase(**base)


# ------------------------------------------------------------------- routing to a judge


def test_a_case_naming_a_hunt_should_be_scored_by_the_hunt_judge():
    result = evaluate([_case()], HUNT_CONFIG)

    assert result.results[0].band_ok is True
    assert result.by_kind()["hunt"]["cases"] == 1


def test_a_case_without_a_hunt_should_still_be_scored_by_the_wardrobe_judge():
    result = evaluate([_case(hunt_id="", expected_band="reject")], HUNT_CONFIG)

    assert result.by_kind()["wardrobe"]["cases"] == 1
    assert result.by_kind()["hunt"]["cases"] == 0


def test_the_two_judges_should_be_reported_separately_rather_than_blended():
    # Blending them would hide which judge a regression touched.
    cases = [_case(id="h"), _case(id="w", hunt_id="", expected_band="reject")]

    stats = evaluate(cases, HUNT_CONFIG).by_kind()

    assert (stats["hunt"]["cases"], stats["wardrobe"]["cases"]) == (1, 1)


def test_a_case_naming_a_hunt_that_does_not_exist_should_fail_loudly():
    with pytest.raises(ValueError, match="no hunt 'boots-typo'"):
        evaluate([_case(hunt_id="boots-typo")], HUNT_CONFIG)


def test_a_disabled_hunt_should_still_be_scoreable():
    # `enabled` governs whether a cron runs a hunt, not whether its rules are protected.
    assert "boots-disabled" in load_hunts(HUNT_CONFIG)


def test_the_hunt_judge_should_reach_a_verdict_the_wardrobe_judge_cannot():
    # The same boot, routed both ways: proof that `hunt:` changes what is measured.
    hunted = evaluate([_case()], HUNT_CONFIG).results[0]
    wardrobe = evaluate([_case(hunt_id="")], HUNT_CONFIG).results[0]

    assert hunted.predicted_band != wardrobe.predicted_band


# --------------------------------------------------------------- attribute expectations


def test_a_case_should_be_able_to_pin_the_attributes_behind_the_verdict():
    result = evaluate([_case(expected_attrs={"tier": "junior-flagship", "soleplate": "AG"})], HUNT_CONFIG)

    assert result.results[0].attr_ok is True
    assert result.attr_accuracy == 1.0


def test_a_pinned_attribute_the_engine_reads_differently_should_be_a_miss():
    result = evaluate([_case(expected_attrs={"tier": "adult-flagship"})], HUNT_CONFIG)

    assert result.results[0].attr_ok is False
    assert result.attr_accuracy == 0.0


def test_an_attribute_miss_should_report_what_was_read_instead():
    result = evaluate([_case(expected_attrs={"tier": "adult-flagship"})], HUNT_CONFIG)

    assert result.results[0].attr_misses == ("tier=junior-flagship, wanted adult-flagship",)


def test_an_attribute_the_title_never_stated_should_read_as_unstated_not_blank():
    boot = _boot(title="Nike Jr. Phantom GX FG")

    result = evaluate([_case(product=boot, expected_attrs={"tier": "junior-flagship"})], HUNT_CONFIG)

    assert result.results[0].attr_misses == ("tier=(unstated), wanted junior-flagship",)


def test_a_case_pinning_nothing_should_be_excluded_rather_than_counted_as_correct():
    # Counting it as a pass would inflate the metric with cases that measure nothing —
    # which is the exact disease this harness exists to cure.
    result = evaluate([_case()], HUNT_CONFIG)

    assert result.results[0].attr_ok is None
    assert result.attr_checked == 0


def test_attribute_accuracy_should_be_undefined_when_no_case_pins_anything():
    result = evaluate([_case()], HUNT_CONFIG)

    assert result.attr_accuracy is None


def test_band_accuracy_should_ignore_whether_attributes_were_pinned():
    # A wrong attribute must not silently move the band metric, or the two blur together.
    result = evaluate([_case(expected_attrs={"tier": "adult-flagship"})], HUNT_CONFIG)

    assert result.accuracy == 1.0
    assert result.attr_accuracy == 0.0


# -------------------------------------------------------------------------- loading YAML


def _write(tmp_path, body: str):
    path = tmp_path / "golden.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_load_golden_should_read_the_hunt_a_case_names(tmp_path):
    path = _write(
        tmp_path,
        """
        cases:
          - id: boot
            hunt: boots-test
            product:
              title: "Nike Jr. Mercurial Superfly 10 Elite AG"
              category: football_boots
              price: 65
              reference_price: 130
              currency: EUR
              url: "https://example.com/b"
            expected: { band: must-buy }
        """,
    )

    assert load_golden(path)[0].hunt_id == "boots-test"


def test_load_golden_should_read_pinned_attributes(tmp_path):
    path = _write(
        tmp_path,
        """
        cases:
          - id: boot
            hunt: boots-test
            product:
              title: "Nike Jr. Mercurial Superfly 10 Elite AG"
              category: football_boots
              price: 65
              reference_price: 130
              currency: EUR
              url: "https://example.com/b"
            expected:
              band: must-buy
              attrs: { tier: elite, soleplate: AG }
        """,
    )

    assert load_golden(path)[0].expected_attrs == {"tier": "elite", "soleplate": "AG"}


def test_load_golden_should_default_a_case_with_no_hunt_to_the_wardrobe_judge(tmp_path):
    path = _write(
        tmp_path,
        """
        cases:
          - id: knit
            product:
              title: "BOSS wool crew"
              category: knitwear
              price: 45
              reference_price: 150
              currency: EUR
              url: "https://example.com/k"
            expected: { band: must-buy }
        """,
    )

    case = load_golden(path)[0]
    assert case.hunt_id == ""
    assert case.kind == "wardrobe"


def test_load_golden_should_reject_attrs_that_are_not_a_mapping(tmp_path):
    path = _write(
        tmp_path,
        """
        cases:
          - id: boot
            product:
              title: "Boot"
              category: football_boots
              price: 65
              reference_price: 130
              currency: EUR
              url: "https://example.com/b"
            expected:
              band: reject
              attrs: [tier, elite]
        """,
    )

    with pytest.raises(ValueError, match="expected.attrs"):
        load_golden(path)


# ------------------------------------------------------------------- the shipped set


def test_the_shipped_golden_set_should_exercise_the_hunt_judge():
    # Before this change the set was 15 wardrobe cases and the hunt judge — which decides
    # every boot the tool surfaces — was scored by nothing at all.
    result = evaluate(load_golden(DEFAULT_GOLDEN), load_config(DEFAULT_CONFIG))

    assert result.by_kind()["hunt"]["cases"] >= 5


def test_the_shipped_golden_set_should_pin_attributes_on_its_hunt_cases():
    result = evaluate(load_golden(DEFAULT_GOLDEN), load_config(DEFAULT_CONFIG))

    assert result.attr_checked >= 4
    assert result.attr_accuracy == 1.0


def test_the_scorecard_should_show_both_judges_and_the_attribute_score():
    scorecard = format_scorecard(evaluate(load_golden(DEFAULT_GOLDEN), load_config(DEFAULT_CONFIG)))

    assert "## By judge" in scorecard
    assert "hunt (`judge_hunt`)" in scorecard
    assert "Attribute accuracy:" in scorecard
    assert "## Attribute misses" in scorecard


# ------------------------------------------------------------------------------ gating


def _knit(price: float = 25.0) -> Product:
    return Product(
        title="BOSS wool crew",
        category="knitwear",
        price=price,
        reference_price=150.0,
        currency="EUR",
        url="https://example.com/knit",
        brand="BOSS",
        materials={"wool": 1.0},
        care="machine wash",
    )


# Both judges live in one config, so a single run mixes them the way the shipped set does.
MIXED_CONFIG = {
    **HUNT_CONFIG,
    "filters": {"natural_fibre_min": 0.0},
    "deal": {"must_buy": {"knitwear": 30}, "good_offer": {"knitwear": 60}},
}


def _mixed_result():
    """The shipped 15/7 split, with two hunt cases failing outright."""
    wardrobe = [
        GoldenCase(id=f"w{i}", product=_knit(), expected_band="must-buy") for i in range(15)
    ]
    hunt_ok = [_case(id=f"h{i}") for i in range(5)]
    hunt_wrong = [_case(id=f"x{i}", expected_band="reject") for i in range(2)]
    return evaluate([*wardrobe, *hunt_ok, *hunt_wrong], MIXED_CONFIG)


def test_a_blended_accuracy_should_hide_a_regression_in_the_smaller_case_set():
    # Not a bug being asserted — the reason --min-kind-accuracy exists. Two of seven hunt
    # cases failing outright still scores above the 90% gate on the blended figure.
    result = _mixed_result()

    assert result.accuracy > 0.90


def test_a_per_judge_accuracy_should_expose_the_regression_the_blend_hides():
    result = _mixed_result()

    assert result.by_kind()["hunt"]["accuracy"] < 0.90
    assert result.by_kind()["wardrobe"]["accuracy"] == 1.0


def test_the_shipped_golden_set_should_pass_the_gate_ci_actually_runs(tmp_path):
    exit_code = main(
        [
            "--out", str(tmp_path / "scorecard.md"),
            "--min-accuracy", "0.90",
            "--min-deal-precision", "0.90",
            "--min-attr-accuracy", "0.90",
            "--min-kind-accuracy", "0.90",
        ]
    )

    assert exit_code == 0


def test_gating_attribute_accuracy_should_fail_when_no_case_pins_anything(tmp_path):
    # Asking to gate a dimension nothing measures is a lie by omission, so it fails
    # rather than silently reporting "—".
    golden = _write(
        tmp_path,
        """
        cases:
          - id: knit
            product:
              title: "BOSS wool crew"
              category: knitwear
              price: 45
              reference_price: 150
              currency: EUR
              url: "https://example.com/k"
              materials: { wool: 1.0 }
            expected: { band: must-buy }
        """,
    )

    exit_code = main(
        ["--golden", str(golden), "--out", str(tmp_path / "s.md"), "--min-attr-accuracy", "0.90"]
    )

    assert exit_code == 1

def _grown_out_config():
    """The real config with every size replaced — the edit made when a child outgrows a size."""
    import copy

    from dealscout.config import load_config

    config = copy.deepcopy(load_config(DEFAULT_CONFIG))
    for hunt in config.get("hunts", []):
        hunt["sizes"] = ["40", "40.5", "40.67"]
        for brand in hunt.get("sizes_by_brand", {}):
            hunt["sizes_by_brand"][brand] = ["40.67" if brand == "adidas" else "40.5"]
    return config


def test_the_golden_set_should_survive_the_owner_s_son_growing():
    """A size change is a legitimate config edit, not a regression. It must not fail the gate.

    The golden set scores against the live config on purpose, so a config regression surfaces.
    But sizes are the one part of that config expected to change on its own schedule, and
    cases pinning a literal size were failing for a reason that had nothing to do with them.
    """
    cases = load_golden()

    result = evaluate(cases, _grown_out_config())

    assert result.accuracy == 1.0
    assert result.by_kind()["hunt"]["accuracy"] == 1.0


def test_a_wanted_size_should_mean_whatever_this_hunt_currently_wants():
    hunt = load_hunts(HUNT_CONFIG)["boots-test"]
    product = replace(_boot(), sizes=frozenset({WANTED_SIZE}), sizes_known=True)

    resolved = resolve_sizes(product, hunt)

    assert resolved.sizes == frozenset(hunt.sizes_for(product.brand, product.title))
    assert WANTED_SIZE not in resolved.sizes


def test_an_unwanted_size_should_never_collide_with_a_wanted_one():
    """The 'shop stated its sizes and ours is not among them' case has to stay a no."""
    hunt = load_hunts(HUNT_CONFIG)["boots-test"]
    product = replace(_boot(), sizes=frozenset({UNWANTED_SIZE}), sizes_known=True)

    resolved = resolve_sizes(product, hunt)

    assert resolved.sizes.isdisjoint(hunt.sizes_for(product.brand, product.title))


def test_a_literal_size_should_be_left_exactly_as_written():
    """Regression guard: resolution must only touch the sentinels, never a real size."""
    hunt = load_hunts(HUNT_CONFIG)["boots-test"]
    product = replace(_boot(), sizes=frozenset({"42"}), sizes_known=True)

    assert resolve_sizes(product, hunt).sizes == frozenset({"42"})


def test_a_null_expected_attr_should_pin_that_nothing_is_asserted():
    """Declining to answer is a real outcome here, so a case has to be able to pin it.

    Without this the Copa Pure IV case could only pin the attributes that did not move,
    and passed just as happily with the bug reinstated.
    """
    case = _case(expected_attrs={"generation_status": None})
    result = CaseResult(
        case=case,
        predicted_band=case.expected_band,
        predicted_is_deal=True,
        reasons=(),
        predicted_attrs={"generation_status": "discontinued"},
    )

    assert result.attr_ok is False
    assert "wanted nothing asserted" in result.attr_misses[0]


def test_a_null_expected_attr_should_pass_when_the_engine_stays_silent():
    case = _case(expected_attrs={"generation_status": None})
    result = CaseResult(
        case=case,
        predicted_band=case.expected_band,
        predicted_is_deal=True,
        reasons=(),
        predicted_attrs={"tier": "junior-flagship"},
    )

    assert result.attr_ok is True


def test_an_empty_string_should_count_as_saying_nothing():
    """The engine writes "" for an attribute it looked at and could not name."""
    case = _case(expected_attrs={"generation_status": None})
    result = CaseResult(
        case=case,
        predicted_band=case.expected_band,
        predicted_is_deal=True,
        reasons=(),
        predicted_attrs={"generation_status": ""},
    )

    assert result.attr_ok is True

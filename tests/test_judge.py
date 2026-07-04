"""Unit tests for the dealScout deal judge."""

from __future__ import annotations

from dealscout.judge import discount_pct, judge, natural_fibre_ratio
from dealscout.models import Product

CONFIG = {
    "filters": {
        "reject_big_wordmarks": True,
        "natural_fibre_min": 0.70,
        "sportswear_synthetic_ok": True,
        "care_no_dry_clean_only": True,
        "quality_signals": ["natural_fibre"],
    },
    "deal": {
        "min_discount_pct": 50,
        "cant_say_no": {"knitwear": 30},
        "never_above": {"knitwear": 110},
    },
}


def _product(**overrides) -> Product:
    base = dict(
        title="Test jumper",
        category="knitwear",
        price=25.0,
        reference_price=100.0,
        currency="EUR",
        url="https://example.com/p",
        materials={"wool": 1.0},
        has_big_logo=False,
        quality_signals=frozenset(),
        care="machine wash",
    )
    base.update(overrides)
    return Product(**base)


def test_should_flag_deep_discount_under_ceiling_as_deal():
    # Arrange: 75% off, €25 <= €30 ceiling, 100% wool, no logo
    verdict = judge(_product(price=25.0, reference_price=100.0), CONFIG)
    # Assert
    assert verdict.is_deal is True


def test_should_reject_item_with_big_logo():
    verdict = judge(_product(has_big_logo=True), CONFIG)
    assert verdict.is_deal is False
    assert "big logo" in verdict.reasons[0]


def test_should_reject_when_price_over_never_above():
    verdict = judge(_product(price=120.0, reference_price=400.0), CONFIG)
    assert verdict.is_deal is False


def test_should_reject_synthetic_when_not_sportswear():
    verdict = judge(_product(materials={"polyester": 1.0}), CONFIG)
    assert verdict.is_deal is False


def test_should_not_flag_shallow_discount():
    # 20% off is below the 50% deep-discount bar
    verdict = judge(_product(price=80.0, reference_price=100.0), CONFIG)
    assert verdict.is_deal is False


def test_natural_fibre_ratio_counts_only_natural():
    assert natural_fibre_ratio({"wool": 0.8, "polyester": 0.2}) == 0.8


def test_discount_pct_handles_missing_reference():
    assert discount_pct(50.0, None) == 0.0

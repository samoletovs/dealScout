"""Unit tests for the dealScout deal judge."""

from __future__ import annotations

from dealscout.judge import brand_tier, discount_pct, judge, natural_fibre_ratio
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
        "must_buy": {"knitwear": 30},
        "good_offer": {"knitwear": 60},
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


def test_should_flag_must_buy_price_as_deal():
    # €25 <= must-buy €30, 100% wool, no logo -> must-buy
    verdict = judge(_product(price=25.0, reference_price=100.0), CONFIG)
    assert verdict.is_deal is True
    assert verdict.band == "must-buy"


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


def test_should_not_flag_regular_price():
    # €80 knitwear is above the good-offer band -> regular price, skip
    verdict = judge(_product(price=80.0, reference_price=100.0), CONFIG)
    assert verdict.is_deal is False


def test_natural_fibre_ratio_counts_only_natural():
    assert natural_fibre_ratio({"wool": 0.8, "polyester": 0.2}) == 0.8


def test_discount_pct_handles_missing_reference():
    assert discount_pct(50.0, None) == 0.0


BRAND_CONFIG = {
    "filters": {**CONFIG["filters"], "min_brand_tier": "basket"},
    "deal": CONFIG["deal"],
    "brands": {
        "worse": ["Jack & Jones", "H&M"],
        "basket": ["BOSS", "Lacoste"],
        "better": ["Sunspel"],
    },
}


def test_should_reject_brand_below_tier():
    verdict = judge(_product(brand="Jack & Jones"), BRAND_CONFIG)
    assert verdict.is_deal is False
    assert "below your tier" in verdict.reasons[0]


def test_should_accept_basket_tier_brand():
    verdict = judge(_product(brand="BOSS"), BRAND_CONFIG)
    assert verdict.is_deal is True


def test_should_score_better_brand_above_basket():
    basket = judge(_product(brand="Lacoste"), BRAND_CONFIG)
    better = judge(_product(brand="Sunspel"), BRAND_CONFIG)
    assert better.score > basket.score


def test_brand_tier_resolves_levels():
    brands = BRAND_CONFIG["brands"]
    assert brand_tier("Sunspel", brands) == "better"
    assert brand_tier("BOSS", brands) == "basket"
    assert brand_tier("Jack & Jones", brands) == "worse"
    assert brand_tier("Obscure Local Co", brands) == "unknown"


def test_should_classify_must_buy_band():
    verdict = judge(_product(price=25.0), CONFIG)
    assert verdict.band == "must-buy"
    assert verdict.is_deal is True


def test_should_classify_good_offer_band():
    # €45 is above must-buy €30 but within good-offer €60
    verdict = judge(_product(price=45.0), CONFIG)
    assert verdict.band == "good"
    assert verdict.is_deal is True


def test_should_classify_regular_price_as_skip():
    # €80 is above good-offer €60 -> regular, not surfaced
    verdict = judge(_product(price=80.0), CONFIG)
    assert verdict.band == "regular"
    assert verdict.is_deal is False

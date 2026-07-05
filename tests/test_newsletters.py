"""Unit tests for the newsletter parser (no live inbox)."""

from __future__ import annotations

from dealscout.models import SaleEvent
from dealscout.newsletters import event_band, parse_newsletter

BRANDS = {"worse": ["Jack & Jones"], "basket": ["BOSS", "COS"], "better": ["Sunspel"]}
CONFIG = {"filters": {"min_brand_tier": "basket"}, "brands": BRANDS}

BOSS_EMAIL = (
    "BOSS <news@hugoboss.com>",
    "Summer Sale — up to 50% off shirts & knitwear",
    '<html><body>Enjoy <b>up to 50% off</b> shirts and knitwear. '
    '<a href="https://hugoboss.com/sale">Shop now</a></body></html>',
)


def test_parse_extracts_discount_categories_and_link():
    event = parse_newsletter(*BOSS_EMAIL)
    assert event is not None
    assert event.max_discount_pct == 50.0
    assert "shirt" in event.categories
    assert "knitwear" in event.categories
    assert event.brand == "BOSS"
    assert event.url == "https://hugoboss.com/sale"


def test_parse_returns_none_without_a_sale():
    event = parse_newsletter(
        "BOSS <news@hugoboss.com>",
        "New arrivals for spring",
        "<p>See the new collection</p>",
    )
    assert event is None


def test_event_band_must_look_for_deep_sale():
    assert event_band(parse_newsletter(*BOSS_EMAIL), CONFIG) == "must-look"


def test_event_band_good_for_moderate_sale():
    event = SaleEvent("COS", "30% off knitwear", 30.0, ("knitwear",), "http://x", "cos@x")
    assert event_band(event, CONFIG) == "good"


def test_event_band_skips_below_tier_brand():
    event = SaleEvent("Jack & Jones", "70% off everything", 70.0, ("tee",), "http://x", "jj@x")
    assert event_band(event, CONFIG) == "skip"

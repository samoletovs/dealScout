"""Unit tests for the SerpApi Google Shopping scanner (dealscout.serpsearch)."""

from __future__ import annotations

import asyncio

from dealscout.judge import judge
from dealscout.serpsearch import _allowed_source, _match_brand, build_products, scan

BRANDS = {"basket": ["BOSS", "GANT"], "better": ["Sunspel"], "worse": ["H&M"]}


def test_build_products_maps_price_reference_and_brand():
    results = [
        {
            "title": "BOSS wool jumper",
            "extracted_price": 89.0,
            "extracted_old_price": 200.0,
            "product_link": "https://g/1",
        },
        {"title": "Missing price item"},  # no extracted_price -> skipped
    ]

    products = build_products(results, "knitwear", "EUR", BRANDS)

    assert len(products) == 1
    p = products[0]
    assert (p.price, p.reference_price, p.currency) == (89.0, 200.0, "EUR")
    assert p.category == "knitwear" and p.url == "https://g/1"
    assert p.brand == "BOSS" and p.materials == {}


def test_build_products_handles_missing_old_price():
    products = build_products([{"title": "GANT knit", "extracted_price": 70.0}], "knitwear", "EUR", BRANDS)

    assert products[0].reference_price is None


def test_match_brand_is_case_insensitive_and_defaults_empty():
    assert _match_brand("boss WOOL jumper", BRANDS) == "BOSS"
    assert _match_brand("some noname tee", BRANDS) == ""


def test_scan_is_dormant_without_key_or_when_disabled():
    assert asyncio.run(scan({"serpapi": {"enabled": True}}, api_key=None)) == []
    assert asyncio.run(scan({"serpapi": {"enabled": False}}, api_key="k")) == []


def test_candidate_feeds_the_judge_with_fibre_gate_off():
    # A Shopping candidate has no materials; with the fibre gate off it still judges on
    # brand tier + price band (this is what run_serpapi does).
    product = build_products(
        [{"title": "BOSS wool jumper", "extracted_price": 45.0, "extracted_old_price": 150.0}],
        "knitwear",
        "EUR",
        BRANDS,
    )[0]
    config = {
        "filters": {"natural_fibre_min": 0, "min_brand_tier": "basket"},
        "deal": {
            "must_buy": {"knitwear": 50},
            "good_offer": {"knitwear": 85},
            "never_above": {"knitwear": 130},
        },
        "brands": BRANDS,
    }

    verdict = judge(product, config)

    assert verdict.is_deal is True
    assert verdict.band == "must-buy"


def test_build_products_captures_source_and_condition():
    results = [
        {"title": "BOSS wool jumper", "extracted_price": 89.0, "source": "Zalando"},
        {"title": "Restored BOSS jumper", "extracted_price": 50.0, "source": "Back Market"},
        {
            "title": "GANT knit",
            "extracted_price": 70.0,
            "source": "eBay - reseller",
            "second_hand_condition": "refurbished",
        },
    ]

    by_title = {p.title: p for p in build_products(results, "knitwear", "EUR", BRANDS)}

    assert by_title["BOSS wool jumper"].source == "Zalando"
    assert by_title["BOSS wool jumper"].condition == "new"
    assert by_title["Restored BOSS jumper"].condition == "used"  # from the title
    assert by_title["GANT knit"].condition == "refurbished"  # from the flag


def test_allowed_source_blocks_marketplaces_and_respects_allowlist():
    assert _allowed_source("eBay - amazing-wireless", [], ["ebay"]) is False  # reseller pattern
    assert _allowed_source("eBay", [], ["ebay"]) is False  # blocklist
    assert _allowed_source("Zalando", [], ["ebay"]) is True  # reputable, not blocked
    assert _allowed_source("Zalando", ["zalando"], []) is True  # on the allowlist
    assert _allowed_source("Best Buy", ["zalando"], []) is False  # allowlist set, not on it
    assert _allowed_source("", [], []) is True  # unknown store, no allowlist -> keep
    assert _allowed_source("", ["zalando"], []) is False  # unknown store, allowlist set -> drop

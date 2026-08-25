"""Unit tests for the scout — gathering hunt candidates from every source."""

from __future__ import annotations

import asyncio

from dealscout.models import Hunt, Product
from dealscout.scout import _match_brand, scout, shopping_products

HUNT = Hunt.from_dict(
    {
        "id": "boots-junior",
        "category": "football_boots",
        "brands": ["Nike", "adidas", "Puma"],
        "watch": ["https://www.sportsdirect.lv/kids-football-boots"],
        "queries": ["Nike Mercurial Superfly Elite junior"],
        "exclude": {"sources": ["dhgate"]},
    }
)


def _p(url: str) -> Product:
    return Product(
        title="Nike Jr. Mercurial Superfly 10 Elite AG",
        category="football_boots",
        price=60.0,
        reference_price=280.0,
        currency="EUR",
        url=url,
    )


def _hit(url: str, source: str = "11teamsports", price: float = 70.0) -> dict:
    return {
        "title": "Nike Jr. Mercurial Superfly 10 Elite AG",
        "extracted_price": price,
        "product_link": url,
        "source": source,
    }


def _patch_sources(monkeypatch, listing: list[Product], results: list[dict]) -> list[str]:
    """Replace both network sources; returns the list that records search queries."""
    queried: list[str] = []

    async def fake_listing(url, category, delay=1.0):
        return list(listing)

    async def fake_search(query, api_key, gl):
        queried.append(query)
        return list(results)

    monkeypatch.setattr("dealscout.scout.collect_listing", fake_listing)
    monkeypatch.setattr("dealscout.scout._search", fake_search)
    return queried


def test_should_map_a_shopping_result_into_a_candidate_product():
    [product] = shopping_products(
        [
            {
                "title": "Nike Jr. Mercurial Superfly 10 Elite AG",
                "extracted_price": 89.99,
                "extracted_old_price": 279.99,
                "product_link": "https://shop.eu/boot",
                "source": "11teamsports",
            }
        ],
        HUNT,
    )
    assert product.price == 89.99
    assert product.reference_price == 279.99
    assert product.brand == "Nike"
    assert product.currency == "EUR"
    assert product.category == "football_boots"


def test_should_never_infer_stock_from_a_shopping_result():
    # Shopping never states sizes; inventing availability here would be a lie.
    [product] = shopping_products([{"title": "Boot", "extracted_price": 50}], HUNT)
    assert product.sizes_known is False
    assert product.sizes == frozenset()


def test_should_skip_a_shopping_result_with_no_usable_price():
    noise = [{"title": "Boot"}, {"title": "Boot", "extracted_price": "n/a"}]
    assert shopping_products(noise, HUNT) == []


def test_should_fall_back_to_the_plain_link_when_there_is_no_product_link():
    [product] = shopping_products(
        [{"title": "Boot", "extracted_price": 50, "link": "https://x.eu/b"}], HUNT
    )
    assert product.url == "https://x.eu/b"


def test_should_mark_a_second_hand_listing_as_used():
    [product] = shopping_products(
        [{"title": "Nike Elite AG pre-owned", "extracted_price": 40}], HUNT
    )
    assert product.condition == "used"


def test_match_brand_should_return_the_first_hunt_brand_named_in_the_title():
    assert _match_brand("adidas Predator Elite FG", HUNT.brands) == "adidas"


def test_match_brand_should_return_nothing_for_a_brand_outside_the_hunt():
    assert _match_brand("Mizuno Morelia Neo IV Beta", HUNT.brands) == ""


def test_scout_should_merge_both_sources_and_drop_duplicate_urls(monkeypatch):
    _patch_sources(
        monkeypatch,
        listing=[_p("https://shop.eu/a"), _p("https://shop.eu/b")],
        results=[_hit("https://shop.eu/a"), _hit("https://shop.eu/c")],
    )
    products = asyncio.run(scout(HUNT, {}, api_key="test-key"))
    assert [p.url for p in products] == [
        "https://shop.eu/a",
        "https://shop.eu/b",
        "https://shop.eu/c",
    ]


def test_scout_should_drop_a_candidate_with_no_url(monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    _patch_sources(monkeypatch, listing=[_p(""), _p("https://shop.eu/a")], results=[])
    products = asyncio.run(scout(HUNT, {}, api_key=None))
    assert [p.url for p in products] == ["https://shop.eu/a"]


def test_scout_should_leave_the_paid_search_dormant_without_a_key(monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    queried = _patch_sources(
        monkeypatch, listing=[_p("https://shop.eu/a")], results=[_hit("https://shop.eu/z")]
    )
    products = asyncio.run(scout(HUNT, {}, api_key=None))
    assert [p.url for p in products] == ["https://shop.eu/a"]
    assert queried == []  # no key, no paid search


def test_scout_should_drop_a_blocked_seller(monkeypatch):
    _patch_sources(
        monkeypatch,
        listing=[],
        results=[
            _hit("https://dh.example/a", source="DHgate"),
            _hit("https://ok.example/b", source="Unisport"),
        ],
    )
    products = asyncio.run(scout(HUNT, {}, api_key="test-key"))
    assert [p.source for p in products] == ["Unisport"]


def test_scout_should_drop_a_marketplace_third_party_reseller(monkeypatch):
    _patch_sources(
        monkeypatch, listing=[], results=[_hit("https://e.example/a", source="eBay - joe_kicks")]
    )
    assert asyncio.run(scout(HUNT, {}, api_key="test-key")) == []


def test_scout_should_honour_a_preferred_store_allowlist(monkeypatch):
    _patch_sources(
        monkeypatch,
        listing=[],
        results=[
            _hit("https://a.eu/a", source="Unisport"),
            _hit("https://b.eu/b", source="Random Shop"),
        ],
    )
    config = {"serpapi": {"preferred_stores": ["Unisport"]}}
    products = asyncio.run(scout(HUNT, config, api_key="test-key"))
    assert [p.source for p in products] == ["Unisport"]


def test_scout_should_not_search_when_a_hunt_declares_no_queries(monkeypatch):
    queried = _patch_sources(
        monkeypatch, listing=[_p("https://shop.eu/a")], results=[_hit("https://shop.eu/z")]
    )
    hunt = Hunt.from_dict({"id": "watch-only", "category": "football_boots", "watch": ["https://x"]})
    products = asyncio.run(scout(hunt, {}, api_key="test-key"))
    assert [p.url for p in products] == ["https://shop.eu/a"]
    assert queried == []


def test_scout_should_respect_the_configured_result_limit(monkeypatch):
    _patch_sources(
        monkeypatch,
        listing=[],
        results=[_hit(f"https://shop.eu/{n}") for n in range(10)],
    )
    products = asyncio.run(scout(HUNT, {"serpapi": {"max_results": 3}}, api_key="test-key"))
    assert len(products) == 3

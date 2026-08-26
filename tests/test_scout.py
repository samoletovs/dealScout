"""Unit tests for the scout — gathering hunt candidates from every source."""

from __future__ import annotations

import asyncio

from dealscout.models import Hunt, Product
from dealscout.scout import (
    _match_brand,
    scout,
    shopping_products,
    title_confirms,
    title_plausible,
)

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

# Same hunt, but with the requirements that make the link pre-filter meaningful.
HUNT_REQ = Hunt.from_dict(
    {
        "id": "boots-junior",
        "category": "football_boots",
        "require": {"tier": ["elite"]},
        "exclude": {"models": ["Legend 10 Elite"]},
    }
)
HUNT_REQ_WATCH = Hunt.from_dict(
    {
        "id": "boots-junior",
        "category": "football_boots",
        "require": {"tier": ["elite"]},
        "watch": ["https://shop.eu/kids-sale"],
    }
)

# Two requirements, so a title must state both to count as confirmed.
HUNT_REQ_SOLE = Hunt.from_dict(
    {
        "id": "boots-junior",
        "category": "football_boots",
        "require": {"tier": ["elite"], "soleplate": ["AG", "FG"]},
    }
)
HUNT_SOLE_WATCH = Hunt.from_dict(
    {
        "id": "boots-junior",
        "category": "football_boots",
        "require": {"tier": ["elite"], "soleplate": ["AG", "FG"]},
        "watch": ["https://shop.eu/kids-sale"],
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
    """Replace every network source; returns the list that records search queries."""
    queried: list[str] = []

    async def fake_listing(url, category, delay=1.0):
        return list(listing)

    async def fake_links(url, delay=1.0):
        return []

    # The scout reads a listing page once and takes both products and links from it, so
    # this is the seam that must be stubbed. Leaving it real makes the suite hang on the
    # network — which is how this stub came to exist.
    async def fake_page(url, category, delay=1.0):
        return await fake_listing(url, category, delay), await fake_links(url, delay)

    async def fake_collect(item, title_hint=""):
        return None

    async def fake_search(query, api_key, gl):
        queried.append(query)
        return list(results)

    monkeypatch.setattr("dealscout.scout.collect_page", fake_page)
    monkeypatch.setattr("dealscout.scout.collect", fake_collect)
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


def test_title_plausible_should_keep_a_title_that_says_nothing():
    # Silence is not contradiction: a bare name must survive to be read properly.
    assert title_plausible("adidas F50 Kids", HUNT_REQ) is True


def test_title_plausible_should_reject_a_title_that_contradicts_a_requirement():
    assert title_plausible("adidas Predator Academy FG Kids", HUNT_REQ) is False


def test_title_plausible_should_keep_a_title_that_satisfies_the_requirement():
    assert title_plausible("adidas Predator Elite FG Kids", HUNT_REQ) is True


def test_title_plausible_should_reject_a_boot_already_owned():
    assert title_plausible("Nike Kids Legend 10 Elite FG", HUNT_REQ) is False


def test_title_plausible_should_ignore_brand_unless_the_hunt_gates_on_it():
    assert title_plausible("Skechers SKX 01 Elite FG", HUNT_REQ) is True


def test_title_plausible_should_reject_an_off_brand_title_when_brands_are_a_gate():
    # Saves the request budget for boots the hunt would actually buy.
    gated = Hunt.from_dict(
        {
            "id": "b",
            "category": "football_boots",
            "require": {"tier": ["elite"]},
            "brands": ["Nike", "adidas", "Puma"],
            "brands_only": True,
        }
    )
    assert title_plausible("Skechers SKX 01 Elite FG", gated) is False
    assert title_plausible("Puma Ultra 5 Ultimate FG Juniors", gated) is True


def test_scout_should_follow_listing_links_when_a_page_lists_no_products(monkeypatch):
    # Pro:Direct-style: the listing carries an ItemList of links, prices live on the PDP.
    fetched: list[str] = []

    async def fake_listing(url, category, delay=1.0):
        return []

    async def fake_links(url, delay=1.0):
        return [
            ("adidas Kids F50 Elite FG", "https://shop.eu/elite"),
            ("adidas Kids F50 Academy FG", "https://shop.eu/academy"),
        ]

    async def fake_collect(item, title_hint=""):
        fetched.append(item.url)
        return _p(item.url)

    async def fake_search(query, api_key, gl):
        return []

    async def fake_page(url, category, delay=1.0):
        return await fake_listing(url, category, delay), await fake_links(url, delay)

    monkeypatch.setattr("dealscout.scout.collect_page", fake_page)
    monkeypatch.setattr("dealscout.scout.collect", fake_collect)
    monkeypatch.setattr("dealscout.scout._search", fake_search)

    products = asyncio.run(scout(HUNT_REQ_WATCH, {"scrape": {"delay_seconds": 0}}, api_key=None))
    # The Academy link is discarded on its title alone — never worth a request.
    assert fetched == ["https://shop.eu/elite"]
    assert [p.url for p in products] == ["https://shop.eu/elite"]


def test_scout_should_cap_how_many_product_pages_one_listing_costs(monkeypatch):
    fetched: list[str] = []

    async def fake_listing(url, category, delay=1.0):
        return []

    async def fake_links(url, delay=1.0):
        return [(f"adidas F50 Elite FG {n}", f"https://shop.eu/{n}") for n in range(20)]

    async def fake_collect(item, title_hint=""):
        fetched.append(item.url)
        return _p(item.url)

    async def fake_search(query, api_key, gl):
        return []

    async def fake_page(url, category, delay=1.0):
        return await fake_listing(url, category, delay), await fake_links(url, delay)

    monkeypatch.setattr("dealscout.scout.collect_page", fake_page)
    monkeypatch.setattr("dealscout.scout.collect", fake_collect)
    monkeypatch.setattr("dealscout.scout._search", fake_search)

    config = {"scrape": {"delay_seconds": 0, "link_budget": 4}}
    asyncio.run(scout(HUNT_REQ_WATCH, config, api_key=None))
    assert len(fetched) == 4


def test_title_confirms_should_recognise_a_title_that_states_everything_required():
    assert title_confirms("adidas Kids F50 Elite FG", HUNT_REQ_SOLE) is True


def test_title_confirms_should_reject_a_title_that_leaves_a_requirement_unstated():
    assert title_confirms("adidas Kids Copa 19.4 FG", HUNT_REQ_SOLE) is False


def test_scout_should_spend_its_budget_on_the_titles_that_already_look_right(monkeypatch):
    # A listing sorted cheapest-first puts takedown models on top; without ordering, the
    # whole budget goes to EUR 15 junk and the Elite boots are never seen.
    fetched: list[str] = []

    async def fake_listing(url, category, delay=1.0):
        return []

    async def fake_links(url, delay=1.0):
        return [
            ("adidas Kids Copa 19.4 FG", "https://shop.eu/junk1"),
            ("adidas Kids X Speedflow .4 FxG", "https://shop.eu/junk2"),
            ("adidas Kids F50 Elite FG", "https://shop.eu/elite"),
        ]

    async def fake_collect(item, title_hint=""):
        fetched.append(item.url)
        return _p(item.url)

    async def fake_search(query, api_key, gl):
        return []

    async def fake_page(url, category, delay=1.0):
        return await fake_listing(url, category, delay), await fake_links(url, delay)

    monkeypatch.setattr("dealscout.scout.collect_page", fake_page)
    monkeypatch.setattr("dealscout.scout.collect", fake_collect)
    monkeypatch.setattr("dealscout.scout._search", fake_search)

    config = {"scrape": {"delay_seconds": 0, "link_budget": 1}}
    asyncio.run(scout(HUNT_SOLE_WATCH, config, api_key=None))
    assert fetched == ["https://shop.eu/elite"]

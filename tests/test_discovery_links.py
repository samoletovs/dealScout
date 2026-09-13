"""Paid discovery must produce retailer links, not replay Google search URLs."""

import asyncio
import json
from unittest.mock import AsyncMock

from dealscout import discovery


def test_prefers_direct_merchant_link_and_removes_search_and_token_parameters():
    item = {
        "product_link": "https://www.google.com/search?q=private-query&ibp=oshop",
        "link": "https://shop.example/product?variant=123&utm_source=google&api_key=secret&q=private-query#token",
    }
    assert discovery.retailer_link(item) == "https://shop.example/product?variant=123"


def test_google_only_results_do_not_become_retained_or_fetched_retailer_links():
    for link in (
        "https://www.google.com/search?q=private-query",
        "https://www.google.de/shopping/product/123?q=private-query",
        "https://serpapi.com/search.json?api_key=secret",
        "https://user:secret@shop.example/product",
    ):
        assert discovery.retailer_link({"product_link": link}) == ""


def test_product_identity_survives_canonicalization():
    assert discovery.retailer_link({
        "link": "https://shop.example/boot?sku=AG-123&fbclid=tracking#colcode=01234",
    }) == "https://shop.example/boot?sku=AG-123#colcode=01234"


def test_successful_discovery_persists_no_search_page_or_query_echo(monkeypatch):
    monkeypatch.setattr(discovery, "_get_json", AsyncMock(return_value={
        "search_metadata": {"status": "Success", "id": "abcdef123456abcdef123456"},
        "search_parameters": {"api_key": "secret"},
        "shopping_results": [
            {"title": "Boot", "extracted_price": 45,
             "product_link": "https://www.google.com/search?q=private-query"},
            {"title": "Boot", "extracted_price": 55, "source": "Shop",
             "link": "https://shop.example/boot?api_key=secret&q=private-query",
             "product_link": "https://www.google.com/search?q=private-query"},
        ],
    }))

    results = asyncio.run(discovery.search("private-query", "secret", "de"))

    assert len(results) == 1
    assert results[0]["link"] == "https://shop.example/boot"
    serialized = json.dumps(results)
    assert "private-query" not in serialized
    assert "secret" not in serialized
    assert "google.com" not in serialized

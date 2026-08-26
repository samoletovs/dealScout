"""Unit tests for reading a Magento storefront through its GraphQL API (no network)."""

from __future__ import annotations

import json

from dealscout.magento import (
    batched,
    build_products_query,
    parse_graphql_products,
    query_url,
    sitemap_product_keys,
)

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://sportland.lv/product/adidas_predator_elite_fg_if8867</loc></url>
  <url><loc>https://sportland.lv/product/nike_phantom_gx_2_elite_hm1234</loc></url>
  <url><loc>https://sportland.lv/viriesu/apavi/futbola-apavi</loc></url>
  <url><loc>https://sportland.lv/product/adidas_predator_elite_fg_if8867</loc></url>
</urlset>"""


def _response(*items: dict) -> str:
    return json.dumps({"data": {"products": {"items": list(items)}}})


def _item(
    name: str,
    url_key: str,
    final: float,
    regular: float,
    stock: str = "IN_STOCK",
    sizes: list[str] | None = None,
) -> dict:
    node = {
        "name": name,
        "url_key": url_key,
        "stock_status": stock,
        "price_range": {
            "minimum_price": {
                "final_price": {"value": final, "currency": "EUR"},
                "regular_price": {"value": regular},
            }
        },
    }
    if sizes is not None:
        node["configurable_options"] = [
            {"attribute_code": "footwear_size", "values": [{"label": s} for s in sizes]}
        ]
    return node


def test_should_take_product_keys_from_a_sitemap_and_skip_category_urls():
    keys = sitemap_product_keys(SITEMAP)
    assert keys == ["adidas_predator_elite_fg_if8867", "nike_phantom_gx_2_elite_hm1234"]


def test_should_return_no_keys_for_an_unreadable_sitemap():
    assert sitemap_product_keys("") == []
    assert sitemap_product_keys("<html>not a sitemap</html>") == []


def test_should_quote_url_keys_into_the_query():
    query = build_products_query(["a_b", "c_d"])
    assert '"a_b","c_d"' in query
    assert "url_key" in query


def test_query_url_should_percent_encode_the_query():
    url = query_url("https://sportland.lv/graphql", ["a_b"])
    assert url.startswith("https://sportland.lv/graphql?query=")
    assert " " not in url and "{" not in url


def test_should_read_price_and_rrp_from_a_graphql_response():
    payload = _response(_item("ADIDAS PREDATOR ELITE FG", "adidas_predator_elite_fg", 104.0, 260.0))
    [boot] = parse_graphql_products(payload, "https://sportland.lv", "football_boots")
    assert boot.title == "ADIDAS PREDATOR ELITE FG"
    assert boot.price == 104.0
    assert boot.reference_price == 260.0
    assert boot.url == "https://sportland.lv/product/adidas_predator_elite_fg"
    assert boot.source == "sportland.lv"


def test_should_not_invent_an_rrp_when_the_product_is_not_discounted():
    payload = _response(_item("ADIDAS F50 ELITE", "f50", 260.0, 260.0))
    [boot] = parse_graphql_products(payload, "https://sportland.lv", "football_boots")
    assert boot.reference_price is None


def test_should_drop_a_product_that_is_out_of_stock():
    payload = _response(_item("GONE ELITE", "gone", 99.0, 300.0, stock="OUT_OF_STOCK"))
    assert parse_graphql_products(payload, "https://sportland.lv", "x") == []


def test_should_report_sizes_as_unknown_even_when_the_size_list_is_published():
    # The response says which sizes the boot is *offered* in and how many variants are in
    # stock, but not which variant is which size. Pairing them by position would be a
    # confident wrong answer about whether a boot exists in EU 37, so sizes stay unknown
    # and the judge caps the find at "verify on click".
    payload = _response(
        _item("NIKE PHANTOM ELITE", "phantom", 99.0, 330.0, sizes=["37 1/3", "42", "44"])
    )
    [boot] = parse_graphql_products(payload, "https://sportland.lv", "football_boots")
    assert boot.sizes_known is False
    assert boot.sizes == frozenset()


def test_should_ignore_a_payload_that_is_not_a_graphql_product_response():
    assert parse_graphql_products("<html>", "https://sportland.lv", "x") == []
    assert parse_graphql_products('{"errors":[{"message":"nope"}]}', "https://sportland.lv", "x") == []


def test_should_skip_an_item_with_no_readable_price():
    payload = json.dumps(
        {"data": {"products": {"items": [{"name": "X", "url_key": "x", "price_range": {}}]}}}
    )
    assert parse_graphql_products(payload, "https://sportland.lv", "x") == []


def test_batched_should_split_a_catalogue_into_request_sized_chunks():
    assert list(batched(["a", "b", "c"], 2)) == [["a", "b"], ["c"]]
    assert list(batched([], 2)) == []
    # A nonsense batch size must not loop forever.
    assert list(batched(["a", "b"], 0)) == [["a"], ["b"]]

"""Unit tests for the dealScout collector parsers (no network)."""

from __future__ import annotations

from dealscout.collector import parse_ldjson_product, parse_materials

_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Merino Crew",
 "material":"100% merino wool",
 "offers":{"@type":"Offer","price":"49.90","priceCurrency":"EUR",
           "availability":"https://schema.org/InStock"}}
</script>
</head><body>Merino Crew - 100% merino wool</body></html>
"""

_HTML_NO_LDJSON = "<html><body>Just a page, no structured data.</body></html>"


def test_parse_materials_splits_composition():
    assert parse_materials("80% cotton, 20% polyester") == {"cotton": 0.8, "polyester": 0.2}


def test_parse_materials_keeps_multiword_fibre():
    assert parse_materials("100% virgin wool") == {"virgin wool": 1.0}


def test_parse_ldjson_extracts_price_and_currency():
    product = parse_ldjson_product(_HTML, "https://shop.example/merino", "knitwear")
    assert product is not None
    assert product.title == "Merino Crew"
    assert product.price == 49.90
    assert product.currency == "EUR"


def test_parse_ldjson_extracts_material():
    product = parse_ldjson_product(_HTML, "https://shop.example/merino", "knitwear")
    assert product is not None
    assert "wool" in next(iter(product.materials))


def test_parse_ldjson_returns_none_without_structured_data():
    assert parse_ldjson_product(_HTML_NO_LDJSON, "https://shop.example/x", "tee") is None

"""Unit tests for the dealScout collector parsers (no network)."""

from __future__ import annotations

import asyncio

from dealscout.collector import (
    _ROBOTS,
    enrich,
    parse_ldjson_links,
    _money,
    collect,
    parse_html_links,
    parse_html_product,
    parse_ldjson_product,
    parse_ldjson_products,
    parse_materials,
    parse_shopify_products,
    robots_allows,
)
from dealscout.models import Product, WatchItem


async def _always_allowed(url, agent="*"):
    return True


def _serves(html):
    """A stand-in for collector.fetch that always serves the same page."""

    async def _fetch(url, timeout=20.0, retries=1):
        return html

    return _fetch


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

# --- ProductGroup, ItemList and PDP enrichment (added with the hunt engine) ---

_GROUP_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"ProductGroup",
 "name":"adidas Kids F50 Elite FG","brand":{"@type":"Brand","name":"adidas"},
 "hasVariant":[
   {"@type":"Product","name":"adidas Kids F50 Elite FG - 36.5",
    "offers":{"@type":"Offer","price":"89.00","priceCurrency":"EUR",
              "availability":"http://schema.org/InStock"}},
   {"@type":"Product","name":"adidas Kids F50 Elite FG - 37.5",
    "offers":{"@type":"Offer","price":"89.00","priceCurrency":"EUR",
              "availability":"http://schema.org/OutOfStock"}},
   {"@type":"Product","name":"adidas Kids F50 Elite FG - 38",
    "offers":{"@type":"Offer","price":"129.00","priceCurrency":"EUR",
              "availability":"http://schema.org/InStock"}}]}
</script></head><body>x</body></html>
"""

_GROUP_UK_HTML = _GROUP_HTML.replace("36.5", "4").replace("37.5", "4.5").replace(" - 38", " - 5")

_LIST_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"ItemList","itemListElement":[
 {"@type":"ListItem","position":"1","name":"adidas Kids F50 Elite FG",
  "url":"/products/adidas-kids-f50-elite-fg-1026183"},
 {"@type":"ListItem","position":"2","name":"Puma Kids Future 7 Play FG",
  "url":"/products/puma-kids-future-7-play-1019345"},
 {"@type":"ListItem","position":"3","name":"duplicate",
  "url":"/products/adidas-kids-f50-elite-fg-1026183"}]}
</script></head><body>x</body></html>
"""


def test_should_read_a_product_group_as_one_product_not_one_per_size():
    products = parse_ldjson_products(_GROUP_HTML, "https://shop.eu/p", "football_boots")
    assert len(products) == 1
    assert products[0].title == "adidas Kids F50 Elite FG"


def test_should_take_the_cheapest_variant_price_and_the_dearest_as_the_rrp():
    [product] = parse_ldjson_products(_GROUP_HTML, "https://shop.eu/p", "football_boots")
    assert product.price == 89.00
    assert product.reference_price == 129.00


def test_should_read_only_the_in_stock_variant_sizes():
    [product] = parse_ldjson_products(_GROUP_HTML, "https://shop.eu/p", "football_boots")
    assert product.sizes_known is True
    assert product.sizes == frozenset({"36.5", "38"})


def test_should_treat_a_uk_size_variant_table_as_unknown():
    # Sizes 4/4.5/5 are UK. Reading them as EU would reject every boot that fits.
    [product] = parse_ldjson_products(_GROUP_UK_HTML, "https://shop.eu/p", "football_boots")
    assert product.sizes_known is False
    assert product.sizes == frozenset()


def test_should_read_the_links_of_a_listing_that_publishes_no_products():
    links = parse_ldjson_links(_LIST_HTML, "https://shop.eu/collections/kids-sale")
    assert links == [
        ("adidas Kids F50 Elite FG",
         "https://shop.eu/products/adidas-kids-f50-elite-fg-1026183"),
        ("Puma Kids Future 7 Play FG",
         "https://shop.eu/products/puma-kids-future-7-play-1019345"),
    ]


def test_should_find_no_links_on_a_page_without_an_item_list():
    assert parse_ldjson_links(_HTML, "https://shop.example/merino") == []


_BREADCRUMB_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[
 {"@type":"ListItem","position":1,"name":"Home","item":"https://shop.eu/"},
 {"@type":"ListItem","position":2,"name":"Football","item":"https://shop.eu/football"}]}
</script></head><body>x</body></html>
"""


def test_should_not_follow_breadcrumb_links_as_if_they_were_products():
    # Breadcrumbs are ListItems too; following them costs a request per level for nothing.
    assert parse_ldjson_links(_BREADCRUMB_HTML, "https://shop.eu/football/boots") == []


_FLAT_CRUMB_HTML = _BREADCRUMB_HTML.replace("BreadcrumbList", "ItemList")


def test_should_drop_an_ancestor_link_even_when_it_is_typed_as_a_plain_item_list():
    # Some retailers publish breadcrumbs as an ItemList. A product page is never an
    # ancestor path of the listing you found it on.
    assert parse_ldjson_links(_FLAT_CRUMB_HTML, "https://shop.eu/football/boots/kids") == []


def _boot(**overrides) -> Product:
    base = dict(
        title="adidas Predator Elite FG Kids", category="football_boots", price=44.95,
        reference_price=None, currency="EUR", url="https://shop.eu/p",
    )
    base.update(overrides)
    return Product(**base)


_STOCK_PAGE = (
    '<html><body><script>var d = "{'
    '\\"stock\\":[{\\"name\\":\\"EU 37\\",\\"availability\\":\\"in stock\\",'
    '\\"price\\":\\"44.95\\",\\"recommended_retail_price\\":\\"119.95\\"}]'
    '}";</script></body></html>'
)


def test_enrich_should_fill_in_the_sizes_and_rrp_a_listing_omitted(monkeypatch):
    monkeypatch.setattr("dealscout.collector.robots_allows", _always_allowed)
    monkeypatch.setattr("dealscout.collector.fetch", _serves(_STOCK_PAGE))
    enriched = asyncio.run(enrich(_boot(), delay=0))
    assert enriched.sizes_known is True
    assert enriched.sizes == frozenset({"37"})
    assert enriched.reference_price == 119.95


def test_enrich_should_never_overwrite_what_the_listing_already_knew(monkeypatch):
    monkeypatch.setattr("dealscout.collector.robots_allows", _always_allowed)
    monkeypatch.setattr("dealscout.collector.fetch", _serves(_STOCK_PAGE))
    known = _boot(sizes=frozenset({"38"}), sizes_known=True, reference_price=200.0)
    enriched = asyncio.run(enrich(known, delay=0))
    assert enriched.sizes == frozenset({"38"})
    assert enriched.reference_price == 200.0


def test_enrich_should_return_the_product_untouched_when_the_page_is_silent(monkeypatch):
    monkeypatch.setattr("dealscout.collector.robots_allows", _always_allowed)
    monkeypatch.setattr("dealscout.collector.fetch", _serves("<html>nothing</html>"))
    product = _boot()
    assert asyncio.run(enrich(product, delay=0)) == product


def test_enrich_should_not_fetch_a_page_robots_txt_disallows(monkeypatch):
    called: list[str] = []

    async def refuse(url, agent="*"):
        return False

    async def record(url, timeout=20.0, retries=1):
        called.append(url)
        return _STOCK_PAGE

    monkeypatch.setattr("dealscout.collector.robots_allows", refuse)
    monkeypatch.setattr("dealscout.collector.fetch", record)
    product = _boot()
    assert asyncio.run(enrich(product, delay=0)) == product
    assert called == []


def test_robots_allows_should_honour_a_disallow_rule(monkeypatch):
    monkeypatch.setattr(
        "dealscout.collector.fetch", _serves("User-agent: *\nDisallow: /private/")
    )
    _ROBOTS.clear()
    assert asyncio.run(robots_allows("https://shop.eu/private/x")) is False
    assert asyncio.run(robots_allows("https://shop.eu/public/x")) is True


def test_robots_allows_should_fail_open_when_robots_txt_is_unreadable(monkeypatch):
    monkeypatch.setattr("dealscout.collector.fetch", _serves(None))
    _ROBOTS.clear()
    assert asyncio.run(robots_allows("https://shop.eu/anything")) is True

# --- HTML fallback for retailers that publish no ld+json Product (sportsdirect.lv) ---

_SD_TILE = """
<html><body>
<a href="/puma-ultra-5-ultimate-firm-ground-football-boots-juniors-084390#colcode=08439011">
  <span class="productdescriptionbrand">Puma</span>
  <span class="productdescriptionname">Ultra 5 Ultimate Firm Ground Football Boots Juniors</span>
</a>
<a href="/football/football-boots">Back to boots</a>
</body></html>
"""

_SD_PDP = """
<html><head><meta property="og:title" content="Puma Ultra 5 Ultimate FG Juniors" /></head>
<body>
<span id="lblTicketPrice">252,00 &#x20AC;</span>
<span id="lblSellingPrice">58,20 &#x20AC;</span>
<select id="sizeDdl">
  <option value="0">Choose</option>
  <option value="4 (36.5)" class="greyOut" data-stock-qty="0">4 (36.5)</option>
  <option value="4.5 (37)" class="" data-stock-qty="3">4.5 (37)</option>
</select>
</body></html>
"""


def test_should_find_product_links_on_a_listing_with_no_structured_data():
    # The #colcode fragment is kept deliberately: it identifies the colourway, and two
    # colourways of one boot have different stock (see monitor.canonical_url).
    links = parse_html_links(_SD_TILE, "https://sd.lv/football/football-boots/kids")
    assert links == [
        ("Puma Ultra 5 Ultimate Firm Ground Football Boots Juniors",
         "https://sd.lv/puma-ultra-5-ultimate-firm-ground-football-boots-juniors-084390"
         "#colcode=08439011")
    ]


def test_should_read_the_tile_name_from_a_later_anchor_when_the_first_is_an_image():
    # A tile links the same product twice: image first, then text. Reading only the first
    # returns an empty name, and a nameless link cannot be pre-filtered.
    page = """
    <a href="/puma-ultra-5-ultimate-boots-084390"><img src="x.jpg"></a>
    <a href="/puma-ultra-5-ultimate-boots-084390">
      <span class="productdescriptionbrand">Puma</span>
      <span class="productdescriptionname">Ultra 5 Ultimate FG Juniors</span>
    </a>
    """
    assert parse_html_links(page, "https://sd.lv/football/boots") == [
        ("Puma Ultra 5 Ultimate FG Juniors", "https://sd.lv/puma-ultra-5-ultimate-boots-084390")
    ]


def test_should_not_mistake_a_navigation_link_for_a_product():
    # "/football/football-boots" has no product id and is an ancestor of the listing.
    links = parse_html_links(_SD_TILE, "https://sd.lv/football/football-boots/kids")
    assert all("football-boots" != l[1].rsplit("/", 1)[-1] for l in links)


def test_should_read_price_rrp_and_sizes_from_a_page_with_no_ldjson():
    product = parse_html_product(_SD_PDP, "https://sd.lv/p-084390", "football_boots")
    assert product is not None
    assert product.price == 58.20
    assert product.reference_price == 252.00
    assert product.title == "Puma Ultra 5 Ultimate FG Juniors"
    assert product.sizes_known is True
    assert product.sizes == frozenset({"37"})


def test_should_refuse_to_invent_a_product_when_there_is_no_price():
    assert parse_html_product("<html><body>no price</body></html>", "https://sd.lv/p", "x") is None


def test_should_ignore_an_rrp_that_is_not_above_the_selling_price():
    page = _SD_PDP.replace("252,00", "50,00")
    assert parse_html_product(page, "https://sd.lv/p", "football_boots").reference_price is None


def test_should_read_a_comma_decimal_price():
    assert _money("167,39 \u20ac") == 167.39


def test_should_read_a_dot_decimal_price_with_a_thousands_comma():
    assert _money("1,234.50") == 1234.50


def test_should_read_a_price_with_no_decimals():
    assert _money("&#x20AC; 72") == 72.0

def test_collect_should_take_the_listing_name_when_it_is_the_same_name_with_more_of_it(monkeypatch):
    # Sports Direct's tile says "Puma Ultra 5 Ultimate..."; its own page title drops the
    # brand. Losing it means a brand-gated hunt rejects a boot it should buy.
    monkeypatch.setattr("dealscout.collector.robots_allows", _always_allowed)
    monkeypatch.setattr("dealscout.collector.fetch", _serves(_SD_PDP))
    item = WatchItem(url="https://sd.lv/p-084390", category="football_boots")
    product = asyncio.run(collect(item, title_hint="Puma Ultra 5 Ultimate FG Juniors"))
    assert product.title == "Puma Ultra 5 Ultimate FG Juniors"


def test_collect_should_ignore_a_listing_name_for_a_different_product(monkeypatch):
    monkeypatch.setattr("dealscout.collector.robots_allows", _always_allowed)
    monkeypatch.setattr("dealscout.collector.fetch", _serves(_SD_PDP))
    item = WatchItem(url="https://sd.lv/p-084390", category="football_boots")
    product = asyncio.run(collect(item, title_hint="adidas Predator Elite FG"))
    assert product.title == "Puma Ultra 5 Ultimate FG Juniors"  # the page's own title

# --- Shopify collection endpoint (komanda.lv, the local adidas dealer) ---

_SHOPIFY = """{"products":[
 {"title":"Predator Elite FT FG J","handle":"predator-elite-ft-fg-j","vendor":"adidas",
  "variants":[
   {"title":"36","price":"130.00","compare_at_price":"260.00","available":true},
   {"title":"37","price":"130.00","compare_at_price":"260.00","available":false},
   {"title":"38","price":"130.00","compare_at_price":"260.00","available":true}]},
 {"title":"adidas Copa Pure IV Elite FG","handle":"copa-pure-iv","vendor":"adidas",
  "variants":[{"title":"42","price":"240.00","compare_at_price":null,"available":true}]},
 {"title":"No variants","handle":"nope","vendor":"adidas","variants":[]}]}"""


def test_should_read_a_shopify_collection_payload():
    products = parse_shopify_products(_SHOPIFY, "https://komanda.lv/collections/fg/products.json",
                                      "football_boots")
    assert [p.title for p in products] == [
        "adidas Predator Elite FT FG J", "adidas Copa Pure IV Elite FG"
    ]


def test_should_read_only_the_available_shopify_variants_as_stock():
    [boot, _] = parse_shopify_products(_SHOPIFY, "https://komanda.lv/c/products.json", "x")
    assert boot.sizes_known is True
    assert boot.sizes == frozenset({"36", "38"})  # 37 exists but is unavailable


def test_should_take_the_shopify_compare_at_price_as_the_rrp():
    [boot, other] = parse_shopify_products(_SHOPIFY, "https://komanda.lv/c/products.json", "x")
    assert boot.price == 130.00
    assert boot.reference_price == 260.00
    assert other.reference_price is None


def test_should_build_the_shopify_product_url_from_the_handle():
    [boot, _] = parse_shopify_products(_SHOPIFY, "https://komanda.lv/c/products.json", "x")
    assert boot.url == "https://komanda.lv/products/predator-elite-ft-fg-j"


def test_should_not_prefix_a_vendor_already_present_in_the_shopify_title():
    [_, other] = parse_shopify_products(_SHOPIFY, "https://komanda.lv/c/products.json", "x")
    assert other.title == "adidas Copa Pure IV Elite FG"


def test_should_not_prefix_a_vendor_that_is_just_the_shop_name():
    # komanda.lv sets `vendor` to itself; prefixing it pollutes brand matching.
    payload = _SHOPIFY.replace('"vendor":"adidas"', '"vendor":"komanda.lv"')
    [boot, _] = parse_shopify_products(payload, "https://komanda.lv/c/products.json", "x")
    assert boot.title == "Predator Elite FT FG J"


def test_should_ignore_a_payload_that_is_not_a_shopify_collection():
    assert parse_shopify_products("<html>not json</html>", "https://x.lv/", "x") == []
    assert parse_shopify_products('{"items":[]}', "https://x.lv/", "x") == []

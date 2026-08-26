"""Unit tests for the dealScout collector parsers (no network)."""

from __future__ import annotations

import asyncio
import json

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
    parse_product_tiles,
    parse_shopify_products,
    robots_allows,
    title_from_slug,
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


def test_should_read_half_sizes_from_a_shopify_payload_that_prints_them_as_glyphs():
    # Pro:Direct's products.json prints half sizes as "37½". Dropping them lost exactly
    # the size the junior hunt wants, so an in-stock 37½ Elite boot was invisible.
    payload = json.dumps(
        {
            "products": [
                {
                    "title": "Puma Future Ultimate FG/AG",
                    "handle": "puma-future-ultimate-fg-ag",
                    "vendor": "Puma",
                    "variants": [
                        {"title": "37", "price": "65.00", "compare_at_price": "220.00",
                         "available": True},
                        {"title": "37\u00bd", "price": "65.00", "compare_at_price": "220.00",
                         "available": True},
                        {"title": "38\u00bd", "price": "65.00", "compare_at_price": "220.00",
                         "available": False},
                    ],
                }
            ]
        }
    )
    [boot] = parse_shopify_products(
        payload, "https://www.prodirectsport.ie/c/products.json", "football_boots"
    )
    assert boot.sizes_known is True
    assert boot.sizes == frozenset({"37", "37.5"})  # 38.5 is listed but out of stock


def test_title_from_slug_should_recover_a_name_from_an_seo_url():
    assert (
        title_from_slug("https://www.futbola-apavi.lv/nike-tiempo-maestro-elite-fg-42559")
        == "nike tiempo maestro elite fg"
    )
    assert (
        title_from_slug("https://shop.example/store/adidas-f50-elite-ag-100234.html")
        == "adidas f50 elite ag"
    )


def test_title_from_slug_should_return_empty_when_the_slug_is_not_a_name():
    assert title_from_slug("https://shop.example/12345678") == ""
    assert title_from_slug("https://shop.example/boots") == ""
    assert title_from_slug("https://shop.example/") == ""


def test_should_fall_back_to_the_slug_when_a_listing_tile_has_no_text():
    # An OpenCart listing (futbola-apavi.lv) renders bare image anchors. Without a name
    # the link cannot be pre-filtered, and under `brands_only` it is actively rejected —
    # so the whole retailer silently yielded nothing.
    html = (
        '<a href="/nike-phantom-6-low-elite-fg-42731"><img src="a.jpg"></a>'
        '<a href="/adidas-predator-elite-ag-42732"><img src="b.jpg"></a>'
    )
    links = parse_html_links(html, "https://www.futbola-apavi.lv/futbola-apavi")
    assert links == [
        ("nike phantom 6 low elite fg", "https://www.futbola-apavi.lv/nike-phantom-6-low-elite-fg-42731"),
        ("adidas predator elite ag", "https://www.futbola-apavi.lv/adidas-predator-elite-ag-42732"),
    ]


def test_should_prefer_real_tile_text_over_the_slug():
    html = (
        '<a href="/nike-phantom-6-low-elite-fg-42731"></a>'
        '<div class="productdescriptionbrand">Nike</div>'
        '<div class="productdescriptionname">Phantom 6 Low Elite FG</div>'
    )
    [(name, _)] = parse_html_links(html, "https://www.sportsdirect.lv/football/")
    assert name == "Nike Phantom 6 Low Elite FG"


def test_should_find_shopware_style_product_links_without_a_numeric_id():
    # 11teamsports (Shopware) has no id in the URL — a product is just `/p/<slug>`, so
    # the id-based matcher found only the few whose colour code held five digits.
    html = (
        '<a href="/de-de/p/adidas-predator-elite-ft-fg-kids-schwarz">x</a>'
        '<a href="/de-de/fussballschuhe/kinder-fussballschuhe/">nav</a>'
    )
    links = parse_html_links(html, "https://www.11teamsports.com/de-de/sale/fussballschuhe/")
    assert links == [
        (
            "adidas predator elite ft fg kids schwarz",
            "https://www.11teamsports.com/de-de/p/adidas-predator-elite-ft-fg-kids-schwarz",
        )
    ]


def _size_variant_page(*rows: tuple[str, str]) -> str:
    """A Shopware-shaped page: one whole ld+json Product block per size."""
    blocks = "".join(
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "Product",
                "name": "adidas Predator Elite FT FG Kids",
                "url": "https://www.11teamsports.com/de-de/p/predator-elite",
                "size": size,
                "offers": {
                    "@type": "Offer",
                    "price": "139.95",
                    "priceCurrency": "EUR",
                    "availability": f"https://schema.org/{stock}",
                },
            }
        )
        + "</script>"
        for size, stock in rows
    )
    return f"<html><body>{blocks}</body></html>"


def test_should_merge_per_size_ldjson_blocks_into_one_product():
    # 11teamsports emits one complete Product block per size, all sharing a name and URL.
    # Treating them as duplicates kept only the first — which is usually out of stock —
    # so a boot genuinely available in EU 37 reported no sizes at all.
    html = _size_variant_page(
        ("29", "OutOfStock"), ("37", "InStock"), ("37,5", "InStock"), ("38", "OutOfStock")
    )
    [boot] = parse_ldjson_products(html, "https://www.11teamsports.com/de-de/p/x", "football_boots")
    assert boot.sizes_known is True
    assert boot.sizes == frozenset({"37", "37.5"})


def test_should_not_report_stock_when_every_size_variant_is_out_of_stock():
    # The distinction that matters: "stated, and none available" must not read as
    # "unknown", or an out-of-stock boot resurfaces every run as "verify on click".
    html = _size_variant_page(("37", "OutOfStock"), ("38", "OutOfStock"))
    [boot] = parse_ldjson_products(html, "https://www.11teamsports.com/de-de/p/x", "football_boots")
    assert boot.sizes_known is True
    assert boot.sizes == frozenset()


def _tile(name: str, href: str, price: str, old: str = "") -> str:
    """One Magento product tile: the name and the price are separate elements."""
    was = (
        f'<span data-price-amount="{old}" data-price-type="oldPrice"></span>' if old else ""
    )
    return (
        f'<div class="product-item-info">'
        f'<strong class="product-item-name">'
        f'<a class="product-item-link" href="{href}"> {name} </a></strong>'
        f'<div class="price-box">{was}'
        f'<span data-price-amount="{price}" data-price-type="finalPrice"></span></div>'
        f"</div>"
    )


def test_should_read_magento_tiles_when_a_listing_has_no_structured_data():
    # teamsport.lv publishes no ld+json and loads the *detail* page price over AJAX, so
    # the listing tiles are the only readable statement of price the site offers.
    html = (
        _tile("ZM VAPOR 16 ELITE KM FG", "https://www.teamsport.lv/lv_lv/a-1", "120.00", "350.00")
        + _tile("LEGEND 10 ELITE AG-PRO", "https://www.teamsport.lv/lv_lv/b-2", "120.00")
    )
    boots = parse_product_tiles(html, "https://www.teamsport.lv/lv_lv/futbols/", "football_boots")
    assert [b.title for b in boots] == ["ZM VAPOR 16 ELITE KM FG", "LEGEND 10 ELITE AG-PRO"]
    assert boots[0].price == 120.00
    assert boots[0].reference_price == 350.00
    assert boots[1].reference_price is None
    assert boots[0].source == "teamsport.lv"


def test_should_not_pair_a_tile_name_with_a_neighbouring_tiles_price():
    # The whole safety of this reader. A product page carries several related-item
    # carousels built from identical markup, so a scan that ignored tile boundaries would
    # confidently attach the wrong boot's price — a wrong answer, not a missing one.
    html = (
        _tile("CHEAP TAKEDOWN FG", "https://shop.example/cheap", "55.00")
        + _tile("SUPERFLY ELITE FG", "https://shop.example/elite", "289.00")
    )
    boots = parse_product_tiles(html, "https://shop.example/list", "football_boots")
    prices = {b.title: b.price for b in boots}
    assert prices == {"CHEAP TAKEDOWN FG": 55.00, "SUPERFLY ELITE FG": 289.00}


def test_should_skip_a_tile_that_states_no_price():
    html = (
        '<div class="product-item-info">'
        '<a class="product-item-link" href="/x">NO PRICE BOOT</a></div>'
    ) + _tile("PRICED BOOT", "https://shop.example/p", "99.00")
    boots = parse_product_tiles(html, "https://shop.example/list", "football_boots")
    assert [b.title for b in boots] == ["PRICED BOOT"]


def test_should_read_a_tile_that_carries_its_name_in_a_title_attribute():
    # The carousel variant of the same markup puts the name in `title=` before `href=`.
    html = (
        '<div class="product-item-info">'
        '<a class="product-item-link" title="VAPOR 17 ELITE AG-PRO" '
        'href="https://shop.example/v17">x</a>'
        '<span data-price-amount="349.99" data-price-type="finalPrice"></span></div>'
    )
    [boot] = parse_product_tiles(html, "https://shop.example/list", "football_boots")
    assert boot.title == "VAPOR 17 ELITE AG-PRO"
    assert boot.price == 349.99


def test_should_report_tile_sizes_as_unknown():
    # A listing tile states no sizes. `sizes_known=False` keeps the judge honest: it caps
    # the find at "verify on click" rather than implying the size is available.
    [boot] = parse_product_tiles(
        _tile("SUPERFLY ELITE FG", "https://shop.example/e", "289.00"),
        "https://shop.example/list",
        "football_boots",
    )
    assert boot.sizes_known is False
    assert boot.sizes == frozenset()


def _swatch(in_stock: tuple[str, ...] = (), sold_out: tuple[str, ...] = ()) -> str:
    """A size swatch: a purchasable size is a link, an unavailable one is plain text."""
    links = "".join(f'<a data-size="{s}" href="/b#size={s}">{s}</a>' for s in in_stock)
    muted = "".join(f'<span class="mute">{s}</span>' for s in sold_out)
    return f'<div class="swatch-opt">{links}{muted}</div>'


def _wrapper_tile(name: str, href: str, price: str, rrp: str = "", swatch: str = "") -> str:
    """One tile in the theme that hangs its data off the element *wrapping* the tile div.

    voetbalshop.nl's shape, and the reason a marker-string split is unsafe: the `<li>`
    carries the catalogue data, `product-item-info` holds only the link and the rendered
    price, and the size swatch is a *sibling* of that div. The tile's own facts therefore
    sit both above and after the element such a split would cut on.
    """
    was = f'<p class="old-price">€ {rrp}</p>' if rrp else ""
    return (
        f'<li data-rrp="{rrp}" data-price="{price}" data-brand="adidas" data-sku="s-{price}">'
        f'<div class="product-item-info">'
        f'<a href="{href}" class="product-item-photo"><img alt="{name}"></a>'
        f'<div class="product-item-details">'
        f'<a class="product-item-link" title="{name}" href="{href}">{name}</a>'
        f'<div class="price-wrapper"><span class="price">€ {price}</span>{was}</div>'
        f"</div></div>{swatch}</li>"
    )


def test_should_read_a_tile_theme_that_keeps_its_price_on_the_wrapping_element():
    # voetbalshop.nl publishes no ld+json at all and uses none of the `data-price-amount`
    # markup teamsport.lv does, so the listing tiles are the only readable statement of
    # price — and the reader has to recognise this theme's conventions to see them.
    html = _wrapper_tile(
        "adidas F50 Hyperfast Elite Laceless FG", "/en/f50-hyperfast-elite.html", "223.99", "279.99"
    )
    [boot] = parse_product_tiles(html, "https://www.voetbalshop.nl/en/football-boots.html", "football_boots")
    assert boot.title == "adidas F50 Hyperfast Elite Laceless FG"
    assert boot.price == 223.99
    assert boot.reference_price == 279.99
    assert boot.url == "https://www.voetbalshop.nl/en/f50-hyperfast-elite.html"
    assert boot.source == "voetbalshop.nl"


def test_should_not_pair_a_wrapping_tiles_name_with_the_next_tiles_price():
    # The boundary test for the wrapper theme. Here the danger is sharper than for a flat
    # tile: this tile's swatch sits *after* the element a marker split would cut on, so
    # the neighbouring tile's price falls inside the same span of markup.
    html = _wrapper_tile(
        "CHEAP TAKEDOWN FG", "/cheap.html", "55.00", swatch=_swatch(("40",), ("41",))
    ) + _wrapper_tile(
        "SUPERFLY ELITE FG", "/elite.html", "289.00", swatch=_swatch(("42",), ("43",))
    )
    boots = parse_product_tiles(html, "https://shop.example/list", "football_boots")
    assert {b.title: b.price for b in boots} == {
        "CHEAP TAKEDOWN FG": 55.00,
        "SUPERFLY ELITE FG": 289.00,
    }


def test_should_read_in_stock_sizes_from_a_tile_swatch():
    # The swatch links only what can actually be bought, so this is the retailer's own
    # statement of stock — which promotes the whole source from "verify on click" to a
    # find that can be confirmed in the size before the human clicks.
    html = _wrapper_tile(
        "PREDATOR ELITE FG", "/p.html", "223.99", swatch=_swatch(("40", "41⅓"), ("36", "37⅓"))
    )
    [boot] = parse_product_tiles(html, "https://shop.example/list", "football_boots")
    assert boot.sizes_known is True
    assert boot.sizes == frozenset({"40", "41.33"})


def test_should_not_claim_sizes_when_every_swatch_option_is_purchasable():
    # Nothing distinguishes "in stock in all sizes" from "this theme renders no stock" —
    # so report unknown. Measured on the live listing: exactly the 14 of 48 tiles whose
    # own counter says every size is available, and no others.
    html = _wrapper_tile("F50 ELITE FG", "/f.html", "215.99", swatch=_swatch(("40", "41", "42")))
    [boot] = parse_product_tiles(html, "https://shop.example/list", "football_boots")
    assert boot.sizes_known is False
    assert boot.sizes == frozenset()


def test_should_report_no_stock_when_a_swatch_labels_its_sold_out_sizes():
    # A theme that tags both states lets "sold out in every size" be *known*, which is
    # real knowledge — quite different from the unknown above.
    swatch = '<div class="swatch-opt">' + "".join(
        f'<span data-size="{s}">{s}</span>' for s in ("40", "41")
    ) + "</div>"
    html = _wrapper_tile("SOLD OUT ELITE FG", "/s.html", "199.00", swatch=swatch)
    [boot] = parse_product_tiles(html, "https://shop.example/list", "football_boots")
    assert boot.sizes_known is True
    assert boot.sizes == frozenset()


def test_should_read_the_brand_a_tile_states_in_an_attribute():
    # Single-word product names like "Copa Mundial" never mention adidas. The tile does,
    # and without it `brands_only` would reject the boot as an unknown brand.
    html = _wrapper_tile("Copa Mundial", "/copa.html", "109.99", "140.00")
    [boot] = parse_product_tiles(html, "https://shop.example/list", "football_boots")
    assert boot.brand == "adidas"


def test_should_not_read_a_product_grid_wrapper_as_a_tile():
    # `product-items` is the wrapper around the tiles and is one character from
    # `product-item`. Matched as a substring it would swallow the whole grid into a
    # single product carrying the first name and the first price it happened to meet.
    html = (
        '<ol class="products list items product-items">'
        + _wrapper_tile("FIRST BOOT FG", "/a.html", "55.00")
        + _wrapper_tile("SECOND BOOT FG", "/b.html", "289.00")
        + "</ol>"
    )
    boots = parse_product_tiles(html, "https://shop.example/list", "football_boots")
    assert [b.title for b in boots] == ["FIRST BOOT FG", "SECOND BOOT FG"]


def _ldjson(node: dict) -> str:
    """A page publishing one schema.org node."""
    return f'<script type="application/ld+json">{json.dumps(node)}</script>'


def test_should_recover_a_brand_from_a_deep_product_url_when_the_page_states_none():    # futbolemotion.com names a boot "F50 Elite FG L-Tech Football Boots" and publishes no
    # `brand` on its listing, but files it under /adidas/. Without this the hunt's
    # `brands_only` gate rejects the entire shop for naming no brand it recognises.
    html = _ldjson(
        {
            "@type": "Product",
            "name": "F50 Elite FG L-Tech Football Boots",
            "url": "https://www.futbolemotion.com/en/buy/football-boot/adidas/f50-elite-fg-l-tech",
            "offers": {"@type": "Offer", "price": "279.99", "priceCurrency": "EUR"},
        }
    )
    [boot] = parse_ldjson_products(html, "https://www.futbolemotion.com/en/football-boots", "football_boots")
    assert boot.brand == "adidas"


def test_should_not_invent_a_brand_from_a_shop_root_path():
    # `/products/<handle>` is Shopify's root, not a brand directory. Reading "products" as
    # a brand would put a meaningless word in front of every Shopify product name.
    html = _ldjson(
        {
            "@type": "Product",
            "name": "Superfly Elite FG",
            "url": "https://komanda.lv/products/superfly-elite-fg",
            "offers": {"@type": "Offer", "price": "280.00", "priceCurrency": "EUR"},
        }
    )
    [boot] = parse_ldjson_products(html, "https://komanda.lv/collections/futbola-apavi", "football_boots")
    assert boot.brand == ""



def test_collect_page_should_fetch_once_and_return_products_and_links(monkeypatch):
    # The scout used to ask for products, get none, then ask for links — each call
    # fetching. Every listing that declared no products was therefore downloaded twice per
    # run: wasted time, and twice the load on a retailer we are a guest of.
    import dealscout.collector as collector

    calls: list[str] = []

    async def counting_fetch(url, timeout=20.0, retries=1):
        calls.append(url)
        return (
            '<div class="product-item-info">'
            '<a class="product-item-link" href="/p/a-elite">A Elite</a>'
            '<span data-price-amount="99.00" data-price-type="finalPrice"></span></div>'
        )

    monkeypatch.setattr(collector, "fetch", counting_fetch)
    monkeypatch.setattr(collector, "robots_allows", _always_allowed)

    products, links = asyncio.run(
        collector.collect_page("https://shop.example/list", "football_boots", delay=0)
    )

    assert len(calls) == 1
    assert [p.title for p in products] == ["A Elite"]
    assert links == []  # not parsed when products were found — the caller cannot need them


def test_collect_page_should_return_links_when_the_page_declares_no_products(monkeypatch):
    import dealscout.collector as collector

    calls: list[str] = []

    async def counting_fetch(url, timeout=20.0, retries=1):
        calls.append(url)
        return '<a href="/nike-phantom-elite-fg-42731"><img src="a.jpg"></a>'

    monkeypatch.setattr(collector, "fetch", counting_fetch)
    monkeypatch.setattr(collector, "robots_allows", _always_allowed)

    products, links = asyncio.run(
        collector.collect_page("https://shop.example/list", "football_boots", delay=0)
    )

    assert len(calls) == 1
    assert products == []
    assert [name for name, _ in links] == ["nike phantom elite fg"]


def test_collect_page_should_not_fetch_when_robots_forbids_it(monkeypatch):
    import dealscout.collector as collector

    async def refuse(url, agent="*"):
        return False

    async def explode(url, timeout=20.0, retries=1):  # pragma: no cover - must not run
        raise AssertionError("fetched a page robots.txt disallows")

    monkeypatch.setattr(collector, "robots_allows", refuse)
    monkeypatch.setattr(collector, "fetch", explode)

    assert asyncio.run(collector.collect_page("https://shop.example/x", "x", delay=0)) == ([], [])

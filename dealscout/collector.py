"""Collector — turns a watch item into a Product snapshot.

Prefers schema.org ld+json on the product page (name, price, currency, material).
Falls back to parsing a fabric-composition string ("80% cotton, 20% polyester")
from the description or page text. Scrape only your own watch-list pages, politely;
prefer affiliate feeds where available.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import replace
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import aiohttp
from bs4 import BeautifulSoup

from .models import Product, WatchItem
from .spec import is_eu_size, looks_like_eu, normalise_size
from .variants import extract_size_stock

logger = logging.getLogger(__name__)

_TAG_STRIP_RE = re.compile(r"<[^>]+>")

# A bare custom User-Agent with no other headers is silently *tarpitted* by the CDN bot
# protection several European retailers sit behind (Akamai especially): TCP connects,
# TLS completes, the request goes out, and no response ever arrives — so it looks like a
# network fault rather than a refusal. A complete, ordinary browser header set is what
# any HTTP client should send anyway. Politeness is enforced where it actually matters:
# one request per watch page, `scrape.delay_seconds` between them, and robots.txt honoured
# (see `robots_allows`).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Accept-Encoding is deliberately absent: aiohttp sets it to what it can actually decode.
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9,lv;q=0.8",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Matches "80% cotton", "100% virgin wool", "55% linen 45% cotton".
_COMPOSITION_RE = re.compile(
    r"(\d{1,3})\s*%\s+([a-zA-Z][a-zA-Z ]*?)(?=\s*\d{1,3}\s*%|[,;./]|$)"
)


def parse_materials(text: str) -> dict[str, float]:
    """Extract a fibre composition from text like '80% cotton, 20% polyester'."""
    materials: dict[str, float] = {}
    for pct, name in _COMPOSITION_RE.findall(text):
        fibre = name.strip().lower()
        frac = int(pct) / 100.0
        if fibre and 0 < frac <= 1:
            materials[fibre] = materials.get(fibre, 0.0) + frac
    return materials


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _is_product(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    node_type = node.get("@type")
    if isinstance(node_type, list):
        return "Product" in node_type or "ProductGroup" in node_type
    return node_type in ("Product", "ProductGroup")


def _is_group(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    node_type = node.get("@type")
    if isinstance(node_type, list):
        return "ProductGroup" in node_type
    return node_type == "ProductGroup"


def _variants(node: dict) -> list[dict]:
    raw = node.get("hasVariant")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [v for v in raw if isinstance(v, dict)]
    return []


def _variant_size(group_name: str, variant_name: str) -> str:
    """The size a ProductGroup variant is for ('… F50 Elite FG - 4.5' -> '4.5')."""
    text = str(variant_name or "").strip()
    base = str(group_name or "").strip()
    if base and text.lower().startswith(base.lower()):
        text = text[len(base) :]
    return normalise_size(text.lstrip(" -–—:,").strip())


def _flatten_offers(raw: Any) -> list[dict]:
    """Offers from one ``offers`` value (schema.org allows one, a list, or nested)."""
    if isinstance(raw, dict):
        nested = raw.get("offers")
        if isinstance(nested, list):
            return [o for o in nested if isinstance(o, dict)] or [raw]
        return [raw]
    if isinstance(raw, list):
        return [o for o in raw if isinstance(o, dict)]
    return []


def _offers(node: dict) -> list[dict]:
    """Every Offer on a node, including a ProductGroup's per-size variants.

    A variant *is* one size, so its size is copied onto its Offer where the size reader
    will find it.
    """
    collected = _flatten_offers(node.get("offers"))
    group_name = str(node.get("name") or "")
    for variant in _variants(node):
        size = _variant_size(group_name, variant.get("name", "")) or normalise_size(
            variant.get("size")
        )
        for offer in _flatten_offers(variant.get("offers")):
            entry = dict(offer)
            if size:
                entry["size"] = size
            collected.append(entry)
    return collected


def _in_stock(offer: dict) -> bool:
    """True when an Offer's availability says it can be bought now."""
    availability = offer.get("availability") or offer.get("itemCondition") or ""
    if isinstance(availability, dict):
        availability = availability.get("@id") or availability.get("name") or ""
    text = str(availability).lower()
    if not text:
        return True  # no availability stated: assume buyable rather than invent stock-outs
    return "instock" in text.replace("_", "") or "limitedavailability" in text.replace("_", "")


def _offer_sizes(offers: list[dict]) -> tuple[frozenset[str], bool]:
    """In-stock sizes from per-size Offers, plus whether sizes were stated at all.

    Retailers commonly emit one Offer per size, with the size in ``name``, ``sku`` or
    a ``size`` property. Returning ``known=False`` matters: "we don't know the sizes"
    must never be confused with "no sizes are in stock".
    """
    labels: list[str] = []
    in_stock: list[str] = []
    for offer in offers:
        raw = offer.get("size") or offer.get("name") or offer.get("sku") or ""
        size = normalise_size(raw)
        if not size:
            continue
        labels.append(size)
        if _in_stock(offer):
            in_stock.append(size)
    if not looks_like_eu(labels):
        # A UK size table, say: stated, but not in a system we can compare against a
        # hunt's EU sizes. Unknown beats a confident wrong rejection.
        return frozenset(), False
    return frozenset(in_stock), bool(labels)


def _brand(node: dict) -> str:
    brand = node.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    if isinstance(brand, list):
        brand = next((b.get("name") if isinstance(b, dict) else b for b in brand), "")
    return str(brand or "").strip()


# A brand directory: a path segment that is a single plain word, sitting under a taxonomy
# deep enough that it cannot be the shop's product root. `/products/<slug>` and
# `/de-de/p/<slug>` are roots and are rejected; futbolemotion's
# `/en/buy/football-boot/adidas/<slug>` is not.
_BRAND_SEGMENT_DEPTH = 3


def _brand_from_url(link: str) -> str:
    """The brand a product URL names in its own path ('' when it names none).

    Some multi-brand retailers omit the brand from both the product name and the listing's
    structured data — futbolemotion.com calls a boot "F50 Elite FG L-Tech Football Boots"
    and states no `brand` on its listing — while filing it under `/…/adidas/…`. Under
    `brands_only` a brandless product is not merely unranked but actively rejected, so the
    whole shop silently yields nothing; the retailer's own path is the fix.

    This can only ever turn a rejection into a match, never the reverse: the brand gate
    accepts a value only if it appears in the hunt's own brand list, so a segment that is
    not a brand matches nothing and leaves the outcome exactly as it was.
    """
    segments = [s for s in urlsplit(link).path.split("/") if s]
    above = segments[:-1]
    if len(above) < _BRAND_SEGMENT_DEPTH:
        return ""
    candidate = above[-1]
    return candidate if candidate.isalpha() and 2 <= len(candidate) <= 24 else ""


def _reference_price(node: dict, offers: list[dict], price: float) -> float | None:
    """Best available RRP: an explicit high price, a listPrice, or the dearest offer."""
    for offer in offers:
        for key in ("highPrice", "listPrice", "priceSpecification"):
            value = offer.get(key)
            if isinstance(value, dict):
                value = value.get("price")
            found = _to_float(value)
            if found and found > price:
                return found
    candidates = [p for p in (_to_float(o.get("price")) for o in offers) if p]
    top = max(candidates, default=None)
    return top if top and top > price else None


def _product_from_node(node: dict, url: str, category: str, page_text: str) -> Product | None:
    offers = _offers(node)
    first = offers[0] if offers else {}

    prices = [p for p in (_to_float(o.get("price") or o.get("lowPrice")) for o in offers) if p]
    price = min(prices) if prices else _to_float(first.get("price") or first.get("lowPrice"))
    if price is None:
        return None

    material = node.get("material")
    if isinstance(material, list):
        material = " ".join(str(m) for m in material)
    materials = parse_materials(str(material)) if material else {}
    if not materials:
        materials = parse_materials(str(node.get("description", "")) or page_text)

    sizes, sizes_known = _offer_sizes(offers)
    # Shopware stores (11teamsports.com) put the size on the Product node itself and emit
    # one whole Product block per size, rather than one Offer per size. Read that too, or
    # every such page reports "sizes unknown" and the owner has to click to find out.
    if not sizes_known:
        own = normalise_size(node.get("size"))
        if own and is_eu_size(own):
            sizes_known = True
            sizes = frozenset({own}) if any(_in_stock(o) for o in offers) else frozenset()
    link = str(node.get("url") or "").strip()
    if link.startswith("/"):
        link = urljoin(url, link)

    return Product(
        title=str(node.get("name", "")).strip() or url,
        category=category,
        price=price,
        reference_price=_reference_price(node, offers, price),
        currency=str(first.get("priceCurrency", "EUR")),
        url=link or url,
        materials=materials,
        brand=_brand(node) or _brand_from_url(link or url),
        source=urlsplit(url).netloc.removeprefix("www."),
        sizes=sizes,
        sizes_known=sizes_known,
    )


def _ldjson_nodes(html: str) -> tuple[list[Any], str]:
    """Every parsed ld+json blob on a page, plus the page's visible text."""
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    blobs: list[Any] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            blobs.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    return blobs, page_text


def _walk_products(data: Any) -> list[dict]:
    """Collect every Product node in a blob, including inside @graph and ItemList.

    A ProductGroup's ``hasVariant`` entries are sizes of one boot, not separate boots, so
    a node that is itself a product is never descended into for more products.
    """
    found: list[dict] = []
    if isinstance(data, list):
        for item in data:
            found.extend(_walk_products(item))
    elif isinstance(data, dict):
        keys = ("@graph", "itemListElement", "item", "mainEntity", "hasVariant")
        if _is_product(data):
            found.append(data)
            keys = ("@graph", "itemListElement", "item", "mainEntity")
        for key in keys:
            if key in data:
                found.extend(_walk_products(data[key]))
    return found


def parse_ldjson_product(html: str, url: str, category: str) -> Product | None:
    """Extract a single Product from schema.org ld+json in a page (pure, testable)."""
    products = parse_ldjson_products(html, url, category)
    return products[0] if products else None


# A product URL on most storefronts ends in a numeric product id. Good enough to tell a
# product link from navigation, and it is only ever used to *shortlist* pages to read.
_PRODUCT_HREF_RE = re.compile(r'href="([^"#?]*?-(\d{5,})[^"]*)"', re.IGNORECASE)
# Shopware storefronts (11teamsports.com) publish ids nowhere in the URL — a product is
# just `/de-de/p/<slug>`. Without this the id-based matcher above found only the handful
# of products whose colour code happened to contain five digits.
_PRODUCT_PATH_RE = re.compile(r'href="([^"#?]*/p/[a-z0-9][^"]*)"', re.IGNORECASE)
_TILE_BRAND_RE = re.compile(r'productdescriptionbrand"[^>]*>([^<]*)<', re.IGNORECASE)
_TILE_NAME_RE = re.compile(r'productdescriptionname"[^>]*>([^<]*)<', re.IGNORECASE)
# Trailing product id and file extension on an SEO slug: ".../nike-phantom-fg-42559".
# Stripped in two passes because the id sits *before* the extension, so a single
# end-anchored alternation only ever removes whichever one happens to be last.
# The id shape matches _PRODUCT_HREF_RE above, so a 2-3 digit model number like
# "Copa 20" or "F50" survives.
_SLUG_EXT_RE = re.compile(r"\.(?:html?|php|aspx)$", re.IGNORECASE)
_SLUG_ID_RE = re.compile(r"(?:-\d{5,})+$")


def title_from_slug(link: str) -> str:
    """A readable product name recovered from an SEO URL slug ('' when there isn't one).

    The tile regexes above are one retailer's markup. Plenty of shops — every OpenCart
    storefront, for instance — render a listing as bare image anchors with no text, so the
    only name on offer is the slug: ``/nike-tiempo-maestro-club-fg-mg-42559``. A nameless
    link cannot be pre-filtered, and under ``brands_only`` it is not merely unfiltered but
    actively *rejected*, so a whole retailer silently yields nothing. The slug is a good
    enough name for that pre-filter, which only ever reads brand and attribute words.
    """
    slug = urlsplit(link).path.rstrip("/").rsplit("/", 1)[-1]
    slug = _SLUG_ID_RE.sub("", _SLUG_EXT_RE.sub("", slug))
    words = [w for w in re.split(r"[-_]+", slug) if w]
    # A slug of pure digits is an id, not a name; one word is too thin to filter on.
    if len(words) < 2 or all(w.isdigit() for w in words):
        return ""
    return " ".join(words)


def parse_html_links(html: str, url: str) -> list[tuple[str, str]]:
    """Product-shaped anchors on a listing page that publishes no structured data.

    A last resort for retailers with neither Product nor ItemList ld+json. A tile usually
    links to the same product more than once — an image anchor and a text anchor — so
    every occurrence is examined and the first that yields a name wins. Taking only the
    first would return an empty name whenever the image anchor came first, and a nameless
    link cannot be pre-filtered, which is the whole point of reading it.
    """
    titles: dict[str, str] = {}
    order: list[str] = []
    here = urlsplit(url).path.rstrip("/")
    host = urlsplit(url).netloc
    matches = sorted(
        [*_PRODUCT_HREF_RE.finditer(html), *_PRODUCT_PATH_RE.finditer(html)],
        key=lambda m: m.start(),
    )
    for match in matches:
        absolute = urljoin(url, unescape(match.group(1)))
        parts = urlsplit(absolute)
        if parts.netloc != host:
            continue
        path = parts.path.rstrip("/")
        if not path or here.startswith(path):
            continue
        if absolute not in titles:
            titles[absolute] = ""
            order.append(absolute)
        if titles[absolute]:
            continue
        window = html[match.end() : match.end() + 3000]
        brand = _TILE_BRAND_RE.search(window)
        name = _TILE_NAME_RE.search(window)
        titles[absolute] = " ".join(
            unescape(part.group(1)).strip() for part in (brand, name) if part
        ).strip()
    # Fall back to the slug for anything the tile markup left nameless, so a retailer
    # whose listing is pure image anchors still offers something to pre-filter on.
    return [(titles[link] or title_from_slug(link), link) for link in order]


# Ordered best-first: an explicit machine-readable price beats a rendered one.
_PRICE_PATTERNS = (
    r'itemprop="price"[^>]*content="([\d.,]+)"',
    r'property="product:price:amount"[^>]*content="([\d.,]+)"',
    r'id="lblSellingPrice"[^>]*>\s*([^<]+?)<',
    r'class="[^"]*(?:curPrice|sellingPrice|price--current)[^"]*"[^>]*>\s*([^<]+?)<',
    # futbola-apavi.lv (OpenCart) renders `<div class="price"><span class="new">59,99€
    # </span><span class="old">69,99€</span></div>`. Matched on the exact class — a
    # wildcard on "new" would also catch `class="news"` — and listed last, so it only
    # applies when no machine-readable price was published at all.
    r'class="new"[^>]*>\s*([^<]+?)<',
)
_RRP_PATTERNS = (
    r'id="lblTicketPrice"[^>]*>\s*([^<]+?)<',
    r'class="[^"]*(?:ticketPrice|wasPrice|price--was|rrp)[^"]*"[^>]*>\s*([^<]+?)<',
    r'class="old"[^>]*>\s*([^<]+?)<',
)
_MONEY_RE = re.compile(r"(\d{1,6}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)")


def _money(text: object) -> float | None:
    """Read a rendered price ('167,39 €', '1 234.50') as a float."""
    match = _MONEY_RE.search(unescape(str(text or "")).replace("\xa0", " ").replace(" ", ""))
    if not match:
        return None
    raw = match.group(1)
    # Whichever separator comes last is the decimal one.
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".") if len(raw.split(",")[-1]) <= 2 else raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _first_match(html: str, patterns: tuple[str, ...]) -> float | None:
    for pattern in patterns:
        for found in re.findall(pattern, html, re.IGNORECASE):
            value = _money(found)
            if value:
                return value
    return None


def parse_html_product(html: str, url: str, category: str) -> Product | None:
    """Read a product page that publishes no ld+json Product at all.

    Sports Direct — the owner's own known-good retailer — is one of these: the page
    carries only a breadcrumb blob, and the price and per-size stock exist purely as
    rendered HTML. Returns None rather than a half-product when there is no price.
    """
    price = _first_match(html, _PRICE_PATTERNS)
    if price is None:
        return None

    title = ""
    og_title = re.search(r'property="og:title"[^>]*content="([^"]*)"', html, re.IGNORECASE)
    if og_title:
        title = unescape(og_title.group(1)).strip()
    if not title:
        page_title = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = unescape(_TAG_STRIP_RE.sub(" ", page_title.group(1))).strip() if page_title else ""

    reference = _first_match(html, _RRP_PATTERNS)
    stock = extract_size_stock(html)
    return Product(
        title=title or url,
        category=category,
        price=price,
        reference_price=reference if reference and reference > price else None,
        currency="EUR",
        url=url,
        source=urlsplit(url).netloc.removeprefix("www."),
        sizes=stock.sizes,
        sizes_known=stock.known,
    )


# A product tile as storefront themes actually render one. The name, the price and the
# sizes live in separate elements, so the tile *element* is what binds them: a listing
# carries more tiles than it has products — related-item carousels reuse the identical
# markup — and a reader that ignored the boundary would confidently pair a name with a
# neighbour's price.
#
# Boundaries come from the parsed tree rather than a marker-string split, because themes
# disagree about which element carries what. teamsport.lv hangs everything inside
# `div.product-item-info`; voetbalshop.nl hangs the price off the enclosing `<li>` and
# puts the size swatch *after* that div. Splitting on a marker leaves one tile's swatch
# and the next tile's price in the same chunk. An element's subtree cannot.
_TILE_CLASSES = frozenset(
    {"product-item-info", "product-item", "product-card", "product-tile", "product-box"}
)
# A theme that gives its tile no class at all still identifies it as a product by hanging
# the catalogue data off it: voetbalshop.nl renders `<li data-price="109.99"
# data-sku="407001.9003">`. Price alone is not enough — a filter widget has prices too.
_TILE_ID_ATTRS = ("data-sku", "data-pid", "data-product-id", "data-productid")
_TILE_PRICE_ATTRS = ("data-price", "data-final-price")
_TILE_RRP_ATTRS = ("data-rrp", "data-old-price", "data-regular-price")
_FINAL_PRICE_CLASSES = frozenset({"price", "special-price", "price--current", "current-price"})
_OLD_PRICE_CLASSES = frozenset(
    {"old-price", "old", "was-price", "price--was", "regular-price", "rrp", "price-old"}
)


def _class_tokens(tag: Any) -> set[str]:
    """The class tokens of a tag.

    Whole tokens only, never substrings: the wrapper around a list of ``product-item``
    tiles is ``product-items``, one character away from the tile itself, and matching it
    would make the entire grid a single product.
    """
    raw = tag.get("class")
    if isinstance(raw, list):
        return {str(token) for token in raw}
    return set(str(raw or "").split())


def _is_tile(tag: Any) -> bool:
    if _class_tokens(tag) & _TILE_CLASSES:
        return True
    return any(tag.has_attr(a) for a in _TILE_PRICE_ATTRS) and any(
        tag.has_attr(a) for a in _TILE_ID_ATTRS
    )


def _outermost_tiles(soup: BeautifulSoup) -> list[Any]:
    """Every tile element that is not itself inside another tile.

    One product commonly matches twice — `<li class="product-item">` wrapping
    `<div class="product-item-info">` — and it is the *outer* element that carries the
    theme's price attributes and its size swatch. Keeping only the outermost match stops
    the inner one becoming a second, poorer copy of the same product.
    """
    candidates = [tag for tag in soup.find_all(True) if _is_tile(tag)]
    inside = {id(tag) for tag in candidates}
    return [tag for tag in candidates if not any(id(p) in inside for p in tag.parents)]


def _tile_link(tile: Any) -> tuple[str, str]:
    """The product name and href a tile states ('', '' when it names no product)."""
    anchors = [
        a
        for a in tile.find_all("a", href=True)
        if str(a["href"]).strip() and not str(a["href"]).strip().startswith(("#", "javascript:"))
    ]
    if not anchors:
        return "", ""
    named = [a for a in anchors if "product-item-link" in _class_tokens(a)]
    anchor = named[0] if named else anchors[0]

    title = " ".join(anchor.get_text(" ", strip=True).split())
    # The carousel variant of this markup wraps an image in the anchor, so the link text
    # is empty or a stray character and the name exists only in the attribute.
    if len(title) < 3:
        title = " ".join(str(anchor.get("title") or "").split())
    if len(title) < 3:
        image = tile.find("img", alt=True)
        if image is not None:
            title = " ".join(str(image["alt"]).split())
    return title, str(anchor["href"])


def _attr_money(tag: Any, attrs: tuple[str, ...]) -> float | None:
    for attr in attrs:
        value = _money(tag.get(attr))
        if value:
            return value
    return None


def _typed_price(tile: Any, price_type: str) -> float | None:
    """A Magento ``data-price-type`` element's amount, wherever in the tile it sits."""
    tagged = tile.find(attrs={"data-price-type": price_type})
    return _money(tagged.get("data-price-amount")) if tagged is not None else None


def _tile_price(tile: Any) -> float | None:
    """What the tile says this product costs today, in whichever way the theme says it."""
    return (
        _attr_money(tile, _TILE_PRICE_ATTRS)
        or _typed_price(tile, "finalPrice")
        or _rendered_price(tile)
    )


def _rendered_price(tile: Any) -> float | None:
    for element in tile.find_all(True):
        if not _class_tokens(element) & _FINAL_PRICE_CLASSES:
            continue
        # `<span class="old-price"><span class="price">140</span></span>` states the RRP
        # inside an element that also calls itself a price; only the enclosing element
        # says which of the two it is.
        if any(_class_tokens(p) & _OLD_PRICE_CLASSES for p in element.parents if p.name):
            continue
        value = _money(element.get_text(" ", strip=True))
        if value:
            return value
    return None


def _tile_reference(tile: Any) -> float | None:
    """The tile's "was" price, if it prints one."""
    found = _attr_money(tile, _TILE_RRP_ATTRS) or _typed_price(tile, "oldPrice")
    if found:
        return found
    for element in tile.find_all(True):
        if _class_tokens(element) & _OLD_PRICE_CLASSES:
            value = _money(element.get_text(" ", strip=True))
            if value:
                return value
    return None


def _tile_sizes(tile: Any) -> tuple[frozenset[str], bool]:
    """In-stock sizes from a tile's size swatch, and whether stock was stated at all.

    A swatch renders every size the product is made in and links only the ones that can
    actually be bought, so "is this option a link" is the retailer's own statement of
    stock rather than an inference about it.

    When *every* option is a link the markup draws no distinction, and "in stock in all
    sizes" cannot be told apart from "this theme renders no stock at all" — so sizes stay
    unknown. A grid the boot is merely *offered* in, reported as availability, is the one
    kind of confident wrong answer that costs someone a boot that does not exist. A swatch
    that labels its sold-out options too is different: nothing purchasable is then real
    knowledge, and it comes back known-and-empty.
    """
    marked = tile.find(attrs={"data-size": True})
    if marked is None or marked.parent is None:
        return frozenset(), False

    offered: list[str] = []
    available: list[str] = []
    for option in marked.parent.find_all(True, recursive=False):
        size = normalise_size(option.get("data-size") or option.get_text(" ", strip=True))
        if not size:
            continue
        offered.append(size)
        if option.name == "a" and option.get("href"):
            available.append(size)
    if not looks_like_eu(offered) or len(available) >= len(offered):
        return frozenset(), False
    return frozenset(available), True


def parse_product_tiles(html: str, url: str, category: str) -> list[Product]:
    """Products from a listing rendered as tiles, for a shop with no structured data.

    Some storefronts publish no ld+json and load the *detail* page price over AJAX, so a
    product page states no price at all — but the listing still renders every tile
    server-side, with the name, the link, the price and sometimes the per-size stock
    already in the markup. That listing is then the only readable statement the shop
    offers, and it is a complete one.

    Themes vary in class names and in where they keep the price, so several conventions
    are tried per field. What does not vary is that each tile is read strictly within its
    own element: that boundary is the whole safety of this reader.
    """
    soup = BeautifulSoup(html, "html.parser")
    origin = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
    source = urlsplit(url).netloc.removeprefix("www.")
    products: list[Product] = []
    seen: set[str] = set()

    for tile in _outermost_tiles(soup):
        title, href = _tile_link(tile)
        price = _tile_price(tile)
        if not title or not href or price is None:
            continue

        link = urljoin(origin, unescape(href))
        if link in seen:
            continue
        seen.add(link)

        was = _tile_reference(tile)
        sizes, sizes_known = _tile_sizes(tile)
        products.append(
            Product(
                title=title,
                category=category,
                price=price,
                reference_price=was if was and was > price else None,
                currency="EUR",
                url=link,
                brand=str(tile.get("data-brand") or "").strip(),
                source=source,
                sizes=sizes,
                sizes_known=sizes_known,
            )
        )
    return products


def _merge_size_variant(existing: Product, extra: Product) -> Product:
    """Fold another ld+json node for the same product into the one already collected.

    Shopware emits one complete Product block *per size*, all sharing a name and a URL.
    Treating those as duplicates kept only the first block — and since the blocks arrive
    in no useful order, that first one is usually out of stock, so a boot that really was
    available in EU 37 came back with no sizes at all.
    """
    references = [r for r in (existing.reference_price, extra.reference_price) if r]
    return replace(
        existing,
        price=min(existing.price, extra.price),
        reference_price=max(references) if references else None,
        sizes=existing.sizes | extra.sizes,
        sizes_known=existing.sizes_known or extra.sizes_known,
    )


def parse_ldjson_products(html: str, url: str, category: str) -> list[Product]:
    """Extract EVERY Product from a page — works for a listing page as well as a PDP."""
    blobs, page_text = _ldjson_nodes(html)
    products: list[Product] = []
    at: dict[str, int] = {}
    for blob in blobs:
        for node in _walk_products(blob):
            product = _product_from_node(node, url, category, page_text)
            if product is None:
                continue
            key = f"{product.url}|{product.title}"
            if key in at:
                products[at[key]] = _merge_size_variant(products[at[key]], product)
                continue
            at[key] = len(products)
            products.append(product)
    return products


def _walk_listitems(node: Any, found: list[dict], in_breadcrumb: bool = False) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_listitems(item, found, in_breadcrumb)
    elif isinstance(node, dict):
        # A BreadcrumbList is also made of ListItems, but they point at categories, not
        # products. Following them costs a request per level and yields nothing.
        breadcrumb = in_breadcrumb or node.get("@type") == "BreadcrumbList"
        if node.get("@type") == "ListItem" and not breadcrumb:
            found.append(node)
        for value in node.values():
            _walk_listitems(value, found, breadcrumb)


def parse_ldjson_links(html: str, url: str) -> list[tuple[str, str]]:
    """Every ``(name, absolute URL)`` in a page's schema.org ItemList.

    Some retailers publish only a list of links on a listing page and keep price and
    stock on the product page. Returning the names as well as the URLs lets a caller
    discard the obvious non-candidates by title *before* spending a request on each one.
    """
    blobs, _ = _ldjson_nodes(html)
    items: list[dict] = []
    for blob in blobs:
        _walk_listitems(blob, items)

    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    here = urlsplit(url).path.rstrip("/")
    for item in items:
        target = item.get("url") or item.get("item")
        if isinstance(target, dict):
            target = target.get("url") or target.get("@id")
        if not isinstance(target, str) or not target.strip():
            continue
        absolute = urljoin(url, target.strip())
        if absolute in seen:
            continue
        # A link whose path is an ancestor of this page is a breadcrumb, not a product.
        # Some retailers publish breadcrumbs as a plain ItemList, and following them costs
        # one request per level to re-read pages we are already on.
        path = urlsplit(absolute).path.rstrip("/")
        if here.startswith(path) and urlsplit(absolute).netloc == urlsplit(url).netloc:
            continue
        seen.add(absolute)
        links.append((str(item.get("name") or "").strip(), absolute))
    return links


def parse_shopify_products(payload: str, url: str, category: str) -> list[Product]:
    """Products from a Shopify ``/collections/<name>/products.json`` payload.

    Shopify hands over exactly what a hunt needs — one variant per size, each with an
    ``available`` flag and a ``compare_at_price`` — for one request per collection and no
    scraping at all. Worth preferring wherever a retailer runs on it.
    """
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("products"), list):
        return []

    origin = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
    source = urlsplit(url).netloc.removeprefix("www.")
    products: list[Product] = []
    for node in data["products"]:
        if not isinstance(node, dict):
            continue
        variants = [v for v in (node.get("variants") or []) if isinstance(v, dict)]
        prices = [p for p in (_to_float(v.get("price")) for v in variants) if p]
        if not prices:
            continue

        vendor = str(node.get("vendor") or "").strip()
        name = str(node.get("title") or "").strip()
        # Some shops set `vendor` to their own name rather than the brand. Prefixing that
        # onto every title pollutes brand matching, so only a real brand is prefixed.
        if not vendor or vendor.lower() in name.lower() or vendor.lower() in source.lower():
            title = name
        else:
            title = f"{vendor} {name}".strip()

        sizes: set[str] = set()
        labelled: list[str] = []
        for variant in variants:
            size = normalise_size(variant.get("title") or variant.get("option1"))
            if not size:
                continue
            labelled.append(size)
            if variant.get("available"):
                sizes.add(size)

        was = [p for p in (_to_float(v.get("compare_at_price")) for v in variants) if p]
        reference = max(was, default=None)
        known = bool(labelled) and looks_like_eu(labelled)
        products.append(
            Product(
                title=title,
                category=category,
                price=min(prices),
                reference_price=reference if reference and reference > min(prices) else None,
                currency="EUR",
                url=f"{origin}/products/{node.get('handle', '')}",
                brand=vendor,
                source=source,
                sizes=frozenset(sizes) if known else frozenset(),
                sizes_known=known,
            )
        )
    return products


async def fetch(url: str, timeout: float = 20.0, retries: int = 1) -> str | None:
    """GET a page's HTML, or None on error/non-200.

    Does NOT consult robots.txt — that is the caller's job (see :func:`robots_allows`),
    so that fetching robots.txt itself cannot recurse.
    """
    for attempt in range(retries + 1):
        try:
            async with aiohttp.ClientSession(headers=BROWSER_HEADERS) as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status != 200:
                        logger.warning("fetch %s -> HTTP %s", url, resp.status)
                        return None
                    return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt >= retries:
                logger.warning("fetch %s failed: %s", url, exc or type(exc).__name__)
                return None
            # A tarpitting CDN drops the first connection far more often than the second.
            logger.info("fetch %s failed (%s) — retrying", url, type(exc).__name__)
            await asyncio.sleep(2.0 * (attempt + 1))
    return None


_ROBOTS: dict[str, RobotFileParser | None] = {}


async def robots_allows(url: str, agent: str = "*") -> bool:
    """True when the host's robots.txt permits fetching ``url``.

    Cached per host, and **fail-open**: an unreadable robots.txt means "no stated rule",
    not "forbidden" — but it is logged, so a silent assumption never passes unnoticed.
    """
    parts = urlsplit(url)
    if not parts.netloc:
        return True
    root = f"{parts.scheme}://{parts.netloc}"
    if root not in _ROBOTS:
        text = await fetch(f"{root}/robots.txt", timeout=10.0, retries=0)
        if text is None:
            logger.info("robots.txt unreadable for %s — proceeding", root)
            _ROBOTS[root] = None
        else:
            parser = RobotFileParser()
            parser.parse(text.splitlines())
            _ROBOTS[root] = parser
    parser = _ROBOTS[root]
    if parser is None:
        return True
    if not parser.can_fetch(agent, url):
        logger.warning("robots.txt disallows %s — skipping", url)
        return False
    return True


async def collect(item: WatchItem, title_hint: str = "") -> Product | None:
    """Fetch a watch item and parse it into a Product (None if unreadable/disallowed).

    ``title_hint`` is the name the listing gave this link. Some retailers put the brand
    in the listing tile but leave it out of the product page's own title — Sports Direct
    renders "Ultra 5 Ultimate ... Juniors" on a page reached from a tile reading "Puma
    Ultra 5 Ultimate ... Juniors". The hint is used only when it strictly contains the
    page's own title, i.e. when it is the same name with more of it.
    """
    if not await robots_allows(item.url):
        return None
    html = await fetch(item.url)
    if html is None:
        return None
    product = parse_ldjson_product(html, item.url, item.category)
    if product is None:
        product = parse_html_product(html, item.url, item.category)
    if product is None:
        logger.info("no product found at %s", item.url)
        return None

    hint = title_hint.strip()
    if hint and product.title.strip() and hint != product.title.strip():
        if product.title.strip().lower() in hint.lower():
            product = replace(product, title=hint)
    return product


def read_listing(html: str, url: str, category: str) -> list[Product]:
    """Every Product a listing page declares (pure).

    A Shopify collection endpoint hands over structured data directly; no parsing of
    rendered markup, and one request covers a whole collection. Rendered tiles are the
    last resort, for a storefront that publishes no structured data anywhere.
    """
    return (
        parse_shopify_products(html, url, category)
        or parse_ldjson_products(html, url, category)
        or parse_product_tiles(html, url, category)
    )


def read_links(html: str, url: str) -> list[tuple[str, str]]:
    """Every ``(name, URL)`` a listing page links to (pure)."""
    return parse_ldjson_links(html, url) or parse_html_links(html, url)


async def collect_page(
    url: str, category: str, delay: float = 1.0
) -> tuple[list[Product], list[tuple[str, str]]]:
    """Fetch a listing page **once** and read it both ways.

    Callers that fall back from products to links previously called ``collect_listing``
    and then ``collect_links``, each of which fetches — so every page that declared no
    products was downloaded twice, once per run, per watch URL. That is wasted time and,
    more importantly, twice the load on a retailer we are a guest of.

    Links are only parsed when there are no products, since that is the only case a
    caller needs them for and the parse is not free on a megabyte of markup.
    """
    if not await robots_allows(url):
        return [], []
    html = await fetch(url)
    if html is None:
        return [], []

    products = read_listing(html, url, category)
    links: list[tuple[str, str]] = [] if products else read_links(html, url)
    if products:
        logger.info("collected %d product(s) from %s", len(products), url)
    elif links:
        logger.info("collected %d link(s) from %s", len(links), url)
    else:
        logger.info("no product found at %s", url)
    if delay > 0:
        await asyncio.sleep(delay)
    return products, links


async def collect_listing(url: str, category: str, delay: float = 1.0) -> list[Product]:
    """Fetch one listing (or product) page and return every Product it declares.

    Skips the page when robots.txt disallows it, and sleeps ``delay`` seconds after the
    request. Scrape only your own watch-list pages, politely — see AGENTS.md.
    """
    if not await robots_allows(url):
        return []
    html = await fetch(url)
    if html is None:
        return []
    products = read_listing(html, url, category)
    if not products:
        logger.info("no product found at %s", url)
    else:
        logger.info("collected %d product(s) from %s", len(products), url)
    if delay > 0:
        await asyncio.sleep(delay)
    return products


async def collect_links(url: str, delay: float = 1.0) -> list[tuple[str, str]]:
    """Fetch a listing page and return the ``(name, URL)`` pairs it links to.

    Prefers a schema.org ItemList; falls back to product-shaped anchors for retailers
    that publish no structured data on a listing at all.
    """
    if not await robots_allows(url):
        return []
    html = await fetch(url)
    if delay > 0:
        await asyncio.sleep(delay)
    if html is None:
        return []
    links = parse_ldjson_links(html, url) or parse_html_links(html, url)
    logger.info("collected %d link(s) from %s", len(links), url)
    return links


async def enrich(product: Product, delay: float = 1.0) -> Product:
    """Re-read a product's own page for the per-size stock and RRP a listing omits.

    A listing page almost never says which sizes are left, and that is the single fact
    that decides the purchase. This only ever *adds* knowledge: anything the page does
    not state is left exactly as it was, so enrichment can never turn a known value into
    a guess.
    """
    if not product.url or not await robots_allows(product.url):
        return product
    html = await fetch(product.url)
    if delay > 0:
        await asyncio.sleep(delay)
    if html is None:
        return product

    stock = extract_size_stock(html)
    updates: dict[str, Any] = {}
    if stock.known and not product.sizes_known:
        updates["sizes"] = stock.sizes
        updates["sizes_known"] = True
    if stock.reference_price and not product.reference_price:
        updates["reference_price"] = stock.reference_price
    if not updates:
        logger.info("no per-size stock found at %s", product.url)
        return product
    logger.info(
        "enriched %s: %d size(s) in stock, RRP %s",
        product.url,
        len(updates.get("sizes", product.sizes)),
        updates.get("reference_price", product.reference_price),
    )
    return replace(product, **updates)


async def enrich_all(products: list[Product], delay: float = 1.0) -> list[Product]:
    """Enrich products one at a time — deliberately serial, to stay a polite visitor."""
    return [await enrich(product, delay) for product in products]

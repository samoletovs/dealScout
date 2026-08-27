"""Product-tile parsing — reading a listing rendered as tiles, with no structured data.

Some storefronts publish no ld+json and load the *detail* page price over AJAX, so a
product page states no price at all — but the listing still renders every tile
server-side, with the name, the link, the price and sometimes the per-size stock already
in the markup. That listing is then the only readable statement the shop offers, and it
is a complete one.

The reader is deliberately theme-agnostic and reads each tile strictly inside its own
element, because a listing carries more tiles than products (related-item carousels reuse
the identical markup) and a marker-string split would pair a name with a neighbour's
price. A wrong price is worse than no price, so boundaries come from the parsed tree.
"""

from __future__ import annotations

from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import Product
from ..spec import looks_like_eu, normalise_size
from .htmlproduct import _money

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

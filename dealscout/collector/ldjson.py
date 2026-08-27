"""schema.org ld+json parsing — the richest source, and where most retailer knowledge lives.

A product page's ``<script type="application/ld+json">`` block usually states the name,
price, currency, material and — on the shops that emit one Offer per size — per-size
stock. This module turns that JSON into ``Product`` objects and is entirely pure: give it
a string of HTML and it yields products, no network involved.

The fabric-composition reader (``parse_materials``) lives here too: it is the fallback
when a page states no ``material``, reading "80% cotton, 20% polyester" out of the
description or visible text.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import Product
from ..spec import is_eu_size, looks_like_eu, normalise_size

logger = logging.getLogger(__name__)

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

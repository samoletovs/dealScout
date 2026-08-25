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
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp
from bs4 import BeautifulSoup

from .models import Product, WatchItem
from .spec import normalise_size

logger = logging.getLogger(__name__)

USER_AGENT = "dealScout/0.1 (+https://dealscout.naurolabs.com)"

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
        return "Product" in node_type
    return node_type == "Product"


def _offers(node: dict) -> list[dict]:
    """Every Offer on a node, flattened (schema.org allows one, a list, or nested)."""
    raw = node.get("offers")
    if isinstance(raw, dict):
        nested = raw.get("offers")
        if isinstance(nested, list):
            return [o for o in nested if isinstance(o, dict)] or [raw]
        return [raw]
    if isinstance(raw, list):
        return [o for o in raw if isinstance(o, dict)]
    return []


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
    return frozenset(in_stock), bool(labels)


def _brand(node: dict) -> str:
    brand = node.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    if isinstance(brand, list):
        brand = next((b.get("name") if isinstance(b, dict) else b for b in brand), "")
    return str(brand or "").strip()


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
        brand=_brand(node),
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
    """Collect every Product node in a blob, including inside @graph and ItemList."""
    found: list[dict] = []
    if isinstance(data, list):
        for item in data:
            found.extend(_walk_products(item))
    elif isinstance(data, dict):
        if _is_product(data):
            found.append(data)
        for key in ("@graph", "itemListElement", "item", "mainEntity", "hasVariant"):
            if key in data:
                found.extend(_walk_products(data[key]))
    return found


def parse_ldjson_product(html: str, url: str, category: str) -> Product | None:
    """Extract a single Product from schema.org ld+json in a page (pure, testable)."""
    products = parse_ldjson_products(html, url, category)
    return products[0] if products else None


def parse_ldjson_products(html: str, url: str, category: str) -> list[Product]:
    """Extract EVERY Product from a page — works for a listing page as well as a PDP."""
    blobs, page_text = _ldjson_nodes(html)
    products: list[Product] = []
    seen: set[str] = set()
    for blob in blobs:
        for node in _walk_products(blob):
            product = _product_from_node(node, url, category, page_text)
            if product is None:
                continue
            key = f"{product.url}|{product.title}"
            if key in seen:
                continue
            seen.add(key)
            products.append(product)
    return products


async def fetch(url: str, timeout: float = 15.0) -> str | None:
    """GET a page's HTML, or None on error/non-200."""
    headers = {"User-Agent": USER_AGENT}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    logger.warning("fetch %s -> HTTP %s", url, resp.status)
                    return None
                return await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("fetch %s failed: %s", url, exc)
        return None


async def collect(item: WatchItem) -> Product | None:
    """Fetch a watch item and parse it into a Product (None if unreadable)."""
    html = await fetch(item.url)
    if html is None:
        return None
    product = parse_ldjson_product(html, item.url, item.category)
    if product is None:
        logger.info("no ld+json Product found at %s", item.url)
    return product


async def collect_listing(url: str, category: str, delay: float = 1.0) -> list[Product]:
    """Fetch one listing (or product) page and return every Product it declares.

    Sleeps ``delay`` seconds after the request. Scrape only your own watch-list pages,
    politely — see the guardrails in AGENTS.md.
    """
    html = await fetch(url)
    if html is None:
        return []
    products = parse_ldjson_products(html, url, category)
    if not products:
        logger.info("no ld+json Product found at %s", url)
    else:
        logger.info("collected %d product(s) from %s", len(products), url)
    if delay > 0:
        await asyncio.sleep(delay)
    return products

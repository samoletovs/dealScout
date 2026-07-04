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

import aiohttp
from bs4 import BeautifulSoup

from .models import Product, WatchItem

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


def _find_product_node(data: Any) -> dict | None:
    if isinstance(data, dict):
        if _is_product(data):
            return data
        graph = data.get("@graph")
        if isinstance(graph, list):
            return next((n for n in graph if _is_product(n)), None)
    elif isinstance(data, list):
        return next((n for n in data if _is_product(n)), None)
    return None


def _product_from_node(node: dict, url: str, category: str, page_text: str) -> Product | None:
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        offers = {}

    price = _to_float(offers.get("price") or offers.get("lowPrice"))
    if price is None:
        return None

    material = node.get("material")
    if isinstance(material, list):
        material = " ".join(str(m) for m in material)
    materials = parse_materials(str(material)) if material else {}
    if not materials:
        materials = parse_materials(str(node.get("description", "")) or page_text)

    return Product(
        title=str(node.get("name", "")).strip() or url,
        category=category,
        price=price,
        reference_price=_to_float(offers.get("highPrice")),
        currency=str(offers.get("priceCurrency", "EUR")),
        url=url,
        materials=materials,
    )


def parse_ldjson_product(html: str, url: str, category: str) -> Product | None:
    """Extract a Product from schema.org ld+json in a page (pure, testable)."""
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        node = _find_product_node(data)
        if node is not None:
            return _product_from_node(node, url, category, page_text)
    return None


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

"""Shopify ``/collections/<name>/products.json`` parsing.

Shopify hands over exactly what a hunt needs — one variant per size, each with an
``available`` flag and a ``compare_at_price`` — for one request per collection and no
scraping at all. It is JSON but not schema.org, so it is its own concern: worth preferring
wherever a retailer runs on it (prodirectsport.ie, komanda.lv).
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from ..models import Product
from ..spec import looks_like_eu, normalise_size
from .ldjson import _to_float


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

"""Shopify ``/collections/<name>/products.json`` parsing.

Shopify hands over exactly what a hunt needs — one variant per size, each with an
``available`` flag and a ``compare_at_price`` — for one request per collection and no
scraping at all. It is JSON but not schema.org, so it is its own concern: worth preferring
wherever a retailer runs on it (prodirectsport.ie, komanda.lv).

Image licence — retrieval is not the right to republish
--------------------------------------------------------
This parser now also keeps each product's image URLs (``Product.images``) and the instant
they were seen (``Product.image_seen_at``). Capturing a URL from a public endpoint does not
license us to display that image on a public/commercial surface. Two questions gate any such
display and BOTH are currently **UNVERIFIED** — they can only be answered from inside an
approved affiliate account, so whoever reaches that point must answer exactly these before
any retailer photo goes public:

  1. Does the *retailer's own* affiliate programme terms (e.g. Pro:Direct on Awin — read its
     Terms/Branding tab after acceptance) actually permit displaying feed imagery on a
     price-comparison / shopping-portal site? Awin's *general* publisher guidance permitting
     feed images is NOT the same as the advertiser's programme terms.
  2. Does that permission survive the image being of *another brand's* product — specifically,
     does Nike's wholesale / authorised-dealer contract forbid the retailer from syndicating
     Nike-product imagery to affiliates, regardless of who owns the copyright in the photo?
     Secondary sources say such a restriction is a standard wholesale term; the operative
     clause is unreadable from outside. If it binds, the retailer's own copyright licence to
     us is overridden upstream and Nike stays illustrated.

Until both are confirmed in writing, treat everything captured here as fit for an internal
design spike only. See the project imagery report for the full analysis and citations.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ..models import Product
from ..spec import looks_like_eu, normalise_size
from .ldjson import _to_float


def _now_utc_iso() -> str:
    """The current instant as ISO-8601 UTC (e.g. '2026-08-28T14:03:11Z').

    Stamped onto every image URL captured from a feed, because merchant CDNs rotate image
    URLs (Shopify appends a ``?v=`` cache-buster) and a stored link is only trustworthy
    relative to when it was last seen served.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _image_urls(node: dict) -> tuple[str, ...]:
    """Every product image URL a Shopify product node carries, in the feed's own order.

    Shopify's ``/products.json`` always includes an ``images`` array (each entry a dict with
    a ``src``); some payloads also repeat the primary as ``featured_image``. We read the URLs
    only — retrieval is not a licence to republish, so what we keep is fit for an internal
    design spike and for public display only once an affiliate (or other) licence covers the
    image. The primary is placed first so a caller wanting one representative shot can take
    ``images[0]``.
    """
    urls: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        src = value.get("src") if isinstance(value, dict) else value
        if isinstance(src, str) and src.strip() and src not in seen:
            seen.add(src)
            urls.append(src.strip())

    add(node.get("featured_image"))
    for image in node.get("images") or []:
        add(image)
    return tuple(urls)


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
        images = _image_urls(node)
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
                images=images,
                image_seen_at=_now_utc_iso() if images else "",
            )
        )
    return products

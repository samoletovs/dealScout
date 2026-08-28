"""Collector package — turns a watch item into a Product snapshot.

Prefers schema.org ld+json on the product page (name, price, currency, material). Falls
back to parsing a fabric-composition string ("80% cotton, 20% polyester") from the
description or page text, or to rendered tiles/anchors on shops that publish no structured
data at all. Scrape only your own watch-list pages, politely; prefer affiliate feeds where
available.

This was one 900-line module and is now a package split along the seams that actually
exist in it. Two of those seams matter most:

* **Pure vs impure.** The parsers (:mod:`ldjson`, :mod:`links`, :mod:`htmlproduct`,
  :mod:`tiles`, :mod:`shopify`) are pure string-to-``Product`` and need no network stub to
  test — that is where every retailer's hard-won intelligence lives. The I/O
  (:mod:`http`) and the orchestration that binds parsers to it (:mod:`collect`) are impure
  and carry the politeness and error handling.
* **One reader per shop shape.** ld+json, listing anchors, a rendered product page,
  rendered tiles, and Shopify JSON are five distinct things a shop can be, and each reader
  encodes what one family of shops does.

The whole public surface is re-exported here, so every existing ``from
dealscout.collector import …`` keeps working unchanged.
"""

from __future__ import annotations

from .collect import (
    collect,
    collect_links,
    collect_listing,
    collect_page,
    enrich,
    enrich_all,
    read_links,
    read_listing,
)
from .htmlproduct import _money, parse_html_product
from .http import (
    _ROBOTS,
    BROWSER_HEADERS,
    HONEST_HEADERS,
    HONEST_USER_AGENT,
    SELF_IDENTIFYING_HOSTS,
    USER_AGENT,
    fetch,
    headers_for,
    robots_allows,
)
from .ldjson import (
    parse_ldjson_links,
    parse_ldjson_product,
    parse_ldjson_products,
    parse_materials,
)
from .links import parse_html_links, title_from_slug
from .shopify import parse_shopify_products
from .tiles import parse_product_tiles

__all__ = [
    "BROWSER_HEADERS",
    "HONEST_HEADERS",
    "HONEST_USER_AGENT",
    "SELF_IDENTIFYING_HOSTS",
    "headers_for",
    "USER_AGENT",
    "_ROBOTS",
    "_money",
    "collect",
    "collect_links",
    "collect_listing",
    "collect_page",
    "enrich",
    "enrich_all",
    "fetch",
    "parse_html_links",
    "parse_html_product",
    "parse_ldjson_links",
    "parse_ldjson_product",
    "parse_ldjson_products",
    "parse_materials",
    "parse_product_tiles",
    "parse_shopify_products",
    "read_links",
    "read_listing",
    "robots_allows",
    "title_from_slug",
]

"""Read a Magento storefront through its own GraphQL API, guided by its sitemap.

Some storefronts render nothing on the server. sportland.lv — the largest sports chain in
the Baltics, and the only source here with shops the boots can be tried on in — serves a
webpack shell: no ld+json, no price, no tiles, nothing a parser can read. The obvious
answer is a headless browser, which is slow, fragile, and a heavy dependency to run on a
schedule.

It is also unnecessary. The shell is a ScandiPWA front-end, and ScandiPWA is a React
storefront *for Magento 2*, which means the data it renders arrives over Magento's own
GraphQL endpoint — public by design, unlike the REST API that guards the same catalogue
behind a token. So the site can be read exactly the way its own front-end reads it.

The remaining problem is discovery: GraphQL here accepts only three filters
(``category_id``, ``category_uid``, ``url_key``) and no free-text search, so there is no
"list me the football boots" query. The sitemap solves it — it is static XML, served to
anyone, and it names every product. Filtering those URLs by slug costs nothing, and the
survivors can be fetched in batches of fifty by ``url_key``.

The result is one sitemap request plus a handful of GraphQL calls for a whole catalogue.

Sizes are read honestly. A configurable product publishes the sizes it is *offered* in and
a stock flag per variant, but this storefront returns no attributes on the variants, so
which size a given in-stock variant refers to cannot be established. Guessing by position
would be a confident wrong answer about whether a boot exists in EU 37, so sizes stay
unknown and the judge caps the find at "verify on click".
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote, urlsplit

from .models import Product

logger = logging.getLogger(__name__)

# Kept well under any URL-length limit, since the query travels as a GET query string.
DEFAULT_BATCH = 50

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)

PRODUCTS_QUERY = """{products(filter:{url_key:{in:[%s]}},pageSize:%d){items{
name url_key stock_status
price_range{minimum_price{final_price{value currency}regular_price{value}}}
... on ConfigurableProduct{configurable_options{attribute_code values{label}}}}}}"""


def sitemap_product_keys(xml: str, marker: str = "/product/") -> list[str]:
    """The ``url_key`` of every product URL in a sitemap (pure).

    A sitemap is the only complete list of products a JavaScript storefront offers to a
    non-browser, and it costs one request. Note that a shop's *default* ``/sitemap.xml``
    may belong to a different storefront of the same group — always take the URL named in
    robots.txt.
    """
    keys: list[str] = []
    seen: set[str] = set()
    for url in _LOC_RE.findall(xml or ""):
        if marker not in url:
            continue
        key = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def build_products_query(url_keys: list[str]) -> str:
    """A Magento ``products`` query for a batch of url_keys (pure)."""
    quoted = ",".join(json.dumps(key) for key in url_keys)
    return PRODUCTS_QUERY % (quoted, max(len(url_keys), 1))


def query_url(endpoint: str, url_keys: list[str]) -> str:
    """The full GET URL for a batch query (pure, so it can be asserted in a test)."""
    return f"{endpoint}?query={quote(build_products_query(url_keys))}"


def _offered_sizes(node: dict) -> frozenset[str]:
    """Size labels the product is offered in — *not* a claim about what is in stock."""
    labels: set[str] = set()
    for option in node.get("configurable_options") or []:
        if "size" not in str(option.get("attribute_code", "")).lower():
            continue
        for value in option.get("values") or []:
            label = str(value.get("label") or "").strip()
            if label:
                labels.add(label)
    return frozenset(labels)


def parse_graphql_products(payload: str, origin: str, category: str) -> list[Product]:
    """Products from a Magento GraphQL ``products`` response (pure).

    ``sizes_known`` is deliberately left False. The response says which sizes exist and
    how many variants are in stock, but not which variant is which size — so claiming a
    size is available would be a guess, and the one kind of mistake a co-pilot must not
    make is a confident wrong answer about whether a boot fits.
    """
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return []
    items = (((data or {}).get("data") or {}).get("products") or {}).get("items") or []

    source = urlsplit(origin).netloc.removeprefix("www.")
    products: list[Product] = []
    for node in items:
        if not isinstance(node, dict):
            continue
        if str(node.get("stock_status") or "").upper() == "OUT_OF_STOCK":
            continue
        prices = ((node.get("price_range") or {}).get("minimum_price")) or {}
        try:
            price = float((prices.get("final_price") or {}).get("value"))
        except (TypeError, ValueError):
            continue
        try:
            regular = float((prices.get("regular_price") or {}).get("value"))
        except (TypeError, ValueError):
            regular = 0.0

        key = str(node.get("url_key") or "").strip()
        products.append(
            Product(
                title=str(node.get("name") or "").strip() or key,
                category=category,
                price=price,
                reference_price=regular if regular > price else None,
                currency=str((prices.get("final_price") or {}).get("currency") or "EUR"),
                url=f"{origin.rstrip('/')}/product/{key}",
                source=source,
                sizes=frozenset(),
                sizes_known=False,
            )
        )
    return products


def batched(items: list[str], size: int = DEFAULT_BATCH):
    """Yield fixed-size slices, so one catalogue costs a handful of requests, not one each."""
    size = max(1, size)
    for start in range(0, len(items), size):
        yield items[start : start + size]

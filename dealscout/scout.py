"""Scout — gather candidate products for a Hunt from every configured source.

A hunt declares *what* it wants; the scout decides *where* to look. Two sources ship
today and both are optional, so a hunt works with either or both:

* ``watch:`` — listing or product pages parsed via schema.org ld+json. Best for a
  retailer you already buy from (their "sort by discount" page is a free deal feed).
* ``queries:`` — links from the weekly Google Shopping discovery cache. Prices and
  stock are read from retailers; this entrypoint never makes paid search requests.

Adding a source (an affiliate feed, a retailer API, an LLM-driven agent) means adding
a function here — the judge and the monitor do not change.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
import re
from urllib.parse import urlsplit

from .collector import collect, collect_page, fetch, robots_allows, title_from_slug
from .discovery import Discovery, DiscoveryError, enabled
from .magento import (
    DEFAULT_BATCH,
    batched,
    parse_graphql_products,
    query_url,
    sitemap_product_keys,
)
from .hunt import attrs_from_title, brand_is_known
from .models import Hunt, Product, WatchItem
from .serpsearch import _allowed_source, _condition, _old_price

logger = logging.getLogger(__name__)

DEFAULT_LINK_BUDGET = 12  # product pages one listing may cost us


def _match_brand(title: str, brands: tuple[str, ...]) -> str:
    """First hunt brand named in the title ('' when none is)."""
    low = title.lower()
    return next((b for b in brands if str(b).strip().lower() in low), "")


def title_plausible(title: str, hunt: Hunt, vocab: dict | None = None) -> bool:
    """Cheap pre-filter for a link we have nothing but a name for.

    Only rejects a title that positively *contradicts* a requirement. A title that says
    nothing stays a candidate — unknown is not the same as absent, and this filter exists
    to save requests, not to make judgements.

    Resolves attributes the same way the judge does, via ``attrs_from_title``. It must:
    this runs *before* any request is spent, so a pre-filter reading tier by an older rule
    than the judge discards whole retailers with no exception, no empty parse and nothing
    in the log to say why.

    ``brands_only`` therefore asks the catalogue, not just the string. A single-brand shop
    omits its own name: teamsport.lv, Nike's Latvian distributor and a configured catalogue
    here, lists "ZM SUPERFLY 10 ELITE SG-PRO". A string test discards that — the exact
    failure the paragraph above warns about, and it had cost the shop's entire storefront.
    The catalogue reads it as a Nike flagship, so the brand is known even though the word
    is absent.

    It is honoured only when the catalogue *also* named a tier. That keeps the gate doing
    its job: "Skechers Razor Astro Turf Trainers" yields no brand and no tier and stays
    rejected, and so does a lookalike titled after a silo it does not belong to, because
    naming a real boot's tier is the part a knock-off does not do.
    """
    attrs = attrs_from_title(title, hunt.category, vocab)
    for attr, allowed in hunt.require.items():
        value = attrs.get(attr)
        if value and not any(str(a).strip().lower() == value.strip().lower() for a in allowed):
            return False
    if hunt.brands_only and hunt.brands and not brand_is_known(attrs, hunt, title):
        return False
    return not any(m.strip().lower() in title.lower() for m in hunt.exclude_models if m.strip())


def title_confirms(title: str, hunt: Hunt, vocab: dict | None = None) -> bool:
    """True when a title positively satisfies every requirement, guessing at nothing.

    Used only to *order* a limited request budget: "adidas Kids F50 Elite FG" states the
    tier and the soleplate, so it is worth a page before "adidas Kids Copa 19.4 FG",
    which merely fails to contradict anything.
    """
    if not hunt.require:
        return False
    attrs = attrs_from_title(title, hunt.category, vocab)
    return all(
        (attrs.get(attr) or "").strip().lower()
        in {str(a).strip().lower() for a in allowed}
        for attr, allowed in hunt.require.items()
    )


def shopping_products(
    results: list[dict], hunt: Hunt
) -> list[Product]:
    """Map SerpApi ``google_shopping`` results into candidate Products (pure).

    Shopping never states which sizes are in stock, so ``sizes_known`` stays False and
    the judge flags size as unverified rather than guessing.
    """
    products: list[Product] = []
    for item in results:
        try:
            price = float(item["extracted_price"])
        except (KeyError, TypeError, ValueError):
            continue
        title = str(item.get("title", ""))
        products.append(
            Product(
                title=title,
                category=hunt.category,
                price=price,
                reference_price=_old_price(item),
                currency=hunt.currency,
                url=item.get("link") or item.get("product_link") or "",
                brand=_match_brand(title, hunt.brands),
                source=str(item.get("source") or "").strip(),
                condition=_condition(item, title),
            )
        )
    return products


async def _from_queries(hunt: Hunt, config: dict, api_key: str | None) -> list[Product]:
    sconf = config.get("serpapi") or {}
    if not hunt.queries:
        return []
    if not enabled(config):
        logger.info("hunt %s: SerpApi discovery disabled; direct sources remain active", hunt.id)
        return []

    limit = int(sconf.get("max_results") or 20)
    block = list(sconf.get("exclude_sources") or []) + list(hunt.exclude_sources)
    allow = list(sconf.get("preferred_stores") or [])

    discovery = Discovery(config)
    leads: dict[str, Product] = {}
    try:
        for query in hunt.queries:
            for product in shopping_products(discovery.cached(query)[:limit], hunt):
                if (
                    product.url and _allowed_source(product.source, allow, block)
                    and (not hunt.require_new or product.condition == "new")
                ):
                    leads.setdefault(product.url, product)
    except DiscoveryError as exc:
        logger.error("hunt %s: discovery unavailable: %s; direct sources continue", hunt.id, exc)
        return []

    # Cached Shopping prices are discovery hints, never today's price or stock evidence.
    scrape = config.get("scrape") or {}
    budget = int(scrape.get("link_budget", DEFAULT_LINK_BUDGET))
    delay = float(scrape.get("delay_seconds", 1.0))
    found: list[Product] = []
    for lead in list(leads.values())[:budget]:
        product = await collect(WatchItem(url=lead.url, category=hunt.category), title_hint=lead.title)
        # The allowlist admitted the Shopping store label above; live parsers use
        # hostnames (e.g. "About You" -> "aboutyou.de"). Still recheck the blocklist.
        if product is not None and _allowed_source(product.source, [], block):
            if lead.condition != "new":
                product = replace(product, condition=lead.condition)
            found.append(product)
        await asyncio.sleep(delay)
    logger.info("hunt %s: %d live product(s) from %d cached discovery link(s)", hunt.id, len(found), len(leads))
    return found


async def _from_watch(
    hunt: Hunt, delay: float, vocab: dict | None = None, budget: int = DEFAULT_LINK_BUDGET
) -> list[Product]:
    found: list[Product] = []
    for url in hunt.watch:
        # One fetch, read both ways. Asking for products and then falling back to links
        # used to download the same page twice for every listing that declared none.
        products, links = await collect_page(url, hunt.category, delay=delay)
        if products:
            found.extend(products)
            continue

        # The page published links rather than products (a schema.org ItemList). Discard
        # the titles that already contradict the hunt, then read the survivors' own pages
        # — a listing of 50 usually leaves a handful worth a request.
        if not links:
            continue
        plausible = [(n, u) for n, u in links if title_plausible(n, hunt, vocab)]
        # Spend the request budget on titles that already state what we need, before
        # those that merely fail to contradict it — otherwise a listing sorted
        # cheapest-first burns the whole budget on takedown models.
        plausible.sort(key=lambda pair: not title_confirms(pair[0], hunt, vocab))
        plausible = plausible[:budget]
        logger.info(
            "hunt %s: %s listed %d link(s), %d plausible", hunt.id, url, len(links), len(plausible)
        )
        for name, link in plausible:
            product = await collect(WatchItem(url=link, category=hunt.category), title_hint=name)
            if product is not None:
                found.append(product)
            await asyncio.sleep(delay)

    if hunt.watch:
        logger.info("hunt %s: %d candidate(s) from %d page(s)", hunt.id, len(found), len(hunt.watch))
    return found


async def _from_catalogs(
    hunt: Hunt, delay: float, vocab: dict | None = None, batch: int = DEFAULT_BATCH
) -> list[Product]:
    """Read Magento storefronts that render nothing, via sitemap + their own GraphQL.

    The sitemap is the only complete product list such a shop offers to a non-browser, and
    the slug is enough to discard the overwhelming majority before spending a request:
    sportland.lv publishes 18,616 products, of which a boots hunt wants under a hundred.
    """
    found: list[Product] = []
    for catalog in hunt.catalogs:
        sitemap = str(catalog.get("sitemap") or "").strip()
        endpoint = str(catalog.get("graphql") or "").strip()
        if not sitemap or not endpoint:
            continue
        origin = str(catalog.get("origin") or "").strip() or (
            f"{urlsplit(endpoint).scheme}://{urlsplit(endpoint).netloc}"
        )
        if not await robots_allows(sitemap):
            logger.info("hunt %s: robots.txt disallows %s", hunt.id, sitemap)
            continue

        xml = await fetch(sitemap)
        if not xml:
            continue
        keys = sitemap_product_keys(xml, str(catalog.get("marker") or "/product/"))

        # The slug is the only thing known before a request, so filter on it. `match`
        # narrows to the right department; the hunt's own rules then reject anything whose
        # name already contradicts them, exactly as for a listing of nameless links.
        needle = str(catalog.get("match") or "").strip().lower()
        if needle:
            wanted = tuple(part for part in re.split(r"[|,]", needle) if part)
            keys = [k for k in keys if any(part in k.lower() for part in wanted)]
        keys = [k for k in keys if title_plausible(title_from_slug(k), hunt, vocab)]
        logger.info("hunt %s: %s -> %d candidate key(s)", hunt.id, sitemap, len(keys))

        for chunk in batched(keys, batch):
            payload = await fetch(query_url(endpoint, chunk))
            if payload:
                found.extend(parse_graphql_products(payload, origin, hunt.category))
            await asyncio.sleep(delay)

    if hunt.catalogs:
        logger.info(
            "hunt %s: %d candidate(s) from %d catalog(s)", hunt.id, len(found), len(hunt.catalogs)
        )
    return found


async def scout(
    hunt: Hunt, config: dict, api_key: str | None = None, vocab: dict | None = None
) -> list[Product]:
    """Gather every candidate product for a hunt, de-duplicated by URL."""
    scrape = config.get("scrape") or {}
    delay = float(scrape.get("delay_seconds", 1.0))
    budget = int(scrape.get("link_budget", DEFAULT_LINK_BUDGET))
    batch = int(scrape.get("graphql_batch", DEFAULT_BATCH))

    watched, searched, catalogued = await asyncio.gather(
        _from_watch(hunt, delay, vocab, budget),
        _from_queries(hunt, config, api_key),
        _from_catalogs(hunt, delay, vocab, batch),
    )

    seen: set[str] = set()
    unique: list[Product] = []
    for product in [*watched, *searched, *catalogued]:
        if not product.url or product.url in seen:
            continue
        seen.add(product.url)
        unique.append(product)
    logger.info("hunt %s: %d unique candidate(s)", hunt.id, len(unique))
    return unique

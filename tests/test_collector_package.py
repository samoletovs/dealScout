"""Characterisation tests for the collector *package* split.

The collector was one 900-line module and is now a package. These tests pin the two
invariants a split like this can quietly break — neither of which the per-reader unit
tests would catch, because they import the names they need directly:

1. **The public surface stays importable from ``dealscout.collector``.** Nine other call
   sites (``scout``, ``qualify``, ``run*``, the test suite) do ``from dealscout.collector
   import …``. A split that moved a name without re-exporting it would pass every reader
   test and still break the app on import.

2. **The network seam still resolves through the package namespace.** The whole suite
   stubs the network with ``monkeypatch.setattr("dealscout.collector.fetch", …)``. When
   ``fetch`` lived as a global of the single module, the orchestrators and
   ``robots_allows`` resolved it there, so the patch reached them. Split naively — ``from
   .http import fetch`` bound by value into ``collect.py`` — that patch would replace only
   the package attribute and the orchestrators would hit the *live network* while the test
   believed it was offline. That is the exact failure this refactor had to avoid, so it is
   the exact thing worth a test that fails without the indirection.
"""

from __future__ import annotations

import asyncio

import dealscout.collector as collector

# The names every other module and the test suite import from the package. Kept here as a
# ledger: if a future edit drops one from the re-export, this list turns the silent
# import-time break into a named test failure.
PUBLIC_SURFACE = (
    "BROWSER_HEADERS",
    "USER_AGENT",
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
)


def test_every_public_name_is_importable_from_the_package():
    missing = [name for name in PUBLIC_SURFACE if not hasattr(collector, name)]
    assert missing == [], f"no longer exported from dealscout.collector: {missing}"


def test_patching_package_fetch_reaches_the_orchestrators(monkeypatch):
    # An orchestrator (enrich) must call whatever ``dealscout.collector.fetch`` currently
    # is, not a copy bound at import time. If it did not, this stub would be ignored and
    # the call would go to the network.
    served: list[str] = []

    async def stub_fetch(url, timeout=20.0, retries=1):
        served.append(url)
        return "<html>no size table here</html>"

    async def allow(url, agent="*"):
        return True

    monkeypatch.setattr(collector, "fetch", stub_fetch)
    monkeypatch.setattr(collector, "robots_allows", allow)

    from dealscout.models import Product

    product = Product(
        title="Boot", category="football_boots", price=90.0, reference_price=None,
        currency="EUR", url="https://shop.example/boot",
    )
    result = asyncio.run(collector.enrich(product, delay=0))
    # The page stated nothing, so enrich returns the product untouched — but it must have
    # gone through the patched fetch to learn that.
    assert served == ["https://shop.example/boot"]
    assert result == product


def test_patching_package_fetch_reaches_robots_allows(monkeypatch):
    # robots_allows fetches robots.txt itself. That request has to resolve the *patched*
    # fetch too, or a "no network" test silently hits the live site for robots.txt.
    served: list[str] = []

    async def stub_fetch(url, timeout=20.0, retries=1):
        served.append(url)
        return "User-agent: *\nDisallow: /private/"

    monkeypatch.setattr(collector, "fetch", stub_fetch)
    collector._ROBOTS.clear()

    assert asyncio.run(collector.robots_allows("https://shop.example/private/x")) is False
    assert asyncio.run(collector.robots_allows("https://shop.example/public/x")) is True
    # Exactly one robots.txt fetch (the second call reads the cached parser), and it went
    # through the stub rather than the network.
    assert served == ["https://shop.example/robots.txt"]
    collector._ROBOTS.clear()

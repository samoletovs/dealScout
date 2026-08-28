"""TEMPORARY probe — what do this repo's parsers recover from sportsdirect.lv?

Delete along with .github/workflows/probe-sportsdirect.yml once SOURCES.md records the
answer. Kept as a file rather than a heredoc because the escaping in YAML-inside-bash
silently mangles regexes.
"""

from __future__ import annotations

import asyncio
import pathlib
import re

from dealscout import collector

URL = "https://www.sportsdirect.lv/football/football-boots/kids-football-boots"
ID_MARKER = re.compile(r'"id":"')
BLOCK = re.compile(r"var ecommerceData\s*=\s*(\{.*?\});", re.S)


async def main() -> None:
    html = await collector.fetch(URL, timeout=40)
    if not html:
        print("FETCH FAILED")
        return
    print(f"fetched {len(html)} bytes")
    pathlib.Path("page.html").write_text(html, encoding="utf-8")

    print("\n--- parse_html_links ---")
    links = collector.parse_html_links(html, URL)
    print(f"  {len(links)} links")
    for name, url in links[:8]:
        print(f"   * {name!r} -> {url[:90]}")

    print("\n--- parse_product_tiles ---")
    tiles = collector.parse_product_tiles(html, URL)
    print(f"  {len(tiles)} tiles")
    for product in tiles[:8]:
        print(f"   * {product.name!r} {product.price} {product.brand!r}")

    print("\n--- parse_ldjson_products ---")
    print(f"  {len(collector.parse_ldjson_products(html, URL))} ld+json products")

    print("\n--- raw ecommerceData (what IS in the page) ---")
    match = BLOCK.search(html)
    print(f"  block present: {bool(match)}")
    if match:
        print(f"  impressions: {len(ID_MARKER.findall(match.group(1)))}")


asyncio.run(main())

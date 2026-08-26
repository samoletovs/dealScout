"""Qualify a retailer as a dealScout source, on evidence rather than reputation.

Adding a shop to a hunt is cheap to do and expensive to get wrong: a source that cannot
be read silently contributes nothing, and one that *looks* readable but states sizes it
cannot actually sell is worse, because it sends the owner to buy a boot that isn't there.

So a candidate is asked three questions, each answered by fetching the shop:

  1. REACHABLE — does a politely-headed GET succeed? Bot protection makes a shop
     unmonitorable however good its stock is.
  2. ELITE     — does it stock the flagship tier at all? Proven with a named product,
     never assumed from the shop's reputation.
  3. READABLE  — can per-size stock be read, and how well?

Run it before editing a hunt's ``watch:`` list::

    python -m dealscout.qualify www.example.com
    python -m dealscout.qualify www.example.com /collections/boots-sale

The verdict is printed for a human to act on; nothing is written.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field

from .collector import (
    collect_links,
    fetch,
    parse_html_product,
    parse_ldjson_products,
    parse_shopify_products,
    robots_allows,
)
from .models import Product
from .variants import extract_size_stock

logger = logging.getLogger(__name__)

# The flagship tier, by the words retailers actually print. Puma calls it Ultimate and
# Mizuno "Made in Japan"; adidas and Nike both say Elite.
TOP_TIER = ("elite", "ultimate", "made in japan")

# An adult flagship lists above this. Used to qualify the *shop* — proof that the top line
# is carried — and not as a filter on any boot a hunt might buy, since the junior edition
# of the same boot lists at €120-130.
ELITE_RRP_FLOOR = 200.0

# Where a shop keeps its catalogue, tried in order when no path is given.
COMMON_PATHS = (
    "/products.json?limit=250",
    "/collections/all/products.json?limit=250",
)

BOT_FINGERPRINTS = (
    ("challenge-platform", "cloudflare"),
    ("datadome", "datadome"),
    ("_incapsula_", "imperva"),
    ("px-captcha", "perimeterx"),
)


@dataclass
class Verdict:
    """What a candidate source can and cannot do."""

    host: str
    reachable: bool = False
    guard: str = ""
    robots_ok: bool | None = None
    reader: str = "none"
    stock_quality: str = "none"  # exact | stated | price-only | none
    elite: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def qualified(self) -> bool:
        """Usable as a source: reachable, permitted, priced, and carrying the top tier."""
        return (
            self.reachable
            and self.robots_ok is not False
            and bool(self.elite)
            and self.stock_quality != "none"
        )

    @property
    def tier(self) -> str:
        if not self.reachable:
            return "BLOCKED"
        if not self.qualified:
            return "TIER 3 — not monitorable as-is"
        return "TIER 1 — exact stock" if self.stock_quality == "exact" else "TIER 2 — partial"


def is_top_tier(title: str) -> bool:
    """True when a product title names the flagship tier.

    ``SG-Pro`` and ``AG-Pro`` are soleplates, not tiers, so they are left alone here — a
    "Phantom Elite SG-Pro" is a genuine Elite boot. Rejecting the *Pro tier* is the hunt's
    job, which reads the tier properly rather than by substring.
    """
    return any(word in title.lower() for word in TOP_TIER)


def elite_evidence(products: list[Product], limit: int = 3) -> list[str]:
    """Named proof that a shop stocks the flagship tier.

    Takes an RRP above the floor as proof, and falls back to absolute price. The fallback
    is not a nicety: an official dealer such as komanda.lv sells at RRP and therefore
    publishes no "was" price at all, so a discount-only test rejects exactly the
    authorised retailers worth trusting most.
    """
    found: list[str] = []
    for product in products:
        if not is_top_tier(product.title):
            continue
        rrp = product.reference_price
        if rrp and rrp > ELITE_RRP_FLOOR:
            found.append(f"{product.title[:48]} — €{product.price:.0f} (RRP €{rrp:.0f})")
        elif not rrp and product.price > ELITE_RRP_FLOOR:
            found.append(f"{product.title[:48]} — €{product.price:.0f} (at RRP)")
        if len(found) >= limit:
            break
    return found


def rate_stock(products: list[Product]) -> str:
    """How well per-size stock is known across a batch of products."""
    if not products:
        return "none"
    if any(p.sizes_known for p in products):
        return "exact"
    return "price-only"


def detect_guard(html: str) -> str:
    low = html.lower()
    hits = {name for needle, name in BOT_FINGERPRINTS if needle in low}
    return ",".join(sorted(hits))


async def _shopify(host: str, verdict: Verdict) -> bool:
    """Read a Shopify catalogue endpoint. The best case: exact stock, one request."""
    for path in COMMON_PATHS:
        url = f"https://{host}{path}"
        payload = await fetch(url)
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or "products" not in data:
            continue
        products = parse_shopify_products(payload, url, "football_boots")
        if not products:
            continue
        verdict.reader = f"shopify {path}"
        verdict.stock_quality = rate_stock(products)
        verdict.elite = elite_evidence(products)
        verdict.robots_ok = await robots_allows(url)
        verdict.notes.append(f"{len(products)} product(s) in one request")
        return True
    return False


async def _listing(host: str, path: str, verdict: Verdict) -> bool:
    """Read a listing page: ld+json products first, then its product pages."""
    url = f"https://{host}{path}"
    html = await fetch(url)
    if not html:
        return False
    verdict.robots_ok = await robots_allows(url)

    products = parse_ldjson_products(html, url, "football_boots")
    if products:
        verdict.reader = f"ld+json listing {path}"
        verdict.stock_quality = rate_stock(products)
        verdict.elite = elite_evidence(products)
        verdict.notes.append(f"{len(products)} product(s) on the listing")
        if verdict.elite:
            return True

    links = await collect_links(url, delay=1.0)
    if not links:
        return False
    top = [(n, u) for n, u in links if is_top_tier(n)]
    verdict.notes.append(f"{len(links)} link(s), {len(top)} naming the top tier")
    if not top:
        return False

    read: list[Product] = []
    sized = 0
    for name, link in top[:2]:
        page = await fetch(link)
        await asyncio.sleep(1.0)
        if not page:
            continue
        found = parse_ldjson_products(page, link, "football_boots")
        # Fall back to rendered markup: a shop with no structured data at all is exactly
        # the kind this check exists to classify, and it is still monitorable.
        product = found[0] if found else parse_html_product(page, link, "football_boots")
        if product is None:
            continue
        stock = extract_size_stock(page)
        if stock.known:
            sized += 1
        read.append(product)

    if not read:
        verdict.reader = f"links only {path}"
        verdict.stock_quality = "none"
        verdict.notes.append("product pages published no price")
        return False

    verdict.reader = f"product pages via {path}"
    verdict.stock_quality = "exact" if sized else "price-only"
    verdict.elite = elite_evidence(read) or verdict.elite
    return True


async def qualify(host: str, paths: list[str] | None = None) -> Verdict:
    """Fetch a candidate source and report what it can do."""
    host = host.removeprefix("https://").removeprefix("http://").strip("/")
    verdict = Verdict(host=host)

    home = await fetch(f"https://{host}/")
    if not home:
        verdict.notes.append("no response to a plain GET — bot protection or geo-block")
        return verdict
    verdict.reachable = True
    verdict.guard = detect_guard(home)

    if await _shopify(host, verdict):
        return verdict

    for path in paths or ["/"]:
        if await _listing(host, path, verdict):
            return verdict
    return verdict


def render(verdict: Verdict) -> str:
    lines = [
        f"{verdict.host}  —  {verdict.tier}",
        f"  reachable   : {verdict.reachable}" + (f"  (guard: {verdict.guard})" if verdict.guard else ""),
        f"  robots.txt  : {verdict.robots_ok}",
        f"  reader      : {verdict.reader}",
        f"  per-size    : {verdict.stock_quality}",
    ]
    if verdict.elite:
        lines.append("  elite proof :")
        lines.extend(f"      {e}" for e in verdict.elite)
    else:
        lines.append("  elite proof : NONE FOUND — not a source for this hunt")
    lines.extend(f"  note        : {n}" for n in verdict.notes)
    return "\n".join(lines)


async def _main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    verdict = await qualify(argv[0], argv[1:] or None)
    print(render(verdict))
    return 0 if verdict.qualified else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))

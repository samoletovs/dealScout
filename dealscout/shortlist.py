"""Rank a hunt's candidates into a shortlist a human can act on in thirty seconds.

The hunt report answers "what changed?". This answers a different question — "what should
I buy?" — and it needs three things the change report does not:

**Landed cost, not shelf price.** A €45 boot from Ireland at €7 delivery costs more than a
€50 boot collected in Rīga. Sorting on the shelf price quietly recommends the wrong shop,
so everything here sorts on what actually leaves the account.

**Source diversity.** One retailer holds most of the discounted stock, so a plain
cheapest-first list is ten rows from the same shop — which reads like a shortlist but
offers no real choice, and no fallback if that shop's stock is stale. Filling the list
round-robin rather than cheapest-first gives every shop its cheapest row before any shop
gets its second, so breadth survives a retailer with a deep sale.

**A stated count per source.** Diversity nobody can see is indistinguishable from none,
and a source that contributed *nothing* is the most useful row of all: a retailer goes
silent when its parser breaks far more often than when its shelves empty, and a list that
merely lacks the row cannot tell the reader which happened.

**An honest split on size.** A boot confirmed in EU 37 and a boot that merely might be are
not comparable, and averaging them into one list hides which is which. They get separate
lists, and the unconfirmed one carries the sizes the shop *does* publish so the reader can
judge for themselves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from .models import Hunt, Product
from .spec import normalise_sizes

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 10
DEFAULT_PER_SOURCE = 3


@dataclass(frozen=True)
class Delivery:
    """What is known about a source: what it costs to receive from, and what it sells."""

    label: str = ""
    shipping: float = 0.0
    free_over: float | None = None
    pickup: bool = False  # a physical shop the boots can be tried on in
    note: str = ""
    # A single-brand retailer often omits the brand from its own product names —
    # teamsport.lv is Nike's Latvian distributor and lists "ZM SUPERFLY 10 ELITE SG-PRO",
    # with no "Nike" anywhere. Under `brands_only` that reads as an unknown brand and the
    # whole shop is rejected. Declaring the house brand here restores what the shop takes
    # as obvious. Only ever set it for a genuinely single-brand storefront.
    house_brand: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> Delivery:
        free = data.get("free_over")
        return cls(
            label=str(data.get("label") or "").strip(),
            shipping=float(data.get("shipping") or 0),
            free_over=float(free) if free is not None else None,
            pickup=bool(data.get("pickup", False)),
            note=str(data.get("note") or "").strip(),
            house_brand=str(data.get("house_brand") or "").strip(),
        )


@dataclass(frozen=True)
class SourceCoverage:
    """One source's contribution to a shortlist: how many rows, from what price, of how many.

    ``found`` is what makes the table answer the question the reader actually asks. Six
    rows from one shop looks like a ranking bug until you can see that shop offered fifteen
    candidates and the next two offered two each — at which point it is the catalogue
    talking, not the ranker.

    ``count == 0`` is a real row, not an omission: it is how a configured retailer says it
    went quiet this run.

    ``scouted`` is what separates the two ways of contributing nothing, and the difference
    decides whether the reader should worry. A shop whose pages were read fine but whose
    stock simply does not fit has ``scouted > 0``; a shop whose reader broke has
    ``scouted == 0``. Only the second deserves an alarm — a warning that fires every week
    for a shop that is working teaches the reader to ignore it, and then it is worth
    nothing on the week a parser really does die.
    """

    source: str
    label: str
    count: int
    cheapest: float | None = None
    found: int = 0
    scouted: int = 0


def stamp_house_brands(products: list[Product], table: dict[str, Delivery]) -> list[Product]:
    """Give products from a single-brand shop the brand its own listing leaves implicit.

    Applied to ``Product.brand`` rather than the title, so the judge's brand gate sees it
    while the displayed name stays exactly what the retailer printed.
    """
    stamped: list[Product] = []
    for product in products:
        brand = delivery_for(product.source, table).house_brand
        if brand and not product.brand and brand.lower() not in product.title.lower():
            product = replace(product, brand=brand)
        stamped.append(product)
    return stamped


def delivery_for(source: str, table: dict[str, Delivery]) -> Delivery:
    """The delivery terms for a source ('' when the source is unknown to config)."""
    key = (source or "").lower().removeprefix("www.")
    return table.get(key, Delivery())


def landed_cost(product: Product, delivery: Delivery) -> float:
    """Price plus what it costs to actually receive it.

    An unknown source contributes no shipping rather than a guessed one — a made-up
    postage figure would silently reorder the list.
    """
    if delivery.free_over is not None and product.price >= delivery.free_over:
        return product.price
    return product.price + max(0.0, delivery.shipping)


def matched_sizes(product: Product, hunt: Hunt) -> list[str]:
    """The sizes this product is in stock in that the hunt wants *for its brand*."""
    if not product.sizes_known:
        return []
    wanted = hunt.sizes_for(product.brand, product.title)
    return sorted(normalise_sizes(wanted) & normalise_sizes(product.sizes), key=_as_float)


def _as_float(size: str) -> float:
    try:
        return float(size)
    except (TypeError, ValueError):
        return 0.0


def pick_diverse(
    products: list[Product],
    table: dict[str, Delivery],
    limit: int = DEFAULT_LIMIT,
    per_source: int = DEFAULT_PER_SOURCE,
) -> list[Product]:
    """Cheapest by landed cost, filled round-robin so no one shop takes the list.

    Each pass takes one product from every source — its cheapest unused one — so a shop
    with a single bargain is on the list before the deepest sale gets its second row.
    Sources are visited in the order of their cheapest row, which keeps each pass itself
    price-ordered.

    The ``per_source`` cap is relaxed rather than enforced: if honouring it would return
    fewer than ``limit`` rows, the passes continue past it. A short list is worse than a
    repetitive one — the reader wants ten options, and being handed six because the rule
    could not be satisfied helps nobody. The relaxed passes stay round-robin, so the extra
    rows still come off the top of each shop in turn instead of all off the deepest one.
    """
    ranked = sorted(products, key=lambda p: (landed_cost(p, delivery_for(p.source, table)), p.price))
    by_source: dict[str, list[Product]] = {}
    for product in ranked:
        by_source.setdefault(product.source, []).append(product)

    cap = max(1, per_source)
    chosen = _round_robin(by_source, limit, cap)
    if len(chosen) < limit:
        logger.debug(
            "relaxing the %d-per-source cap: %d of %d row(s) from %d source(s)",
            cap,
            len(chosen),
            limit,
            len(by_source),
        )
        chosen = _round_robin(by_source, limit, depth_cap=None)
    # Re-sort: the passes append by depth, not by price, so without this the list can show
    # a €108 boot above a €62 one, which makes a "cheapest first" list actively misleading.
    return sorted(chosen, key=lambda p: landed_cost(p, delivery_for(p.source, table)))


def _round_robin(
    by_source: dict[str, list[Product]], limit: int, depth_cap: int | None
) -> list[Product]:
    """One product from each source in turn, up to ``limit`` and ``depth_cap`` per source.

    Each source's list must already be cheapest-first; ``depth_cap`` of ``None`` runs until
    every source is exhausted.
    """
    chosen: list[Product] = []
    depth = 0
    while len(chosen) < limit and (depth_cap is None or depth < depth_cap):
        exhausted = True
        for queue in by_source.values():
            if len(chosen) >= limit:
                break
            if depth < len(queue):
                chosen.append(queue[depth])
                exhausted = False
        if exhausted:
            break
        depth += 1
    return chosen


def expected_sources(hunt: Hunt, table: dict[str, Delivery]) -> list[str]:
    """The sources this hunt polls *and* config states delivery terms for, in config order.

    Derived from the hunt rather than hard-coded, so a running-shoe hunt reports
    running-shoe shops. Intersected with the delivery table on purpose: a watch list keeps
    URLs for shops that have since been blocked or written off, and reporting those as
    having gone quiet on every single run would train the reader to ignore the one line
    that matters. A shop the owner has written delivery terms for is one they consider live.
    """
    urls = list(hunt.watch)
    for catalog in hunt.catalogs:
        urls.append(
            str(catalog.get("origin") or catalog.get("graphql") or catalog.get("sitemap") or "")
        )

    hosts: list[str] = []
    for url in urls:
        host = urlsplit(url).netloc.lower().removeprefix("www.")
        if host and host in table and host not in hosts:
            hosts.append(host)
    return hosts


def source_coverage(
    picked: list[Product],
    table: dict[str, Delivery],
    expected: list[str] | tuple[str, ...] = (),
    pool: list[Product] | tuple[Product, ...] = (),
    scouted: list[Product] | tuple[Product, ...] = (),
) -> list[SourceCoverage]:
    """How much of the shortlist each source contributed, biggest contributor first.

    ``pool`` is everything that survived judging, so the report can distinguish a shop that
    was beaten on price from one that only ever had two boots to offer. ``scouted`` is
    everything read from the shop *before* judging, which is what tells a broken reader
    apart from a shop that simply stocks nothing suitable.

    A configured source that contributed nothing is reported with ``count == 0`` rather
    than left out. A list that merely lacks the row cannot say which of the two happened.
    """
    counts: dict[str, int] = {}
    cheapest: dict[str, float] = {}
    for product in picked:
        source = product.source
        total = landed_cost(product, delivery_for(source, table))
        counts[source] = counts.get(source, 0) + 1
        cheapest[source] = min(cheapest.get(source, total), total)

    found: dict[str, int] = {}
    for product in pool:
        found[product.source] = found.get(product.source, 0) + 1

    seen: dict[str, int] = {}
    for product in scouted:
        seen[product.source] = seen.get(product.source, 0) + 1

    silent = [s for s in expected if s not in counts]
    rows = [
        SourceCoverage(
            source=source,
            label=delivery_for(source, table).label or source,
            count=counts.get(source, 0),
            cheapest=cheapest.get(source),
            found=found.get(source, counts.get(source, 0)),
            scouted=seen.get(source, 0),
        )
        for source in [*counts, *silent]
    ]
    return sorted(rows, key=lambda r: (-r.count, r.cheapest if r.cheapest is not None else 0.0))


def split_by_size_confidence(
    products: list[Product], hunt: Hunt
) -> tuple[list[Product], list[Product]]:
    """Split into (confirmed in a wanted size, size not stated by the shop).

    A product the shop *did* state sizes for, in which the wanted size is absent, belongs
    in neither list: the shop has answered the question and the answer is no.
    """
    confirmed = [p for p in products if matched_sizes(p, hunt)]
    unknown = [p for p in products if not p.sizes_known]
    return confirmed, unknown

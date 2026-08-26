"""Rank a hunt's candidates into a shortlist a human can act on in thirty seconds.

The hunt report answers "what changed?". This answers a different question — "what should
I buy?" — and it needs three things the change report does not:

**Landed cost, not shelf price.** A €45 boot from Ireland at €7 delivery costs more than a
€50 boot collected in Rīga. Sorting on the shelf price quietly recommends the wrong shop,
so everything here sorts on what actually leaves the account.

**Source diversity.** One retailer holds most of the discounted stock, so a plain
cheapest-first list is ten rows from the same shop — which reads like a shortlist but
offers no real choice, and no fallback if that shop's stock is stale.

**An honest split on size.** A boot confirmed in EU 37 and a boot that merely might be are
not comparable, and averaging them into one list hides which is which. They get separate
lists, and the unconfirmed one carries the sizes the shop *does* publish so the reader can
judge for themselves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

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
    """The hunt's wanted sizes this product is actually in stock in."""
    if not product.sizes_known:
        return []
    return sorted(normalise_sizes(hunt.sizes) & normalise_sizes(product.sizes), key=_as_float)


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
    """Cheapest by landed cost, but no more than ``per_source`` rows from one shop.

    The cap is relaxed rather than enforced: if honouring it would return fewer than
    ``limit`` rows, the remainder is filled cheapest-first regardless of source. A short
    list is worse than a repetitive one — the reader wants ten options, and being handed
    six because the rule could not be satisfied helps nobody.
    """
    ranked = sorted(products, key=lambda p: (landed_cost(p, delivery_for(p.source, table)), p.price))
    chosen: list[Product] = []
    counts: dict[str, int] = {}
    for product in ranked:
        if len(chosen) >= limit:
            break
        used = counts.get(product.source, 0)
        if used >= max(1, per_source):
            continue
        counts[product.source] = used + 1
        chosen.append(product)

    if len(chosen) < limit:
        already = {id(p) for p in chosen}
        for product in ranked:
            if len(chosen) >= limit:
                break
            if id(product) not in already:
                chosen.append(product)
    # Re-sort: the fallback appends in catalogue order, so without this the list can show
    # a €108 boot above a €62 one, which makes a "cheapest first" list actively misleading.
    return sorted(chosen, key=lambda p: landed_cost(p, delivery_for(p.source, table)))


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

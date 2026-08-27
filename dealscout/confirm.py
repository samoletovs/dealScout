"""Spend the confirmation budget where it can actually change the email.

The shortlist judges on what a listing states, then re-reads the product pages of the
survivors that still owe a size or an RRP. That second pass is capped (``max_confirmations``)
because it is one polite request per boot, and on a real run there are far more unresolved
boots than slots — ~87 kept, 25 slots. So *which* 25 matters, and until now it was decided
by a bare slice in whatever order candidates arrived.

Two facts let the same budget buy more, and they are different in kind:

* **Some sources cannot be resolved by a second request at all.** ``SOURCES.md`` records,
  from investigation rather than counting, that futbolemotion.com and sportland.lv publish
  the same ld+json on the listing and the product page and fill per-size stock from a
  separate API the page never carries. So re-fetching a boot from one of these learns
  neither a size nor anything new about its price — the listing already held everything the
  product page holds. A confirmation slot spent there is pure waste, every run, forever, and
  the measured ``futbolemotion.com=0/7`` on a real run is exactly that waste. These sources
  are declared in ``scrape.size_unreadable_sources`` and a boot from one is not confirmed.

* **A cheap boot's size is worth more than an expensive one's.** Resolving a €64 boot's
  size promotes it into the confirmed "in your size" list; resolving a €249 boot's size
  changes nothing the owner will act on. So what remains is ordered cheapest-first, mirroring
  ``scout.py``'s already-ordered link budget rather than leaving this one unordered beside it.

Both are opinions about *ordering and skipping*, never about the answer: nothing here fills
in a size or drops an unresolved boot. An unresolved boot still reaches the email's
"❔ Size not published" section exactly as before — a skipped source's boot simply stays
unresolved instead of spending a request to stay unresolved.

The declared skip is knowledge that can rot — a shop can start publishing sizes, or start
serving distinct product pages. So it is paired with :func:`newly_readable`: on the rare run
where such a source *is* fetched anyway (it never is here, but the guard is cheap) and
returns a size, the fact is reported so the config line is revisited rather than trusted
forever. Pure; the entrypoint does the logging and the reading of config.
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import Product


def owes_size(product: Product) -> bool:
    """The listing never stated a size for this boot."""
    return not product.sizes_known


def owes_rrp(product: Product) -> bool:
    """The listing carried no reference/RRP price for this boot."""
    return product.reference_price is None


def needs_confirmation(product: Product) -> bool:
    """True when a product page might still teach us a size or an RRP."""
    return owes_size(product) or owes_rrp(product)


def _source(product: Product) -> str:
    return (product.source or "").lower().removeprefix("www.")


def plan_confirmations(
    candidates: Iterable[Product],
    limit: int,
    size_unreadable: frozenset[str] = frozenset(),
) -> list[Product]:
    """Choose, and order, the product pages worth re-reading this run (pure).

    ``candidates`` is the kept pool (already filtered to what the hunt would show). The
    returned list is what to fetch, best value first, capped at ``limit``:

    1. keep only boots that still owe a size or an RRP;
    2. drop a boot whose source cannot be resolved by a second request — one that serves the
       same listing data on its product page, so re-fetching it learns nothing. Measured:
       ``futbolemotion.com=0/7`` a run. These are declared in ``size_unreadable`` because a
       missing per-size stock is the fact that makes their product page redundant;
    3. order the survivors cheapest-first, because resolving a cheap boot's size is what
       actually promotes it into the confirmed list.

    ``limit <= 0`` disables confirmation entirely, matching the previous slice semantics.
    """
    if limit <= 0:
        return []
    unreadable = frozenset(s.lower().removeprefix("www.") for s in size_unreadable)
    worth: list[Product] = []
    for product in candidates:
        if not needs_confirmation(product):
            continue
        # A source whose product page repeats its listing can never repay a slot: it states
        # no size, and its RRP is already whatever the listing gave. Asking it is guaranteed
        # waste, so its boots are left unresolved rather than fetched to stay unresolved.
        if _source(product) in unreadable and owes_size(product):
            continue
        worth.append(product)
    worth.sort(key=lambda p: p.price)
    return worth[:limit]


def newly_readable(
    asked: Iterable[Product],
    answered: dict[str, Product],
    size_unreadable: frozenset[str] = frozenset(),
) -> set[str]:
    """Declared-unreadable sources that nevertheless returned a size (pure).

    The whole risk of a config skip is that it outlives the fact it records: a shop starts
    publishing per-size stock and the tool keeps skipping it on 2026's knowledge. This is the
    defensive net for that: any source in ``size_unreadable`` whose product page, once
    fetched, states a size the listing did not, is reported so the config line is re-checked
    rather than silently trusted.

    Note the honest limit of a *zero-extra-cost* guard: a boot from such a source that owes a
    size is skipped by :func:`plan_confirmations`, so it is never fetched and cannot surprise
    us here — detecting that would cost the very request the skip saves. What this does catch
    is the day such a source is fetched for another reason (an RRP) and returns a size anyway,
    and it stands ready if a future path fetches these sources directly.
    """
    unreadable = frozenset(s.lower().removeprefix("www.") for s in size_unreadable)
    surprised: set[str] = set()
    for product in asked:
        source = _source(product)
        if source not in unreadable:
            continue
        after = answered.get(product.url)
        # The listing for a fetched unreadable source owes only an RRP (a boot owing a size
        # is skipped, never fetched). So a product page that comes back stating a size at all
        # is the surprise: the documented "no per-size stock on the page" no longer holds.
        if after is not None and after.sizes_known and not product.sizes_known:
            surprised.add(source)
    return surprised

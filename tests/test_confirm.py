"""The confirmation budget planner: skip what cannot answer, order what remains.

These pin invariants, not counts. The two that matter for this task:
  * a source that structurally cannot answer a size is not asked *to learn a size*;
  * an unresolved boot is never dropped or filled in — it only reorders.
"""

from __future__ import annotations

from dataclasses import replace

from dealscout.confirm import newly_readable, needs_confirmation, plan_confirmations
from dealscout.models import Product


def _boot(
    url: str,
    source: str,
    price: float = 90.0,
    *,
    sizes_known: bool = False,
    reference_price: float | None = None,
) -> Product:
    return Product(
        title="adidas Predator Elite FG",
        category="football_boots",
        price=price,
        reference_price=reference_price,
        currency="EUR",
        url=url,
        source=source,
        brand="adidas",
        sizes=frozenset({"37.33"}) if sizes_known else frozenset(),
        sizes_known=sizes_known,
    )


# --- what gets asked at all -----------------------------------------------------------


def test_a_size_unreadable_source_is_not_asked_at_all():
    """The heart of the task: a shop whose product page repeats its listing wastes the slot.

    futbolemotion states no per-size stock and serves the same ld+json on the product page
    as the listing, so re-fetching it learns neither a size nor a new RRP — measured
    futbolemotion.com=0/7 on a real run. Its boots must not consume a confirmation request.
    """
    readable = _boot("https://a/1", "teamsport.lv")  # owes a size, but its page can answer
    only_size = _boot(
        "https://b/1", "futbolemotion.com", reference_price=130.0
    )  # owes only a size
    also_rrp = _boot("https://b/2", "futbolemotion.com")  # owes size AND rrp — still futile

    picked = plan_confirmations(
        [readable, only_size, also_rrp],
        limit=10,
        size_unreadable=frozenset({"futbolemotion.com"}),
    )

    urls = {p.url for p in picked}
    assert readable.url in urls, "a source that can answer is still asked"
    assert only_size.url not in urls, "a futile source is not asked"
    assert also_rrp.url not in urls, "even owing an RRP, a futile source's page adds nothing"


def test_a_boot_owing_nothing_is_never_confirmed():
    settled = _boot("https://a/1", "teamsport.lv", sizes_known=True, reference_price=200.0)
    assert not needs_confirmation(settled)
    assert plan_confirmations([settled], limit=10) == []


# --- ordering -------------------------------------------------------------------------


def test_the_budget_is_spent_cheapest_first():
    """Resolving a €64 boot's size promotes it into the confirmed list; a €249 boot's
    size changes nothing the owner acts on. When slots are scarce the cheap ones must win.
    """
    dear = _boot("https://a/dear", "teamsport.lv", price=249.0)
    cheap = _boot("https://a/cheap", "teamsport.lv", price=64.0)
    mid = _boot("https://a/mid", "teamsport.lv", price=120.0)

    picked = plan_confirmations([dear, cheap, mid], limit=2)

    assert [p.url for p in picked] == [cheap.url, mid.url], "the two cheapest, in order"


def test_the_cap_is_honoured_and_a_nonpositive_cap_confirms_nothing():
    boots = [_boot(f"https://a/{i}", "teamsport.lv", price=float(i)) for i in range(5)]
    assert len(plan_confirmations(boots, limit=3)) == 3
    assert plan_confirmations(boots, limit=0) == []


# --- the anti-rot guard ---------------------------------------------------------------


def test_a_source_marked_unreadable_that_returns_a_size_is_flagged():
    """A config skip must not outlive the fact it records. If a declared-unreadable shop
    answers a size after all (we fetched it for its RRP), the run has to notice so the
    config line is revisited rather than trusted into 2027.
    """
    asked = [_boot("https://b/1", "futbolemotion.com")]  # fetched for its missing RRP
    answered = {
        "https://b/1": replace(
            asked[0], sizes=frozenset({"37.33"}), sizes_known=True, reference_price=130.0
        )
    }

    surprised = newly_readable(asked, answered, frozenset({"futbolemotion.com"}))

    assert surprised == {"futbolemotion.com"}


def test_a_source_that_stays_silent_is_not_flagged():
    asked = [_boot("https://b/1", "futbolemotion.com", reference_price=130.0)]
    answered = {"https://b/1": asked[0]}  # learned nothing
    assert newly_readable(asked, answered, frozenset({"futbolemotion.com"})) == set()

"""Rendering tests for price memory — what the email says, and when it says nothing.

The retailer's "RRP €330, -70%" is the retailer's own claim about its own "was" price.
These tests pin the sentence that can actually be checked, and — more importantly — pin
the silence that has to follow when the history cannot support one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dealscout.models import Change, Hunt, Product, Verdict
from dealscout.notify import render_hunt_report, render_shortlist
from dealscout.pricehistory import Observation, PriceMemory, summarise, summarise_all

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
URL = "https://www.komanda.lv/products/predator-elite"

HUNT = Hunt(
    id="boots-junior",
    label="Football boots — Elite, EU 37",
    sizes=("37", "37.33"),
    brands=("adidas",),
)


def _product(price: float = 95.0, url: str = URL) -> Product:
    return Product(
        title="adidas Predator Elite FG",
        category="football_boots",
        price=price,
        reference_price=220.0,
        currency="EUR",
        url=url,
        brand="adidas",
        source="komanda.lv",
        sizes=frozenset({"37.33"}),
        sizes_known=True,
    )


def _history(prices: list[float], every_days: float = 10.0) -> list[Observation]:
    span = timedelta(days=every_days)
    start = NOW - span * (len(prices) - 1)
    return [Observation(url=URL, price=p, at=start + span * i) for i, p in enumerate(prices)]


def _memory(prices: list[float], price: float, every_days: float = 10.0) -> dict[str, PriceMemory]:
    return {URL: summarise(price, _history(prices, every_days), NOW)}


def _shortlist(price: float, memory: dict[str, PriceMemory] | None) -> str:
    return render_shortlist(HUNT, [_product(price)], [], {}, memory=memory)


def test_shortlist_should_say_when_a_price_is_the_cheapest_it_has_ever_seen():
    body = _shortlist(85.0, _memory([120.0, 110.0, 95.0, 85.0], 85.0))

    assert "cheapest seen in 30 days" in body


def test_shortlist_should_say_how_far_above_its_own_low_a_price_sits():
    body = _shortlist(105.0, _memory([120.0, 95.0, 110.0, 105.0], 105.0))

    assert "€10 above its 30-day low" in body


def test_shortlist_should_stay_silent_when_two_runs_cannot_support_a_claim():
    # The honesty case: a thin history must add nothing at all — not "no history",
    # and above all not a confident "lowest in 30 days".
    thin = _memory([110.0, 85.0], 85.0, every_days=40.0)

    body = _shortlist(85.0, thin)

    assert "cheapest" not in body
    assert "-day low" not in body
    assert "history" not in body


def test_shortlist_should_stay_silent_for_a_product_it_has_never_seen_before():
    body = _shortlist(85.0, {})

    assert "cheapest" not in body


def test_shortlist_should_render_unchanged_when_no_memory_is_supplied():
    assert _shortlist(85.0, None) == _shortlist(85.0, {})


def test_shortlist_should_never_claim_a_zero_euro_gap_above_the_low():
    # €0.40 above the low is neither "the cheapest" nor "€0 above" — so say neither.
    body = _shortlist(95.4, _memory([120.0, 110.0, 95.0, 95.4], 95.4))

    assert "cheapest" not in body
    assert "€0 above" not in body


def test_shortlist_should_quote_only_the_history_it_actually_holds():
    # Three weeks of memory may not be described as a ninety-day low.
    body = _shortlist(85.0, _memory([120.0, 110.0, 85.0], 85.0, every_days=10.5))

    assert "21 days" in body
    assert "90" not in body


def test_shortlist_should_keep_the_retailer_claim_and_the_measured_one_side_by_side():
    # The invariant, not the wording: the retailer's own discount claim and the sentence
    # that says whether it means anything sit together on one line, the discount first.
    # Pinning the exact phrasing here made this test break on a pure rewording once already.
    body = _shortlist(85.0, _memory([120.0, 110.0, 95.0, 85.0], 85.0))

    line = next(ln for ln in body.splitlines() if "−61%" in ln or "-61%" in ln)
    assert "cheapest seen in 30 days" in line
    assert line.index("61%") < line.index("cheapest")


def test_shortlist_should_read_memory_through_the_tracking_stripped_url():
    tracked = _product(85.0, url=f"{URL}?utm_source=mail")
    memory = summarise_all([tracked], {URL: _history([120.0, 110.0, 95.0, 85.0])}, NOW)

    body = render_shortlist(HUNT, [tracked], [], {}, memory=memory)

    assert "cheapest seen in 30 days" in body


def test_shortlist_should_annotate_rows_whose_size_the_shop_never_published():
    body = render_shortlist(
        HUNT, [], [_product(85.0)], {}, memory=_memory([120.0, 110.0, 95.0, 85.0], 85.0)
    )

    assert "cheapest seen in 30 days" in body


def test_hunt_report_should_carry_the_price_memory_beside_the_change_badge():
    results = [(_product(85.0), Verdict(True, 9.0, (), "must-buy"), Change(_product(85.0), "new"))]

    body = render_hunt_report(HUNT, results, memory=_memory([120.0, 110.0, 95.0, 85.0], 85.0))

    assert "**NEW**" in body
    assert "cheapest seen in 30 days" in body


def test_hunt_report_should_stay_silent_when_the_history_is_too_thin():
    results = [(_product(85.0), Verdict(True, 9.0, (), "must-buy"), Change(_product(85.0), "new"))]

    body = render_hunt_report(HUNT, results, memory=_memory([110.0, 85.0], 85.0, every_days=40.0))

    assert "cheapest" not in body

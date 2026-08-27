"""Unit tests for the shortlist entrypoint (no network — `scout` is stubbed)."""

from __future__ import annotations

import asyncio

import pytest

import dealscout.run_shortlist as run_shortlist
from dealscout.models import Hunt, Product

HUNT = Hunt(
    id="boots",
    category="football_boots",
    sizes=("37.33",),
    brands=("adidas",),
    good_offer=500.0,
)

CONFIG: dict = {"scrape": {"delay_seconds": 0, "max_confirmations": 0}}


def _boot(title: str, url: str, price: float = 90.0) -> Product:
    return Product(
        title=title,
        category="football_boots",
        price=price,
        reference_price=200.0,
        currency="EUR",
        url=url,
        source="shop.example",
        brand="adidas",
        sizes=frozenset({"37.33"}),
        sizes_known=True,
    )


def _stub_scout(products: list[Product]):
    async def _scout(hunt, config, api_key=None, vocab=None):
        return products

    return _scout


def _run(hunt: Hunt, products: list[Product], rejected: frozenset[str], monkeypatch):
    monkeypatch.setattr(run_shortlist, "scout", _stub_scout(products))
    return asyncio.run(
        run_shortlist.shortlist_for(hunt, CONFIG, limit=10, per_source=5, rejected=rejected)
    )


def test_should_drop_a_product_the_owner_has_thumbed_down(monkeypatch):
    # The shortlist email prints 👍/👎 links, so it has to honour them. Without this a
    # rejected boot returns on every run and the feedback loop is decorative.
    keep = _boot("adidas Predator Elite FG", "https://shop.example/keep")
    drop = _boot("adidas F50 Elite FG", "https://shop.example/drop")
    confirmed, _, checked, _coverage = _run(HUNT, [keep, drop], frozenset({drop.url}), monkeypatch)
    assert [p.url for p in confirmed] == [keep.url]
    assert checked == 1


def test_should_keep_everything_when_nothing_has_been_rejected(monkeypatch):
    boots = [
        _boot("adidas Predator Elite FG", "https://shop.example/a"),
        _boot("adidas F50 Elite FG", "https://shop.example/b"),
    ]
    confirmed, _, checked, _coverage = _run(HUNT, boots, frozenset(), monkeypatch)
    assert len(confirmed) == 2
    assert checked == 2


def test_uncapped_should_lift_the_ceiling_without_clearing_every_band():
    # Clearing the bands makes the judge call everything "not a deal", which returned an
    # empty shortlist — the ceiling is the only thing that should go.
    hunt = Hunt(id="x", must_buy=70.0, good_offer=100.0, never_above=100.0)
    opened = run_shortlist._uncapped(hunt)
    assert opened.never_above is None
    assert opened.must_buy == 70.0
    assert opened.good_offer == float("inf")


# --- argument parsing: the run is the side effect, so an unknown flag must stop it ---


def test_help_should_print_help_rather_than_run_and_email():
    """`--help` used to be a six-minute live scrape that emailed the owner.

    main() filtered out anything starting with "-", so no flag was ever recognised and
    every flag was silently a default run with sending on.
    """
    with pytest.raises(SystemExit) as exit_info:
        run_shortlist.parse_args(["--help"])

    assert exit_info.value.code == 0


def test_a_mistyped_no_email_flag_should_refuse_rather_than_send():
    """The dangerous half: --no-emails used to send the email it was meant to suppress."""
    with pytest.raises(SystemExit) as exit_info:
        run_shortlist.parse_args(["--no-emails"])

    assert exit_info.value.code != 0


def test_no_email_should_still_suppress_sending():
    assert run_shortlist.parse_args(["--no-email"]).no_email is True
    assert run_shortlist.parse_args([]).no_email is False


def test_a_named_hunt_should_still_be_honoured():
    assert run_shortlist.parse_args(["boots-junior"]).hunt == "boots-junior"
    assert run_shortlist.parse_args([]).hunt == ""

"""Unit tests for the shortlist entrypoint (no network — `scout` is stubbed)."""

from __future__ import annotations

import asyncio

import pytest

from dataclasses import replace
from pathlib import Path

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
    run = _run(HUNT, [keep, drop], frozenset({drop.url}), monkeypatch)
    assert [p.url for p in run.confirmed] == [keep.url]
    assert run.checked == 1


def test_should_keep_everything_when_nothing_has_been_rejected(monkeypatch):
    boots = [
        _boot("adidas Predator Elite FG", "https://shop.example/a"),
        _boot("adidas F50 Elite FG", "https://shop.example/b"),
    ]
    run = _run(HUNT, boots, frozenset(), monkeypatch)
    assert len(run.confirmed) == 2
    assert run.checked == 2


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


# --- price memory must remember every boot judged, not only the ones shown ------------


def test_a_boot_that_lost_its_place_should_still_be_remembered(monkeypatch):
    """The memory used to see only the rows that reached the email.

    That made it mute at the one moment it matters: a boot appearing for the first time
    at a startling price has no history precisely because it was never shown before. The
    cap that keeps the email readable was silently also capping what could be learned.
    """
    boots = [
        replace(
            _boot("adidas Predator Elite FG", f"https://shop{i}.example/b"),
            source=f"shop{i}.example",
        )
        for i in range(14)
    ]

    run = _run(HUNT, boots, frozenset(), monkeypatch)

    assert len(run.confirmed) == 10, "the email is still capped at ten rows"
    assert len(run.kept) == 14, "but everything that qualified is available to remember"
    unshown = {p.url for p in run.kept} - {p.url for p in run.confirmed}
    assert len(unshown) == 4, "four qualified boots never reached the email"

def test_the_run_should_log_a_price_for_every_boot_it_judged(monkeypatch, tmp_path):
    """The behaviour, not the plumbing: what reaches the price log when main() runs.

    The previous test only proved `kept` was exposed. Reverting the line that uses it
    left all 495 tests green — a suite certifying the implementation rather than the
    requirement, which is the failure that shipped a regression here yesterday. This
    one asserts what `append` actually received.
    """
    boots = [
        replace(
            _boot("adidas Predator Elite FG", f"https://shop{i}.example/b"),
            source=f"shop{i}.example",
        )
        for i in range(14)
    ]
    logged: list = []

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_shortlist, "scout", _stub_scout(boots))
    monkeypatch.setattr(run_shortlist, "load_config", lambda _p: CONFIG)
    monkeypatch.setattr(run_shortlist, "config_path", lambda: Path("config.example.yaml"))
    monkeypatch.setattr(run_shortlist, "load_hunts", lambda _c, _o="": [HUNT])
    monkeypatch.setattr(run_shortlist, "append", lambda obs, _path: logged.extend(obs))

    async def _no_feedback():
        return []

    async def _no_send(_subject, _body):
        return False

    monkeypatch.setattr(run_shortlist, "read_feedback", _no_feedback)
    monkeypatch.setattr(run_shortlist, "send_email", _no_send)

    asyncio.run(run_shortlist.main([]))

    assert len(logged) == 14, "every boot that qualified is remembered, not only the ten shown"

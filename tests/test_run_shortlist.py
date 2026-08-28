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
    monkeypatch.setattr(run_shortlist, "append_dir", lambda obs, _dir: logged.extend(obs))
    monkeypatch.setattr(run_shortlist, "rewrite_dir", lambda _hist, _dir: None)

    async def _no_feedback():
        return []

    async def _no_send(_subject, _body):
        return False

    monkeypatch.setattr(run_shortlist, "read_feedback", _no_feedback)
    monkeypatch.setattr(run_shortlist, "send_email", _no_send)

    asyncio.run(run_shortlist.main([]))

    assert len(logged) == 14, "every boot that qualified is remembered, not only the ten shown"


# --- what the confirmation budget actually bought -------------------------------------


def _unsized(url: str, source: str) -> Product:
    """A listing-stage product still owing both a size and an RRP."""
    return replace(_boot("adidas Predator Elite FG", url), source=source, sizes=frozenset(),
                   sizes_known=False, reference_price=None)


def test_a_source_that_answers_nothing_should_be_counted_separately_from_one_that_does():
    """Zero yield is invisible downstream: a page that says nothing and a page never
    fetched produce the same silence. While teamsport's reader was broken it consumed
    ~22 of 25 slots a run, every run, and nothing reported it."""
    asked = [_unsized("https://a.example/1", "mute.example"),
             _unsized("https://a.example/2", "mute.example"),
             _unsized("https://b.example/1", "helpful.example")]
    answered = {
        "https://a.example/1": _unsized("https://a.example/1", "mute.example"),
        "https://a.example/2": _unsized("https://a.example/2", "mute.example"),
        "https://b.example/1": replace(
            _unsized("https://b.example/1", "helpful.example"),
            sizes=frozenset({"37.33"}), sizes_known=True,
        ),
    }

    payoff = run_shortlist.confirmation_payoff(asked, answered)

    assert payoff["mute.example"] == (2, 0)
    assert payoff["helpful.example"] == (1, 1)


def test_gaining_only_an_rrp_should_still_count_as_learning_something():
    """A size is not the only thing worth a request: an RRP is what makes -58% sayable."""
    asked = [_unsized("https://c.example/1", "rrp.example")]
    answered = {
        "https://c.example/1": replace(
            _unsized("https://c.example/1", "rrp.example"), reference_price=130.0
        )
    }

    assert run_shortlist.confirmation_payoff(asked, answered)["rrp.example"] == (1, 1)


def test_a_page_that_failed_to_load_should_count_as_spent_and_unhelpful():
    """enrich_all drops what it could not read, so the url is simply absent."""
    asked = [_unsized("https://d.example/1", "broken.example")]

    assert run_shortlist.confirmation_payoff(asked, {})["broken.example"] == (1, 0)


# --- the confirmation budget: don't ask what can't answer; still show what stays unknown -


def _capture_enrich(monkeypatch, resolve=None):
    """Record which URLs `shortlist_for` chose to confirm, and optionally resolve them.

    Returns the list the planner fed to enrich_all, so a test can assert what the scarce
    budget was actually spent on rather than only what came back.
    """
    asked_urls: list[str] = []

    async def _enrich(products, delay):
        asked_urls.extend(p.url for p in products)
        out = []
        for p in products:
            out.append(resolve(p) if resolve else p)
        return out

    monkeypatch.setattr(run_shortlist, "enrich_all", _enrich)
    return asked_urls


def test_a_size_unreadable_source_does_not_consume_a_slot_for_a_size(monkeypatch):
    """The task's whole point: with a tight budget, a boot from a shop that cannot state a
    size must not take a slot from one that can — and asking it would learn nothing anyway.
    """
    config = {"scrape": {"delay_seconds": 0, "max_confirmations": 1,
                         "size_unreadable_sources": ["mute.example"]}}
    cannot = replace(
        _unsized("https://cannot/1", "mute.example"), reference_price=200.0, price=50.0
    )  # first in order and cheaper — a bare slice would grab it
    can_answer = replace(
        _unsized("https://can/1", "loud.example"), reference_price=200.0, price=90.0
    )  # dearer, but its source can actually state a size

    monkeypatch.setattr(run_shortlist, "scout", _stub_scout([cannot, can_answer]))
    asked = _capture_enrich(monkeypatch)
    asyncio.run(run_shortlist.shortlist_for(HUNT, config, limit=10, per_source=5))

    assert asked == ["https://can/1"], "the one slot went to the source that can answer"


def test_an_unresolved_boot_still_reaches_the_email(monkeypatch):
    """Never silently dropped: a boot the budget could not resolve stays visible in the
    "size not published" list rather than vanishing because we skipped or ran out of slots.
    """
    config = {"scrape": {"delay_seconds": 0, "max_confirmations": 0}}
    unresolved = replace(_unsized("https://x/1", "mute.example"), reference_price=200.0)

    monkeypatch.setattr(run_shortlist, "scout", _stub_scout([unresolved]))
    run = asyncio.run(run_shortlist.shortlist_for(HUNT, config, limit=10, per_source=5))

    assert [p.url for p in run.unconfirmed] == ["https://x/1"], "still shown, just unconfirmed"
    assert run.confirmed == [], "and never optimistically promoted"


def test_the_budget_is_spent_cheapest_first_end_to_end(monkeypatch):
    """A single scarce slot goes to the cheapest unresolved boot, because resolving a cheap
    boot's size is what actually changes the email.
    """
    config = {"scrape": {"delay_seconds": 0, "max_confirmations": 1}}
    dear = replace(_unsized("https://a/dear", "loud.example"), reference_price=200.0, price=249.0)
    cheap = replace(_unsized("https://a/cheap", "loud.example"), reference_price=200.0, price=64.0)

    monkeypatch.setattr(run_shortlist, "scout", _stub_scout([dear, cheap]))
    asked = _capture_enrich(monkeypatch)
    asyncio.run(run_shortlist.shortlist_for(HUNT, config, limit=10, per_source=5))

    assert asked == ["https://a/cheap"], "the cheapest unresolved boot got the slot"


def test_a_remembered_rrp_frees_a_slot_for_a_size(monkeypatch):
    """Half of confirmations chase an RRP. If yesterday taught us this boot's RRP, today's
    slot should not be re-spent on it — it should go to a boot that still owes a size.
    """
    config = {"scrape": {"delay_seconds": 0, "max_confirmations": 1}}
    # Boot A: size known, RRP missing — would normally take the slot to learn its RRP.
    knows_size = replace(
        _boot("adidas Predator Elite FG", "https://a/rrp", price=40.0),
        reference_price=None,
    )
    # Boot B: dearer, owes a size — only wins the slot once A's RRP is remembered.
    owes_size = replace(_unsized("https://a/size", "loud.example"), reference_price=200.0, price=99.0)

    monkeypatch.setattr(run_shortlist, "scout", _stub_scout([knows_size, owes_size]))
    asked = _capture_enrich(monkeypatch)
    asyncio.run(
        run_shortlist.shortlist_for(
            HUNT, config, limit=10, per_source=5, rrp_memory={"https://a/rrp": 130.0}
        )
    )

    assert asked == ["https://a/size"], "the slot went to the boot still owing a size"

# --- a run that cannot send has not succeeded -----------------------------------------


def _stub_run(monkeypatch, tmp_path, boots, send_email):
    """Wire main() to a fixed set of boots and a controllable send."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_shortlist, "scout", _stub_scout(boots))
    monkeypatch.setattr(run_shortlist, "load_config", lambda _p: CONFIG)
    monkeypatch.setattr(run_shortlist, "config_path", lambda: Path("config.example.yaml"))
    monkeypatch.setattr(run_shortlist, "load_hunts", lambda _c, _o="": [HUNT])
    monkeypatch.setattr(run_shortlist, "append_dir", lambda _obs, _dir: None)
    monkeypatch.setattr(run_shortlist, "rewrite_dir", lambda _hist, _dir: None)
    monkeypatch.setattr(run_shortlist, "send_email", send_email)

    async def _no_feedback():
        return []

    monkeypatch.setattr(run_shortlist, "read_feedback", _no_feedback)


def test_a_shortlist_that_could_not_be_sent_should_fail_the_run(monkeypatch, tmp_path):
    """This shipped silent for months.

    COURIER_URL/COURIER_KEY/DEALSCOUT_EMAIL_TO were never set as repository secrets, so
    every scheduled run scraped seven retailers, judged them, wrote the markdown, logged
    "courier not configured - skipping send" and exited 0. GitHub drew a green tick twice
    a day and the owner never got an email. A warning is not enough, because nobody reads
    the log of a run that passed.
    """
    boots = [_boot("adidas Predator Elite FG", "https://shop.example/a")]

    async def _refuses_to_send(_subject, _body):
        return False

    _stub_run(monkeypatch, tmp_path, boots, send_email=_refuses_to_send)

    assert asyncio.run(run_shortlist.main([])) == 1


def test_a_shortlist_that_was_sent_should_succeed(monkeypatch, tmp_path):
    boots = [_boot("adidas Predator Elite FG", "https://shop.example/a")]

    async def _sends(_subject, _body):
        return True

    _stub_run(monkeypatch, tmp_path, boots, send_email=_sends)

    assert asyncio.run(run_shortlist.main([])) == 0


def test_no_email_should_stay_a_success_because_not_sending_was_the_request(
    monkeypatch, tmp_path
):
    """`--no-email` is how you say you meant it, which is what makes the failure above safe."""
    boots = [_boot("adidas Predator Elite FG", "https://shop.example/a")]

    async def _must_not_be_called(_subject, _body):  # pragma: no cover - the point is it isn't
        raise AssertionError("--no-email must not attempt a send")

    _stub_run(monkeypatch, tmp_path, boots, send_email=_must_not_be_called)

    assert asyncio.run(run_shortlist.main(["--no-email"])) == 0
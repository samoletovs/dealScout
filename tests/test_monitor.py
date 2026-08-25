"""Unit tests for the monitor — the ledger that keeps a scheduled hunt quiet."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from dealscout.models import Product
from dealscout.monitor import (
    canonical_url,
    classify,
    forget_stale,
    load_state,
    record,
    save_state,
)

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
URL = "https://www.sportsdirect.lv/boot-123456"


def _product(**overrides) -> Product:
    base = dict(
        title="Nike Jr. Mercurial Superfly 10 Elite AG",
        category="football_boots",
        price=65.0,
        reference_price=280.0,
        currency="EUR",
        url=URL,
        source="sportsdirect.lv",
        sizes=frozenset({"37", "37.5"}),
        sizes_known=True,
    )
    base.update(overrides)
    return Product(**base)


def test_canonical_url_should_strip_click_tracking_parameters():
    url = "https://shop.eu/boot?utm_source=mail&gclid=abc&colour=red"
    assert canonical_url(url) == "https://shop.eu/boot?colour=red"


def test_canonical_url_should_keep_the_fragment_that_carries_the_colourway():
    tagged = "https://www.sportsdirect.lv/boots-084181#colcode=08418103"
    assert canonical_url(tagged).endswith("#colcode=08418103")


def test_canonical_url_should_key_two_differently_tracked_links_as_one_product():
    assert canonical_url("https://shop.eu/boot?utm_campaign=a") == canonical_url(
        "https://shop.eu/boot?fbclid=b"
    )


def test_should_classify_an_unseen_product_as_new():
    change = classify(_product(), {})
    assert change.kind == "new"
    assert change.is_news is True


def test_should_classify_a_meaningful_price_fall_as_a_price_drop():
    change = classify(_product(price=80.0), {URL: {"price": 100.0}}, min_drop_pct=5.0)
    assert change.kind == "price-drop"
    assert change.previous_price == 100.0
    assert change.is_news is True


def test_should_stay_quiet_about_a_trivial_price_wobble():
    change = classify(_product(price=98.0), {URL: {"price": 100.0}}, min_drop_pct=5.0)
    assert change.kind == "seen"
    assert change.is_news is False


def test_should_report_a_wanted_size_coming_back_into_stock():
    state = {URL: {"price": 65.0, "in_stock": False}}
    assert classify(_product(), state, wanted_sizes=("37", "37.5")).kind == "back-in-stock"


def test_should_not_report_back_in_stock_for_something_never_out_of_stock():
    state = {URL: {"price": 65.0, "in_stock": True}}
    assert classify(_product(), state, wanted_sizes=("37",)).kind == "seen"


def test_should_survive_a_corrupt_price_in_the_ledger():
    change = classify(_product(), {URL: {"price": "n/a"}})
    assert change.kind == "seen"
    assert change.previous_price is None


def test_record_should_store_a_first_sighting_with_both_timestamps():
    entry = record({}, [_product()], ("37", "37.5"), now=NOW)[URL]
    assert entry["first_seen"] == entry["last_seen"] == NOW.isoformat()
    assert entry["price"] == 65.0
    assert entry["best_price"] == 65.0
    assert entry["in_stock"] is True


def test_record_should_remember_the_best_price_ever_seen():
    first = record({}, [_product(price=65.0)], now=NOW)
    later = record(first, [_product(price=90.0)], now=NOW + timedelta(days=1))
    assert later[URL]["price"] == 90.0
    assert later[URL]["best_price"] == 65.0
    assert later[URL]["first_seen"] == NOW.isoformat()


def test_record_should_not_mutate_the_ledger_it_was_given():
    before = record({}, [_product(price=65.0)], now=NOW)
    snapshot = json.dumps(before, sort_keys=True)
    record(before, [_product(price=40.0)], now=NOW)
    assert json.dumps(before, sort_keys=True) == snapshot


def test_record_should_leave_stock_unknown_when_the_page_never_said():
    state = record({}, [_product(sizes=frozenset(), sizes_known=False)], ("37",), now=NOW)
    assert state[URL]["in_stock"] is None


def test_forget_stale_should_drop_a_product_unseen_for_too_long():
    state = {
        "old": {"last_seen": (NOW - timedelta(days=120)).isoformat()},
        "fresh": {"last_seen": (NOW - timedelta(days=3)).isoformat()},
    }
    assert set(forget_stale(state, older_than_days=90, now=NOW)) == {"fresh"}


def test_forget_stale_should_keep_an_entry_whose_timestamp_is_unreadable():
    # Keeping one stale row beats silently losing price history.
    assert "weird" in forget_stale({"weird": {"last_seen": "yesterday"}}, 90, NOW)


def test_forget_stale_should_treat_a_naive_timestamp_as_utc():
    naive = (NOW - timedelta(days=1)).replace(tzinfo=None).isoformat()
    assert "recent" in forget_stale({"recent": {"last_seen": naive}}, 90, NOW)


def test_load_state_should_return_an_empty_ledger_on_a_first_run(tmp_path):
    assert load_state(tmp_path / "missing.json") == {}


def test_load_state_should_start_fresh_rather_than_crash_on_a_corrupt_ledger(tmp_path):
    path = tmp_path / "hunts.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_state(path) == {}


def test_save_state_should_round_trip_through_a_directory_it_creates(tmp_path):
    path = tmp_path / "state" / "hunts.json"
    state = record({}, [_product()], ("37",), now=NOW)
    save_state(state, path)
    assert load_state(path) == state

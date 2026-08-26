"""Unit tests for price memory — the log that lets dealScout say what a price usually is.

The behaviour under test is as much about *restraint* as about arithmetic: a thin history
must produce no claim at all, and must say so in a field rather than in a zero.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from dealscout.models import Product
from dealscout.pricehistory import (
    HistoryConfig,
    Observation,
    append,
    extend,
    load_history,
    observe,
    prune,
    rewrite,
    summarise,
    summarise_all,
)

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
URL = "https://www.komanda.lv/products/predator-elite"


def _product(**overrides) -> Product:
    base = dict(
        title="adidas Predator Elite FG",
        category="football_boots",
        price=95.0,
        reference_price=220.0,
        currency="EUR",
        url=URL,
        source="komanda.lv",
        sizes=frozenset({"37.33"}),
        sizes_known=True,
    )
    base.update(overrides)
    return Product(**base)


def _series(prices: list[float], every_days: float = 7.0, url: str = URL) -> list[Observation]:
    """Observations ending at NOW, oldest first, one every ``every_days``."""
    span = timedelta(days=every_days)
    start = NOW - span * (len(prices) - 1)
    return [
        Observation(url=url, price=price, at=start + span * i)
        for i, price in enumerate(prices)
    ]


# --------------------------------------------------------------------------- store


def test_load_history_should_report_no_history_when_the_file_is_missing(tmp_path):
    assert load_history(tmp_path / "prices.jsonl") == {}


def test_load_history_should_skip_a_corrupt_line_rather_than_lose_the_whole_log(tmp_path):
    path = tmp_path / "prices.jsonl"
    good = Observation(url=URL, price=95.0, at=NOW).to_json()
    path.write_text(f"{good}\n{{not json\n", encoding="utf-8")

    history = load_history(path)

    assert [o.price for o in history[URL]] == [95.0]


def test_load_history_should_skip_a_line_missing_the_fields_that_make_it_meaningful(tmp_path):
    path = tmp_path / "prices.jsonl"
    path.write_text(json.dumps({"url": URL, "at": NOW.isoformat()}) + "\n", encoding="utf-8")

    assert load_history(path) == {}


def test_load_history_should_return_no_opinion_when_the_path_cannot_be_read(tmp_path):
    # A directory where a file is expected: unreadable, but never a reason to crash a run.
    unreadable = tmp_path / "prices.jsonl"
    unreadable.mkdir()

    assert load_history(unreadable) == {}


def test_load_history_should_return_observations_oldest_first(tmp_path):
    path = tmp_path / "prices.jsonl"
    newer = Observation(url=URL, price=90.0, at=NOW)
    older = Observation(url=URL, price=110.0, at=NOW - timedelta(days=3))
    path.write_text(f"{newer.to_json()}\n{older.to_json()}\n", encoding="utf-8")

    assert [o.price for o in load_history(path)[URL]] == [110.0, 90.0]


def test_load_history_should_treat_a_naive_timestamp_as_utc(tmp_path):
    path = tmp_path / "prices.jsonl"
    naive = {"url": URL, "price": 95.0, "at": NOW.replace(tzinfo=None).isoformat()}
    path.write_text(json.dumps(naive) + "\n", encoding="utf-8")

    assert load_history(path)[URL][0].at == NOW


def test_observe_should_record_one_observation_per_product_per_run():
    tracked = _product(url=f"{URL}?utm_source=mail")

    observations = observe([_product(), tracked], ("37.33",), now=NOW)

    assert [o.url for o in observations] == [URL]


def test_observe_should_capture_stock_for_the_sizes_the_hunt_wants():
    observation = observe([_product()], ("37.33",), now=NOW)[0]

    assert observation.in_stock is True


def test_observe_should_leave_stock_unknown_when_the_page_never_said():
    blind = _product(sizes=frozenset(), sizes_known=False)

    assert observe([blind], ("37.33",), now=NOW)[0].in_stock is None


def test_append_should_round_trip_through_a_directory_it_creates(tmp_path):
    path = tmp_path / "state" / "prices.jsonl"

    append(observe([_product()], ("37.33",), now=NOW), path)

    assert load_history(path)[URL][0].price == 95.0


def test_append_should_add_to_the_previous_run_rather_than_replace_it(tmp_path):
    path = tmp_path / "prices.jsonl"
    append(observe([_product(price=110.0)], now=NOW - timedelta(days=7)), path)

    append(observe([_product(price=95.0)], now=NOW), path)

    assert [o.price for o in load_history(path)[URL]] == [110.0, 95.0]


def test_extend_should_not_mutate_the_history_it_was_given():
    history = {URL: _series([110.0, 100.0])}
    before = len(history[URL])

    extend(history, observe([_product(price=95.0)], now=NOW))

    assert len(history[URL]) == before


# ----------------------------------------------------------------- honest uncertainty


def test_summarise_should_withhold_an_opinion_below_the_minimum_observations():
    thin = _series([110.0, 95.0], every_days=40.0)  # long span, only two points

    memory = summarise(95.0, thin, NOW, min_observations=3, min_span_days=7)

    assert memory.enough_history is False
    assert memory.observations == 2


def test_summarise_should_withhold_an_opinion_when_the_history_is_too_short_in_time():
    same_day = _series([110.0, 100.0, 95.0], every_days=0.5)  # three points, one day

    memory = summarise(95.0, same_day, NOW, min_observations=3, min_span_days=7)

    assert memory.enough_history is False


def test_summarise_should_leave_every_claim_unset_when_it_has_no_opinion():
    # The point of the type: no field may read as a measurement we did not make.
    memory = summarise(95.0, _series([110.0, 95.0]), NOW)

    assert memory.enough_history is False
    assert memory.is_lowest is None
    assert memory.low is None
    assert memory.median is None
    assert memory.above_low is None
    assert memory.days_at_price is None


def test_summarise_should_have_no_opinion_about_a_product_it_has_never_seen():
    memory = summarise(95.0, [], NOW)

    assert (memory.enough_history, memory.observations, memory.span_days) == (False, 0, 0.0)


# ------------------------------------------------------------------------- the reading


def test_summarise_should_report_the_current_price_as_the_lowest_seen():
    memory = summarise(85.0, _series([120.0, 110.0, 95.0, 85.0]), NOW)

    assert memory.enough_history is True
    assert memory.is_lowest is True
    assert memory.above_low == 0.0


def test_summarise_should_measure_how_far_above_the_low_a_price_sits():
    memory = summarise(105.0, _series([120.0, 95.0, 110.0, 105.0]), NOW)

    assert memory.is_lowest is False
    assert memory.above_low == 10.0


def test_summarise_should_report_the_median_of_what_the_product_actually_sold_for():
    memory = summarise(80.0, _series([120.0, 100.0, 90.0, 80.0]), NOW)

    assert memory.median == 95.0
    assert memory.above_median == -15.0


def test_summarise_should_span_the_history_it_actually_holds():
    memory = summarise(95.0, _series([120.0, 110.0, 95.0], every_days=10.0), NOW)

    assert memory.span_days == 20.0


def test_summarise_should_count_the_days_a_price_has_held():
    # 120 fifteen days ago, then 95 at ten, five and zero days ago.
    memory = summarise(95.0, _series([120.0, 95.0, 95.0, 95.0], every_days=5.0), NOW)

    assert memory.days_at_price == 10.0


def test_summarise_should_reset_the_hold_time_when_the_price_has_only_just_moved():
    memory = summarise(80.0, _series([120.0, 110.0, 95.0], every_days=10.0), NOW)

    assert memory.days_at_price == 0.0


def test_summarise_all_should_key_memory_on_the_tracking_stripped_url():
    history = {URL: _series([120.0, 110.0, 95.0])}

    memories = summarise_all([_product(url=f"{URL}?gclid=xyz", price=95.0)], history, NOW)

    assert memories[URL].is_lowest is True


# ------------------------------------------------------------------------------ prune


def test_prune_should_drop_observations_older_than_the_retention_window():
    history = {URL: _series([120.0, 110.0, 95.0], every_days=100.0)}

    kept = prune(history, keep_days=180, max_points=200, now=NOW)

    assert [o.price for o in kept[URL]] == [110.0, 95.0]


def test_prune_should_cap_the_observations_kept_per_product_keeping_the_newest():
    history = {URL: _series([120.0, 110.0, 100.0, 95.0], every_days=1.0)}

    kept = prune(history, keep_days=180, max_points=2, now=NOW)

    assert [o.price for o in kept[URL]] == [100.0, 95.0]


def test_prune_should_forget_a_product_whose_every_observation_has_expired():
    stale = [
        Observation(url=URL, price=120.0, at=NOW - timedelta(days=400)),
        Observation(url=URL, price=110.0, at=NOW - timedelta(days=300)),
    ]

    assert prune({URL: stale}, keep_days=180, max_points=200, now=NOW) == {}


def test_rewrite_should_replace_the_log_with_exactly_the_pruned_history(tmp_path):
    path = tmp_path / "prices.jsonl"
    append(_series([120.0, 110.0, 95.0], every_days=100.0), path)

    rewrite(prune(load_history(path), keep_days=180, max_points=200, now=NOW), path)

    assert [o.price for o in load_history(path)[URL]] == [110.0, 95.0]


def test_rewrite_should_bound_a_log_that_would_otherwise_grow_forever(tmp_path):
    path = tmp_path / "prices.jsonl"
    append(_series([100.0] * 50, every_days=1.0), path)

    rewrite(prune(load_history(path), keep_days=180, max_points=10, now=NOW), path)

    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 10


# ----------------------------------------------------------------------------- config


def test_history_config_should_read_its_limits_from_the_monitor_block():
    config = {
        "monitor": {
            "price_history_path": "state/custom.jsonl",
            "price_history_days": 30,
            "price_history_max_points": 12,
            "price_min_observations": 5,
            "price_min_span_days": 14,
        }
    }

    limits = HistoryConfig.from_config(config)

    assert str(limits.path).endswith("custom.jsonl")
    assert (limits.keep_days, limits.max_points) == (30, 12)
    assert (limits.min_observations, limits.min_span_days) == (5, 14.0)


def test_history_config_should_fall_back_to_defaults_when_config_says_nothing():
    limits = HistoryConfig.from_config({})

    assert limits.keep_days > 0
    assert limits.min_observations >= 2


def test_history_config_should_ignore_a_nonsense_limit_rather_than_disable_the_memory():
    limits = HistoryConfig.from_config({"monitor": {"price_history_max_points": "lots"}})

    assert limits.max_points == HistoryConfig().max_points


# ------------------------------------------------------------------------ across runs


def test_a_fortnight_of_runs_should_turn_into_a_claim_two_runs_could_not_support(tmp_path):
    """The whole feature, end to end: log a run at a time until a claim is earned."""
    path = tmp_path / "prices.jsonl"
    limits = HistoryConfig(path=path, min_observations=3, min_span_days=7)

    for day, price in enumerate([120.0, 110.0, 95.0]):
        at = NOW - timedelta(days=14 - day * 7)
        run = observe([_product(price=price)], now=at)
        append(run, path)
        history = load_history(path)
        memory = summarise_all([_product(price=price)], history, at, limits)[URL]
        if day < 2:
            assert memory.enough_history is False, f"claimed an opinion after {day + 1} run(s)"

    assert memory.enough_history is True
    assert memory.is_lowest is True
    assert memory.span_days == 14.0

"""Unit tests for price memory — the log that lets dealScout say what a price usually is.

The behaviour under test is as much about *restraint* as about arithmetic: a thin history
must produce no claim at all, and must say so in a field rather than in a zero.
"""

from __future__ import annotations

import json
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dealscout.models import Product
from dealscout import pricehistory
from dealscout.pricehistory import (
    DEFAULT_HISTORY_DIR,
    HistoryConfig,
    Observation,
    _month_of,
    _shard_path,
    append,
    append_dir,
    boot_key,
    extend,
    load_history,
    load_history_dir,
    observe,
    prune,
    rewrite,
    rewrite_dir,
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


# ---------------------------------------------------------------------- boot identity
#
# The Scout's promise is "cheapest this *boot* has been", not "cheapest this listing has
# been". A boot is a different URL at every shop, so keying price memory on the URL can
# never answer the cross-retailer question. These tie an observation to a resolved boot
# identity instead — the change that must be right before months of data accumulate.


def _identify(boot: str, size: str = ""):
    """A stand-in identity resolver: every product maps to one boot key and size."""
    return lambda _product: (boot, size)


def test_observe_should_key_on_the_boot_when_an_identity_is_supplied():
    boot = "adidas/predator//adult-flagship/adult"

    observation = observe([_product()], ("37.33",), now=NOW, identify=_identify(boot, "37.33"))[0]

    assert observation.boot_key == boot
    assert observation.size == "37.33"
    assert observation.key == boot  # the boot, not the URL


def test_observe_should_fall_back_to_the_url_when_the_boot_is_unclassified():
    # An empty boot key is the catalogue's honest "unknown": the observation must keep its
    # own URL rather than merge with every other unclassified boot.
    observation = observe([_product()], ("37.33",), now=NOW, identify=_identify(""))[0]

    assert observation.boot_key == ""
    assert observation.key == URL


def test_observe_should_keep_the_same_boot_at_two_shops_as_two_observations():
    # The cheaper of two shops is the whole point of a cross-retailer low; collapsing them
    # would discard it. One boot, two sources -> two observations.
    boot = "adidas/predator//adult-flagship/adult"
    komanda = _product(url=URL, source="komanda.lv", price=95.0)
    unisport = _product(url="https://unisportstore.com/predator", source="unisportstore.com", price=88.0)

    observations = observe([komanda, unisport], ("37.33",), now=NOW, identify=_identify(boot))

    assert sorted(o.price for o in observations) == [88.0, 95.0]
    assert {o.source for o in observations} == {"komanda.lv", "unisportstore.com"}


def test_the_same_boot_at_two_retailers_should_share_one_price_history(tmp_path):
    """The behaviour bRoom needs, impossible under URL keying.

    Two shops sell one boot: one has logged it for weeks at €120, the other appears today
    at €88. The honest claim is that €88 is the lowest this *boot* has been anywhere — a
    claim only a boot-keyed history can make. Under the old URL keying the fresh listing
    would have no history at all and could say nothing.
    """
    boot = "adidas/predator//adult-flagship/adult"
    path = tmp_path / "prices.jsonl"

    # komanda.lv has three weeks of history at a higher price.
    for day, price in enumerate([120.0, 118.0, 120.0]):
        at = NOW - timedelta(days=14 - day * 7)
        append(observe([_product(url=URL, source="komanda.lv", price=price)], now=at,
                       identify=_identify(boot)), path)

    # unisportstore.com appears today, cheaper, at a URL never seen before.
    fresh = _product(url="https://unisportstore.com/predator", source="unisportstore.com", price=88.0)
    history = extend(load_history(path), observe([fresh], now=NOW, identify=_identify(boot)))

    memory = summarise_all([fresh], history, NOW, identify=_identify(boot))[
        "https://unisportstore.com/predator"
    ]

    assert memory.enough_history is True
    assert memory.low == 88.0
    assert memory.is_lowest is True  # cheapest this boot has been, across both shops


def test_a_boot_keyed_line_should_read_back_from_the_log_with_its_identity(tmp_path):
    boot = "adidas/predator//adult-flagship/adult"
    path = tmp_path / "prices.jsonl"

    append(observe([_product()], ("37.33",), now=NOW, identify=_identify(boot, "37.33")), path)
    [loaded] = load_history(path)[boot]

    assert loaded.boot_key == boot
    assert loaded.size == "37.33"


def test_a_log_written_before_the_identity_fields_existed_should_still_read(tmp_path):
    # Backward compatibility: an old line has no boot_key/size. It must parse and key on
    # its URL exactly as it always did, not vanish.
    path = tmp_path / "prices.jsonl"
    path.write_text(json.dumps({"url": URL, "price": 95.0, "at": NOW.isoformat()}) + "\n",
                    encoding="utf-8")

    history = load_history(path)

    assert [o.price for o in history[URL]] == [95.0]
    assert history[URL][0].boot_key == ""


def test_boot_key_should_return_empty_when_the_tier_is_unknown():
    # No honest identity without a tier — the signal to fall back to the URL.
    assert boot_key({"brand": "adidas", "silo": "predator"}) == ""
    assert boot_key({"brand": "adidas", "tier": "unknown"}) == ""


def test_boot_key_should_separate_a_junior_flagship_from_an_adult_one():
    adult = boot_key({"brand": "nike", "silo": "mercurial", "tier": "adult-flagship"})
    junior = boot_key({"brand": "nike", "silo": "mercurial", "tier": "junior-flagship"})

    assert adult != junior
    # Assert the audience *segment* rather than the end of the string: the key gains
    # fields over time (cut was appended after this test was written), and a test that
    # pins a field to the end of the key fails on a change that did not touch it.
    assert adult.split("/")[5] == "adult"
    assert junior.split("/")[5] == "junior"


# --------------------------------------------------------------------- soleplate
#
# The FG and SG of one boot are different SKUs at different prices and are not substitutes:
# a firm-ground player cannot wear soft-ground studs on turf. Quoting an SG clearance as
# the low for an FG boot is the confident-and-wrong price claim this project exists to
# refuse, so the soleplate is part of the identity — and an *unstated* soleplate is its own
# "unknown" bucket, never a silent merge into a stated one.


def test_boot_key_should_separate_the_firm_ground_boot_from_the_soft_ground_one():
    base = {"brand": "adidas", "silo": "predator", "tier": "adult-flagship"}
    firm = boot_key({**base, "soleplate": "FG"})
    soft = boot_key({**base, "soleplate": "SG"})

    assert firm != soft
    assert firm.split("/")[5] == "adult"  # audience keeps its position
    assert "/fg/" in firm
    assert "/sg/" in soft


def test_an_unstated_soleplate_should_not_merge_into_a_stated_one():
    base = {"brand": "adidas", "silo": "predator", "tier": "adult-flagship"}
    stated = boot_key({**base, "soleplate": "FG"})
    silent = boot_key(base)  # the listing never said

    # The silent listing is honestly "unknown", not quietly folded into the FG history.
    assert stated != silent
    assert "/unknown/" in silent


# --------------------------------------------------------------------- collar cut
#
# Nike sells "Phantom 6 High" and "Phantom 6 Low" as separate boots under one model name,
# on one shelf, £30 apart — the collar is the difference. The Mercurial line makes the same
# split by *name* (Superfly has the collar, Vapor does not) and the catalogue keeps those
# apart as two silos; Phantom says it in a word, and nothing was reading the word. In the
# production log that pooled 64 observations across three keys, with spreads up to €82.


def test_boot_key_should_separate_the_collar_from_the_low_cut():
    base = {
        "brand": "nike",
        "silo": "phantom",
        "generation": "6",
        "tier": "adult-flagship",
        "soleplate": "FG",
    }
    high = boot_key({**base, "cut": "high"})
    low = boot_key({**base, "cut": "low"})

    assert high != low, "a collared boot and a low-cut boot are not one price history"
    # Assert the cut *segment*, not the end of the key. I wrote this as `endswith` and it
    # broke the next time a field was appended — the same brittleness the audience tests
    # above were already fixed for.
    assert high.split("/")[6] == "high"
    assert low.split("/")[6] == "low"


def test_an_unstated_cut_should_not_merge_into_a_stated_one():
    # Same rule as the soleplate, for the same reason: a listing that does not say is its
    # own bucket. Missing a pooling is cheap; quoting the Low's clearance as the High's
    # price is the confident-and-wrong claim this project refuses.
    base = {
        "brand": "nike",
        "silo": "phantom",
        "generation": "6",
        "tier": "adult-flagship",
        "soleplate": "FG",
    }
    stated = boot_key({**base, "cut": "low"})
    silent = boot_key(base)

    assert stated != silent
    assert silent.split("/")[6] == "unknown"


def test_the_cut_should_be_read_from_a_real_retailer_title():
    # End-to-end through the resolver, on titles copied verbatim from teamsport.lv, whose
    # listings name neither the brand nor a separator: "PHANTOM 6 HIGH ELITE FG T". If the
    # vocabulary stops reading the word, these two collapse back into one identity and the
    # test that catches it must be the one that uses the retailer's own wording.
    from dealscout.hunt import product_identity
    from dealscout.models import Hunt

    hunt = Hunt.from_dict(
        {
            "id": "probe",
            "category": "football_boots",
            "brands": ["Nike", "adidas"],
            "brands_only": True,
            "require": {},
            "price": {},
        }
    )

    def key(title: str) -> str:
        product = Product(
            title=title,
            category="football_boots",
            price=299.99,
            reference_price=None,
            currency="EUR",
            url="https://www.teamsport.lv/x",
            source="teamsport.lv",
            brand="",
        )
        return product_identity(product, hunt)[0]

    high = key("PHANTOM 6 HIGH ELITE FG T")
    low = key("PHANTOM 6 LOW ELITE FG")

    assert high and low, "both are readable Nike flagships and must be loggable"
    assert high != low
    assert high.split("/")[6] == "high"
    assert low.split("/")[6] == "low"


# ----------------------------------------------------------------------- closure
#
# adidas sells "Predator Elite Laceless FG" and "Predator Elite FG" as separate boots at
# separate prices under one tier name. #42 correctly strips `laceless`/`ll` as tier *noise*
# (a within-tier SKU variant must never be drawn as a tier trait), which means the two share
# a tier — and without closure in the identity they therefore shared a price history. In the
# production log that pooled 32 keys across adidas Copa/F50/Predator, the laced low standing
# in €55 under the true laceless low on Predator Elite FG (€75 pooled vs €130 laceless).
# Closure joins the key under the soleplate/cut rule: an unstated closure keys as `unknown`
# (the laced default), never folded into a stated one.


def test_boot_key_should_separate_laceless_from_laced():
    base = {
        "brand": "adidas",
        "silo": "predator",
        "tier": "adult-flagship",
        "soleplate": "FG",
    }
    laceless = boot_key({**base, "closure": "laceless"})
    laced = boot_key(base)

    assert laceless != laced, "a laceless boot and a laced boot are not one price history"
    assert laceless.split("/")[7] == "laceless"
    assert laced.split("/")[7] == "unknown"


def test_an_unstated_closure_should_not_merge_into_laceless():
    # The laced default is silent — adidas titles say "laceless" but never "laced" — so an
    # unstated closure must be its own `unknown` bucket, not folded into the stated laceless
    # one. Missing a pooling is cheap; quoting the laced clearance as the laceless low is the
    # confident-and-wrong claim this project refuses.
    base = {
        "brand": "adidas",
        "silo": "f50",
        "tier": "adult-flagship",
        "soleplate": "FG",
    }
    stated = boot_key({**base, "closure": "laceless"})
    silent = boot_key(base)

    assert stated != silent
    # The closure segment specifically, not the end of the key — a trailing `unknown` would
    # also be satisfied by the rung field, so `endswith` here passed for the wrong reason.
    assert silent.split("/")[7] == "unknown"


def test_closure_should_be_read_from_real_retailer_titles():
    # End-to-end through the resolver, on adidas titles as feeds and slugs actually carry
    # them — the full word "laceless" and adidas' own "ll" shorthand, neither naming the
    # brand. If the vocabulary stops reading these, the pair collapses to one identity and
    # the laceless low is pooled away.
    from dealscout.hunt import product_identity
    from dealscout.models import Hunt

    hunt = Hunt.from_dict(
        {
            "id": "probe",
            "category": "football_boots",
            "brands": ["Nike", "adidas"],
            "brands_only": True,
            "require": {},
            "price": {},
        }
    )

    def key(title: str) -> str:
        product = Product(
            title=title,
            category="football_boots",
            price=130.00,
            reference_price=None,
            currency="EUR",
            url="https://www.teamsport.lv/x",
            source="teamsport.lv",
            brand="",
        )
        return product_identity(product, hunt)[0]

    laced = key("adidas predator elite fg")
    laceless_word = key("adidas predator elite laceless fg")
    laceless_ll = key("adidas f50 elite ll fg")
    laced_f50 = key("adidas f50 elite fg")

    assert laced and laceless_word, "both are readable adidas flagships and must be loggable"
    assert laced != laceless_word
    assert laceless_word.split("/")[7] == "laceless"
    assert laced.split("/")[7] == "unknown"
    assert laceless_ll != laced_f50, "adidas' own 'll' shorthand must read as laceless"
    assert laceless_ll.split("/")[7] == "laceless"


def test_closure_should_not_be_read_from_an_ordinary_word():
    # The boundary-safe matcher is what makes the bare "ll" shorthand usable: without it
    # every "yellow" and "elite ll"-lookalike would name a laceless boot. "ll" must match
    # only as its own token, never inside another word.
    from dealscout.spec import extract_attrs

    for title in (
        "adidas Predator Elite FG Yellow",  # 'll' inside 'yellow'
        "adidas F50 Elite FG Solar Yellow Full Kit",  # 'll' inside 'full'
    ):
        assert "closure" not in extract_attrs(title, "football_boots"), title



    # The counterpart, and the reason this is a narrow change rather than "put every word
    # in the key". teamsport.lv appends colourway codes — "... FG T", "... FG NU3",
    # "... FG LV8" — to one boot. Those ARE the same boot and must keep sharing a history,
    # or "cheapest seen" stops meaning anything. Only the collar splits.
    from dealscout.hunt import product_identity
    from dealscout.models import Hunt

    hunt = Hunt.from_dict(
        {"id": "probe", "category": "football_boots", "brands": ["Nike"], "require": {}}
    )

    def key(title: str) -> str:
        product = Product(
            title=title,
            category="football_boots",
            price=329.99,
            reference_price=None,
            currency="EUR",
            url="https://www.teamsport.lv/x",
            source="teamsport.lv",
            brand="",
        )
        return product_identity(product, hunt)[0]

    assert key("VAPOR 17 ELITE FG T") == key("VAPOR 17 ELITE FG")
    assert key("VAPOR 17 ELITE FG NU3") == key("VAPOR 17 ELITE FG")


def test_two_shops_of_one_boot_with_the_same_soleplate_still_share_history(tmp_path):
    # The pooling we *do* want: same boot, same soleplate, two shops -> one history, so the
    # cheaper shop's price is recognised as the boot's low.
    boot = boot_key(
        {"brand": "adidas", "silo": "predator", "tier": "adult-flagship", "soleplate": "FG"}
    )
    path = tmp_path / "prices.jsonl"
    for day, price in enumerate([120.0, 118.0, 120.0]):
        at = NOW - timedelta(days=14 - day * 7)
        append(observe([_product(url=URL, source="komanda.lv", price=price)], now=at,
                       identify=_identify(boot)), path)
    fresh = _product(url="https://unisportstore.com/p", source="unisportstore.com", price=88.0)
    history = extend(load_history(path), observe([fresh], now=NOW, identify=_identify(boot)))

    memory = summarise_all([fresh], history, NOW, identify=_identify(boot))[
        "https://unisportstore.com/p"
    ]

    assert memory.enough_history is True
    assert memory.is_lowest is True


# ----------------------------------------------------------- surviving the store fix
#
# Step 2 moves the log to durable storage and, in doing so, restarts it near-empty. Two
# things must hold across that transition: a log written in the *old* schema must still
# load and answer sanely, and a freshly-restarted log must keep saying "not enough history
# yet" rather than reporting the first three days as a meaningful low.


def test_an_old_schema_fixture_should_load_and_answer_sanely(tmp_path):
    """A real old-schema log — lines with no boot_key/size — must still produce a memory.

    This is the exact shape ``prices.jsonl`` had before boot identity existed. It must key
    on its URL as it always did and, with enough spread, still answer the price question,
    so the store migration never silently discards the depth already collected.
    """
    path = tmp_path / "prices.jsonl"
    lines = [
        {"url": URL, "price": 120.0, "at": (NOW - timedelta(days=20)).isoformat()},
        {"url": URL, "price": 110.0, "at": (NOW - timedelta(days=10)).isoformat()},
        {"url": URL, "price": 118.0, "at": NOW.isoformat()},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    history = load_history(path)
    # No identity resolver: an old caller keys on the URL, exactly as before.
    memory = summarise_all([_product()], history, NOW)[URL]

    assert [o.boot_key for o in history[URL]] == ["", "", ""]  # genuinely old lines
    assert memory.enough_history is True
    assert memory.low == 110.0


def test_a_freshly_restarted_log_should_still_say_not_enough_history(tmp_path):
    """After the store fix the log restarts near-empty; it must not over-claim.

    Three observations over three days is below the span floor. The honest answer is
    ``enough_history=False`` with no low — a three-day dip is not "the cheapest it has
    been", and saying so is exactly the confident-and-wrong claim the module refuses.
    """
    boot = boot_key(
        {"brand": "adidas", "silo": "predator", "tier": "adult-flagship", "soleplate": "FG"}
    )
    path = tmp_path / "prices.jsonl"
    for day, price in enumerate([120.0, 100.0, 110.0]):  # a tempting three-day low
        at = NOW - timedelta(days=2 - day)
        append(observe([_product(price=price)], now=at, identify=_identify(boot)), path)

    history = load_history(path)
    memory = summarise_all([_product()], history, NOW, identify=_identify(boot))[URL]

    assert memory.enough_history is False
    assert memory.low is None
    assert memory.is_lowest is None


# --------------------------------------------------------- durable monthly store

def _obs(price: float, at: datetime, url: str = URL) -> Observation:
    return Observation(url=url, price=price, at=at)


def test_append_dir_writes_a_monthly_shard(tmp_path):
    # An observation is filed under the YYYY-MM.jsonl of its own timestamp, so a shard
    # never grows without bound and the site can read one month at a time.
    directory = tmp_path / "prices"
    append_dir([_obs(95.0, NOW)], directory)

    assert (directory / "2026-08.jsonl").exists()
    assert [o.price for o in load_history_dir(directory, legacy_path=None)[URL]] == [95.0]


def test_observations_in_different_months_land_in_different_shards(tmp_path):
    # Two runs a month apart write two files; a load reads them back as one merged series.
    directory = tmp_path / "prices"
    july = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    append_dir([_obs(110.0, july)], directory)
    append_dir([_obs(95.0, NOW)], directory)

    assert (directory / "2026-07.jsonl").exists()
    assert (directory / "2026-08.jsonl").exists()
    assert [o.price for o in load_history_dir(directory, legacy_path=None)[URL]] == [110.0, 95.0]


def test_load_history_dir_merges_the_legacy_flat_file(tmp_path):
    # The migration must not throw away what the old cache holds: whatever is in the legacy
    # state/prices.jsonl is the only history that exists, so it is read alongside the shards
    # rather than replaced by them.
    directory = tmp_path / "prices"
    legacy = tmp_path / "prices.jsonl"
    legacy.write_text(_obs(120.0, NOW - timedelta(days=40)).to_json() + "\n", encoding="utf-8")
    append_dir([_obs(95.0, NOW)], directory)

    merged = load_history_dir(directory, legacy_path=legacy)[URL]

    assert [o.price for o in merged] == [120.0, 95.0]


def test_legacy_observations_survive_a_rewrite_round_trip(tmp_path):
    # After migration the legacy file is re-sharded into monthly files; the old observations
    # must still be present, now living in the shard for their own month.
    directory = tmp_path / "prices"
    legacy = tmp_path / "prices.jsonl"
    old = NOW - timedelta(days=40)  # July 2026
    legacy.write_text(_obs(120.0, old).to_json() + "\n", encoding="utf-8")
    append_dir([_obs(95.0, NOW)], directory)

    merged = load_history_dir(directory, legacy_path=legacy)
    rewrite_dir(merged, directory)

    reloaded = load_history_dir(directory, legacy_path=None)[URL]
    assert [o.price for o in reloaded] == [120.0, 95.0]
    assert (directory / "2026-07.jsonl").exists()


def test_duplicate_observation_in_legacy_and_shard_collapses(tmp_path):
    # If a run migrates the legacy file into a shard and the legacy file is read again, the
    # same observation must not be counted twice, or the history would look denser than it is.
    directory = tmp_path / "prices"
    legacy = tmp_path / "prices.jsonl"
    same = _obs(120.0, NOW - timedelta(days=40))
    legacy.write_text(same.to_json() + "\n", encoding="utf-8")
    append_dir([same], directory)  # same observation now in both places

    merged = load_history_dir(directory, legacy_path=legacy)[URL]

    assert [o.price for o in merged] == [120.0]


def test_rewrite_dir_drops_a_shard_whose_month_aged_out(tmp_path):
    # Pruning past keep_days must actually remove the file, not just the in-memory series, or
    # the store would grow forever on disk while claiming to be bounded.
    directory = tmp_path / "prices"
    old = NOW - timedelta(days=400)
    append_dir([_obs(120.0, old)], directory)
    append_dir([_obs(95.0, NOW)], directory)
    assert _shard_path(directory, _month_of(old)).exists()

    pruned = prune(load_history_dir(directory, legacy_path=None), keep_days=180, max_points=200, now=NOW)
    rewrite_dir(pruned, directory)

    assert not _shard_path(directory, _month_of(old)).exists()
    assert [o.price for o in load_history_dir(directory, legacy_path=None)[URL]] == [95.0]


def test_both_workflows_resolve_to_one_log(monkeypatch):
    # The split-brain bug: hunt.yml and shortlist.yml cached state under different keys, so
    # their observations never merged. Both now read DEALSCOUT_PRICE_HISTORY_DIR, so both
    # HistoryConfig instances must point at the exact same directory — proof of one log.
    monkeypatch.setenv("DEALSCOUT_PRICE_HISTORY_DIR", "price-history/prices")

    hunt_cfg = HistoryConfig.from_config({})
    shortlist_cfg = HistoryConfig.from_config({})

    assert hunt_cfg.dir == shortlist_cfg.dir == Path("price-history/prices")


def test_env_overrides_configured_history_dir(monkeypatch):
    # Env is the deployment's fact about where the log physically lives and must win over the
    # user's configured preference.
    monkeypatch.setenv("DEALSCOUT_PRICE_HISTORY_DIR", "price-history/prices")

    cfg = HistoryConfig.from_config({"monitor": {"price_history_dir": "state/prices"}})

    assert cfg.dir == Path("price-history/prices")


def test_history_dir_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("DEALSCOUT_PRICE_HISTORY_DIR", raising=False)

    cfg = HistoryConfig.from_config({})

    assert cfg.dir == DEFAULT_HISTORY_DIR


def test_load_history_dir_defaults_to_reading_the_legacy_file():
    """The migration must be wired by DEFAULT, not only when a caller asks for it.

    `test_load_history_dir_merges_the_legacy_flat_file` passes `legacy_path` explicitly,
    which is correct for a unit test but leaves the production wiring uncovered: if the
    default were ever changed to None, the migration would silently stop and every
    existing test would still pass.

    That regression is unrecoverable. The legacy flat file is the only price history that
    exists, and a price log cannot be backfilled -- a day not observed is gone. So the
    default is asserted directly, by introspection, rather than through behaviour
    (Python binds defaults at definition time, so monkeypatching the module attribute
    afterwards cannot reach it).
    """
    default = inspect.signature(load_history_dir).parameters["legacy_path"].default

    assert default is not None, (
        "load_history_dir's `legacy_path` default is None, so the workflows -- which "
        "call it with no such argument -- would skip the one-time migration and drop "
        "the entire history the two caches hold."
    )
    assert default == pricehistory.DEFAULT_HISTORY_PATH, (
        f"`legacy_path` defaults to {default!r}, not DEFAULT_HISTORY_PATH. The migration "
        "would read somewhere other than where the legacy log actually lives."
    )


def test_the_cut_should_not_be_read_from_an_ordinary_word():
    # The boundary-safe matcher is what makes bare "high"/"low" usable at all: without it
    # every "below", "highlight" and "yellow" would name a collar.
    from dealscout.spec import extract_attrs

    for title in (
        "adidas Predator Elite FG Yellow",  # 'low' inside 'yellow'
        "Nike Mercurial Vapor 16 Elite Highlight Pack FG",  # 'high' inside 'highlight'
        "adidas Copa Pure 4 Elite FG Below Zero",  # 'low' inside 'below'
    ):
        assert "cut" not in extract_attrs(title, "football_boots"), title


def test_a_high_visibility_colourway_is_a_known_false_positive():
    # Pinned deliberately, as a decision rather than an oversight. "green high vis yellow"
    # reads as a collar; measured on 1,987 real slugs it is 1 wrong read against 231 right
    # ones, on an Under Armour boot no Nike/adidas hunt keeps. It fails in the safe
    # direction — the boot is split from its own history rather than pooled with another —
    # and excluding it would mean teaching a category-neutral matcher about football
    # colourways. If this test ever needs to change, that is the trade being revisited.
    from dealscout.spec import extract_attrs

    title = "Under Armour Shadow Elite 4.0 Mach FG Arden Green High Vis Yellow"
    assert extract_attrs(title, "football_boots").get("cut") == "high"


# --------------------------------------------------------------------- takedown rung
#
# `takedown` is the right answer for the JUDGE: its question is "flagship or not", and the
# rung does not change the buy signal. It is the wrong answer for an IDENTITY. Pro, League,
# Club and Academy are different boots with different plates, uppers and prices, and the
# production log pooled a Superfly 11 Club at EUR 60 with a Superfly 11 Pro at EUR 180 --
# 371 observations across 36 keys, spreads reaching EUR 223. Quoting the Club as the low for
# the Pro is the same dishonesty the soleplate rule was written to refuse.


def test_boot_key_should_separate_the_rungs_of_the_takedown_ladder():
    base = {
        "brand": "nike",
        "silo": "mercurial superfly",
        "generation": "11",
        "tier": "takedown",
        "soleplate": "FG",
    }
    pro = boot_key({**base, "rung": "pro"})
    club = boot_key({**base, "rung": "club"})

    assert pro != club, "a Pro and a Club are not one price history"
    assert pro.split("/")[8] == "pro"
    assert club.split("/")[8] == "club"


def test_a_flagship_should_be_unaffected_by_the_rung():
    # A flagship has no rung, so every flagship keys as `unknown` and flagship pooling is
    # unchanged. Worth pinning: this field exists to split takedowns, and if it ever split
    # flagships as well it would be quietly halving the history the Scout depends on.
    flagship = {
        "brand": "adidas",
        "silo": "predator",
        "generation": "26",
        "tier": "adult-flagship",
        "soleplate": "FG",
    }
    assert boot_key(flagship).split("/")[8] == "unknown"
    assert boot_key(flagship) == boot_key({**flagship, "rung": ""})


def test_the_rung_should_be_read_from_a_real_retailer_title():
    # End to end through the resolver, on titles as voetbalshop.nl writes them. These two
    # shared a key in production and are EUR 47 apart.
    from dealscout.hunt import product_identity
    from dealscout.models import Hunt

    hunt = Hunt.from_dict(
        {"id": "probe", "category": "football_boots", "brands": ["Nike", "adidas"], "require": {}}
    )

    def key(title: str, price: float) -> str:
        product = Product(
            title=title,
            category="football_boots",
            price=price,
            reference_price=None,
            currency="EUR",
            url="https://example.test/x",
            source="voetbalshop.nl",
            brand="",
        )
        return product_identity(product, hunt)[0]

    league = key("adidas Copa Pure IV League Gras Football Boots (FG)", 56.99)
    pro = key("adidas Copa Pure IV Pro Gras Football Boots (FG)", 104.99)

    assert league and pro
    assert league != pro
    assert league.split("/")[8] == "league"
    assert pro.split("/")[8] == "pro"


def test_a_soft_ground_flagship_is_not_read_as_the_pro_rung():
    # The trap the catalogue is already arranged to avoid, now load-bearing for a second
    # reason. Nike's soft-ground soleplate is literally named "SG-Pro", so a naive read of
    # the word "pro" would give every soft-ground flagship a Pro rung -- splitting it from
    # its own firm-ground-priced history and mislabelling the boot. The rung is read from
    # the title AFTER soleplate suffixes are stripped, which is why this holds.
    from dealscout.catalogue import classify

    c = classify("Nike Mercurial Superfly 10 Elite SG-Pro", category="football_boots")
    assert c.tier == "adult-flagship"
    assert c.rung == ""

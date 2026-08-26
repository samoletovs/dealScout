"""Unit tests for per-source yield drift detection (pure — no filesystem, no network)."""

from __future__ import annotations

import json

from dealscout.yields import (
    DEFAULT_BASELINE_RUNS,
    Drop,
    drops,
    load,
    record,
    save,
)

LABELS = {"sportland.lv": "Sportland (Rīga)", "prodirectsport.ie": "Pro:Direct (IE)"}


def test_should_notice_a_source_that_collapsed_to_zero():
    # The case this module exists for. When a tier-label change made the pre-filter
    # discard every candidate, Sportland went 35 -> 0 and the only signal was a
    # broken-reader alarm that named the wrong cause.
    history = {"sportland.lv": [35, 37, 35]}
    [drop] = drops(history, {"sportland.lv": 0}, LABELS)
    assert drop.now == 0
    assert drop.baseline == 35
    assert "Sportland (Rīga) returned nothing" in drop.describe()


def test_should_notice_a_halving_before_it_reaches_zero():
    # The whole point: a source thins out long before it disappears, and that earlier
    # signal is the one worth having.
    history = {"sportland.lv": [35, 37, 35]}
    [drop] = drops(history, {"sportland.lv": 12}, LABELS)
    assert drop.now == 12
    assert "returned 12, usually about 35" in drop.describe()


def test_should_stay_quiet_about_ordinary_movement():
    history = {"sportland.lv": [35, 37, 35]}
    assert drops(history, {"sportland.lv": 30}, LABELS) == []


def test_should_never_report_a_rise():
    # A source that doubles is not a fault. Treating every change as noteworthy is how a
    # signal becomes noise.
    history = {"sportland.lv": [35, 37, 35]}
    assert drops(history, {"sportland.lv": 90}, LABELS) == []


def test_should_refuse_to_judge_on_a_single_observation():
    # A first run has no baseline, and "0 where we have never seen anything else" is not
    # evidence of a fall.
    assert drops({"sportland.lv": [35]}, {"sportland.lv": 0}, LABELS) == []
    assert drops({}, {"sportland.lv": 0}, LABELS) == []


def test_should_ignore_a_source_that_was_always_tiny():
    # komanda.lv offers two qualifying boots. A fall from 2 to 0 is stock, not breakage,
    # and alarming about it every other week would train the reader to ignore the line.
    assert drops({"komanda.lv": [2, 2, 2]}, {"komanda.lv": 0}) == []


def test_should_use_a_median_so_one_quiet_week_does_not_move_the_baseline():
    # A mean would be dragged down by the single 4 and then fail to notice the collapse.
    history = {"sportland.lv": [35, 4, 36, 35]}
    [drop] = drops(history, {"sportland.lv": 3}, LABELS)
    assert drop.baseline == 35


def test_should_report_the_worst_faller_first():
    history = {"a": [40, 40, 40], "b": [40, 40, 40]}
    found = drops(history, {"a": 20, "b": 0})
    assert [d.source for d in found] == ["b", "a"]


def test_record_should_keep_only_the_recent_window():
    history = {"a": list(range(20))}
    updated = record(history, {"a": 99})
    assert updated["a"][-1] == 99
    assert len(updated["a"]) == DEFAULT_BASELINE_RUNS + 1


def test_record_should_start_a_history_for_a_source_never_seen_before():
    assert record({}, {"new.shop": 7})["new.shop"] == [7]


def test_record_should_not_mutate_the_history_it_was_given():
    history = {"a": [1, 2]}
    record(history, {"a": 3})
    assert history == {"a": [1, 2]}


def test_load_should_treat_a_missing_or_broken_file_as_no_history(tmp_path):
    # No history is "no opinion", never a crash — a corrupt file must not stop the run.
    assert load(tmp_path / "absent.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load(broken) == {}


def test_save_then_load_should_round_trip(tmp_path):
    path = tmp_path / "state" / "yields.json"
    save({"sportland.lv": [35, 36]}, path)
    assert load(path) == {"sportland.lv": [35, 36]}
    # The stamp is written for a human reading the file, and must not come back as a source.
    assert "updated" in json.loads(path.read_text(encoding="utf-8"))


def test_drop_share_should_be_safe_when_the_baseline_is_zero():
    assert Drop(source="a", label="a", now=0, baseline=0).share == 1.0

"""Unit tests for the deal feedback loop (dealscout.feedback)."""

from __future__ import annotations

import pytest

from dealscout.feedback import (
    DOWN,
    UP,
    collect_feedback,
    feedback_link,
    feedback_text,
    latest_by_url,
    parse_feedback,
    parse_feedback_jsonl,
    summarize_feedback,
)
from dealscout.models import Feedback

BASE = "https://courier.example.com/api/feedback"
URL = "https://shop.example.com/boss-wool-crew"


def test_feedback_link_encodes_verdict_project_and_url():
    link = feedback_link(BASE, URL, UP)

    assert link.startswith(f"{BASE}?")
    assert "p=dealscout" in link and "v=up" in link
    assert "u=https%3A%2F%2F" in link  # product url is percent-encoded


def test_feedback_link_rejects_bad_verdict():
    with pytest.raises(ValueError, match="verdict"):
        feedback_link(BASE, URL, "maybe")


def test_feedback_text_empty_without_base_url():
    assert feedback_text("", URL) == ""


def test_feedback_text_includes_both_choices():
    line = feedback_text(BASE, URL)

    assert "👍 keep" in line and "👎 skip" in line
    assert line.count(BASE) == 2


def test_feedback_text_uses_markdown_https_links():
    line = feedback_text(BASE, URL)

    assert line.startswith("rate: [👍 keep](https://")
    assert "[👎 skip](https://" in line


def test_parse_feedback_reads_verdict_from_subject_and_url_from_body():
    fb = parse_feedback("Re: dealScout feedback: down", f"down {URL}\n\nnot my style")

    assert fb == Feedback(url=URL, verdict=DOWN)


def test_parse_feedback_ties_url_to_the_verdict_token():
    # A quoted reply may contain other URLs; the one after the token wins.
    body = f"up {URL}\n\nOn Mon, dealScout wrote:\n> see https://tracker.example.com/x"

    fb = parse_feedback("dealScout feedback: up", body)

    assert fb.verdict == UP
    assert fb.url == URL


def test_parse_feedback_falls_back_to_emoji():
    fb = parse_feedback("Re: dealScout feedback", f"👎 {URL}")

    assert fb is not None
    assert fb.verdict == DOWN


def test_parse_feedback_ignores_non_feedback_mail():
    assert parse_feedback("Summer Sale 50% off", "great deals inside") is None


def test_collect_feedback_skips_unparseable_messages():
    messages = [
        ("me@x.com", "dealScout feedback: up", f"up {URL}"),
        ("brand@shop.com", "Newsletter", "buy now"),  # not feedback -> dropped
        ("me@x.com", "Re: dealScout feedback: down", f"down {URL}2"),
    ]

    entries = collect_feedback(messages)

    assert [e.verdict for e in entries] == [UP, DOWN]


def test_summarize_feedback_empty_state():
    assert "No ratings yet" in summarize_feedback([])


def test_summarize_feedback_counts_and_lists_skips():
    entries = [
        Feedback(url=URL, verdict=UP),
        Feedback(url=f"{URL}-2", verdict=DOWN),
    ]

    out = summarize_feedback(entries)

    assert "1 kept · 1 skipped" in out
    assert f"👎 {URL}-2" in out


def test_parse_feedback_jsonl_reads_rows_and_skips_junk():
    text = (
        f'{{"project":"dealscout","verdict":"up","url":"{URL}","ts":"2026-07-13T10:00:00+00:00"}}\n'
        "not json\n"
        f'{{"verdict":"down","url":"{URL}-2","ts":"2026-07-13T11:00:00+00:00"}}\n'
        '{"verdict":"maybe","url":"x"}\n'  # unknown verdict -> dropped
    )

    entries = parse_feedback_jsonl(text)

    assert [e.verdict for e in entries] == [UP, DOWN]
    assert entries[0].url == URL


def test_latest_by_url_keeps_most_recent_vote():
    entries = [
        Feedback(url=URL, verdict=UP, when="2026-07-13T10:00:00+00:00"),
        Feedback(url=URL, verdict=DOWN, when="2026-07-13T12:00:00+00:00"),  # changed mind
    ]

    latest = latest_by_url(entries)

    assert len(latest) == 1
    assert latest[0].verdict == DOWN

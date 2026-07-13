"""Unit tests for the deal feedback loop (dealscout.feedback)."""

from __future__ import annotations

import pytest

from dealscout.feedback import (
    DOWN,
    UP,
    collect_feedback,
    feedback_mailto,
    feedback_text,
    parse_feedback,
    summarize_feedback,
)
from dealscout.models import Feedback

ADDR = "deals@example.com"
URL = "https://shop.example.com/boss-wool-crew"


def test_feedback_mailto_encodes_verdict_and_url():
    link = feedback_mailto(ADDR, URL, UP)

    assert link.startswith(f"mailto:{ADDR}?subject=")
    assert "dealScout%20feedback%3A%20up" in link
    assert "up%20https%3A" in link  # verdict token + url in the body


def test_feedback_mailto_rejects_bad_verdict():
    with pytest.raises(ValueError, match="verdict"):
        feedback_mailto(ADDR, URL, "maybe")


def test_feedback_text_empty_without_address():
    assert feedback_text("", URL) == ""


def test_feedback_text_includes_both_choices():
    line = feedback_text(ADDR, URL)

    assert "👍 keep" in line and "👎 skip" in line
    assert line.count("mailto:") == 2


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

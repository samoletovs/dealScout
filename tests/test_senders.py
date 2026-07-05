"""Unit tests for the sender (subscription-health) summary."""

from __future__ import annotations

from dealscout.senders import sender_domain, summarize_senders


def test_sender_domain_extracts_domain():
    assert sender_domain("BOSS <news@hugoboss.com>") == "hugoboss.com"


def test_sender_domain_handles_bare_address():
    assert sender_domain("news@cos.com") == "cos.com"


def test_summarize_counts_by_domain_desc():
    messages = [
        ("A <x@cos.com>", "s", ""),
        ("B <y@cos.com>", "s", ""),
        ("C <z@gant.com>", "s", ""),
    ]
    assert summarize_senders(messages) == [("cos.com", 2), ("gant.com", 1)]

"""Unit tests for the inbox email parser (no live IMAP)."""

from __future__ import annotations

from dealscout import inbox
from dealscout.inbox import extract_parts

RAW = (
    b"From: BOSS <news@hugoboss.com>\r\n"
    b"Subject: Summer Sale up to 50%\r\n"
    b'Content-Type: text/html; charset="utf-8"\r\n'
    b"\r\n"
    b"<html><body>Up to 50% off shirts</body></html>"
)


def test_extract_parts_reads_sender_subject_and_html():
    sender, subject, html = extract_parts(RAW)
    assert sender == "BOSS <news@hugoboss.com>"
    assert subject == "Summer Sale up to 50%"
    assert "50% off" in html


def test_health_reports_not_configured_without_credentials(monkeypatch):
    monkeypatch.delenv("DEALSCOUT_IMAP_USER", raising=False)
    monkeypatch.delenv("DEALSCOUT_IMAP_PASS", raising=False)
    assert inbox.health() == "not_configured"

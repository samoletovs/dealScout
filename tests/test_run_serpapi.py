"""The weekly job must distinguish a paused source from an empty successful scan."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from dealscout import run_serpapi
from dealscout.discovery import DiscoveryError


def test_exhausted_quota_publishes_paused_status_without_scan_or_email(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_serpapi, "load_config", lambda path: {})
    monkeypatch.setattr(
        run_serpapi.Discovery, "refresh",
        AsyncMock(return_value="Paused: 0 credits remain; renews 2026-09-30."),
    )
    scan = AsyncMock(side_effect=AssertionError("paused discovery must not run the scan"))
    send = AsyncMock()
    monkeypatch.setattr(run_serpapi, "scan", scan)
    monkeypatch.setattr(run_serpapi, "send_email", send)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert asyncio.run(run_serpapi.run(Path("config.yaml"))) == []

    assert "Paused:" in summary.read_text(encoding="utf-8")
    assert "nothing on sale" not in summary.read_text(encoding="utf-8")
    scan.assert_not_called()
    send.assert_not_called()


def test_cached_weekly_rerun_does_not_send_the_same_shopping_email(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_serpapi, "load_config", lambda path: {})
    monkeypatch.setattr(
        run_serpapi.Discovery, "refresh",
        AsyncMock(return_value="Cached: this week's discovery is already available; 0 searches."),
    )
    send = AsyncMock()
    monkeypatch.setattr(run_serpapi, "send_email", send)

    asyncio.run(run_serpapi.run(Path("config.yaml")))

    send.assert_not_called()


def test_search_failure_is_a_failed_cli_run_with_explicit_status(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_serpapi, "load_config", lambda path: {})
    monkeypatch.setattr(
        run_serpapi.Discovery, "refresh",
        AsyncMock(side_effect=DiscoveryError("SerpApi HTTP 429; discovery stopped")),
    )

    assert run_serpapi.main(["--no-email"]) == 1

    assert "Failed: SerpApi HTTP 429" in Path("out/discovery-status.md").read_text(encoding="utf-8")

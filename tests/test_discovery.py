"""Paid discovery must conserve credits across failures, reruns, and consumers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import aiohttp
import pytest

from dealscout import discovery
from dealscout.discovery import Discovery, DiscoveryError

NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)
RENEWAL = "2026-10-01"
API_KEY = "synthetic-key-never-send"
QUERY = "synthetic query never disclose"
SEARCH_ID = "a" * 32
ITEM = {
    "title": "Example running trainers",
    "extracted_price": 49.0,
    "product_link": "https://shop.example/trainers",
    "source": "Example shop",
}


@pytest.fixture(autouse=True)
def no_real_provider_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEALSCOUT_DISCOVERY_PATH", raising=False)
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.setattr(
        discovery.aiohttp,
        "ClientSession",
        MagicMock(side_effect=AssertionError("Tests must not contact the provider")),
    )


@pytest.fixture
def state_path() -> Iterator[Path]:
    # Keep test artifacts inside this project, including when a child process runs.
    directory = Path(__file__).resolve().parent / f".discovery-state-{uuid4().hex}"
    directory.mkdir()
    try:
        yield directory / "state.json"
    finally:
        shutil.rmtree(directory)


def _config(path: Path, queries: tuple[str, ...] = (QUERY,), **settings: object) -> dict:
    return {
        "serpapi": {
            "enabled": True,
            "state_path": str(path),
            "country": "de",
            "queries": [{"q": query, "category": "footwear"} for query in queries],
            **settings,
        }
    }


def _providers(
    monkeypatch: pytest.MonkeyPatch,
    remaining: int = 1_000,
    renewal: str = RENEWAL,
) -> tuple[AsyncMock, AsyncMock]:
    account = AsyncMock(return_value=(remaining, renewal))
    search = AsyncMock(return_value=[ITEM])
    monkeypatch.setattr(discovery, "account_snapshot", account)
    monkeypatch.setattr(discovery, "search", search)
    return account, search


def _state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _http_response(
    monkeypatch: pytest.MonkeyPatch, status: int, payload: object
) -> tuple[MagicMock, MagicMock]:
    response = MagicMock(status=status)
    response.json = AsyncMock(return_value=payload)
    session = MagicMock()
    session.__aenter__.return_value = session
    session.get.return_value.__aenter__.return_value = response
    monkeypatch.setattr(discovery.aiohttp, "ClientSession", MagicMock(return_value=session))
    return session, response


@pytest.mark.parametrize("remaining", [0, 1, 49, 50])
def test_should_not_dispatch_a_search_at_or_below_the_account_reserve(
    state_path: Path, monkeypatch: pytest.MonkeyPatch, remaining: int
) -> None:
    _, search = _providers(monkeypatch, remaining=remaining)

    status = asyncio.run(Discovery(_config(state_path), now=NOW).refresh(API_KEY))

    search.assert_not_awaited()
    assert _state(state_path)["attempts"] == 0
    assert "Paused" in status


def test_should_recheck_account_quota_before_spending_the_next_credit(
    state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account, search = _providers(monkeypatch)
    account.side_effect = [(51, RENEWAL), (50, RENEWAL)]

    asyncio.run(Discovery(_config(state_path, ("first", "second")), now=NOW).refresh(API_KEY))

    search.assert_awaited_once_with("first", API_KEY, "de")
    assert _state(state_path)["attempts"] == 1


def test_should_disable_cache_reads_and_refresh_with_the_same_flag(
    state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account, search = _providers(monkeypatch)
    config = _config(state_path)
    asyncio.run(Discovery(config, now=NOW).refresh(API_KEY))
    assert Discovery(config, now=NOW).cached(QUERY) == [ITEM]
    before = state_path.read_bytes()
    account.reset_mock()
    search.reset_mock()
    config["serpapi"]["enabled"] = False

    assert Discovery(config, now=NOW).cached(QUERY) == []
    assert "Disabled" in asyncio.run(Discovery(config, now=NOW).refresh(API_KEY))

    account.assert_not_awaited()
    search.assert_not_awaited()
    assert state_path.read_bytes() == before


def test_disabled_discovery_should_not_read_even_a_broken_cache(
    state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account, search = _providers(monkeypatch)
    state_path.write_text("not json", encoding="utf-8")
    store = Discovery(_config(state_path, enabled=False), now=NOW)

    assert store.cached(QUERY) == []
    assert "Disabled" in asyncio.run(store.refresh(API_KEY))

    account.assert_not_awaited()
    search.assert_not_awaited()


def test_should_limit_one_refresh_to_twelve_search_attempts(
    state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, search = _providers(monkeypatch)
    config = _config(state_path, tuple(f"query-{index}" for index in range(20)))

    asyncio.run(Discovery(config, now=NOW).refresh(API_KEY))

    assert search.await_count == 12
    assert _state(state_path)["attempts"] == 12


def test_should_limit_a_billing_cycle_to_sixty_attempts_across_new_objects(
    state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, search = _providers(monkeypatch)
    config = _config(state_path, tuple(f"query-{index}" for index in range(80)))

    for day in range(6):
        before = search.await_count
        asyncio.run(Discovery(config, now=NOW + timedelta(days=day)).refresh(API_KEY))
        assert search.await_count - before <= 12

    assert search.await_count == 60
    assert _state(state_path)["attempts"] == 60


def test_should_persist_an_attempt_before_dispatch_and_retain_it_after_failure(
    state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, search = _providers(monkeypatch)
    config = _config(state_path, cycle_budget=1)

    async def fail_after_reservation(*args: object) -> list[dict]:
        assert _state(state_path)["attempts"] == 1
        raise DiscoveryError("SerpApi request failed; discovery stopped")

    search.side_effect = fail_after_reservation
    with pytest.raises(DiscoveryError):
        asyncio.run(Discovery(config, now=NOW).refresh(API_KEY))

    asyncio.run(Discovery(config, now=NOW + timedelta(hours=1)).refresh(API_KEY))

    assert search.await_count == 1
    assert _state(state_path)["attempts"] == 1
    assert _state(state_path)["entries"] == {}


def test_should_honor_a_failed_attempt_after_restarting_python(
    state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, search = _providers(monkeypatch)
    config = _config(state_path, cycle_budget=1)
    search.side_effect = DiscoveryError("SerpApi request failed; discovery stopped")
    with pytest.raises(DiscoveryError):
        asyncio.run(Discovery(config, now=NOW).refresh(API_KEY))

    code = """
import asyncio
import json
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from dealscout import discovery

discovery.aiohttp.ClientSession = MagicMock(side_effect=AssertionError("Network forbidden"))
discovery.account_snapshot = AsyncMock(return_value=(1000, "2026-10-01"))
discovery.search = AsyncMock(side_effect=AssertionError("Persisted budget must prevent search"))
config = json.loads(sys.argv[1])
store = discovery.Discovery(config, now=datetime.fromisoformat(sys.argv[2]))
asyncio.run(store.refresh("synthetic-key-never-send"))
discovery.search.assert_not_awaited()
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, json.dumps(config), NOW.isoformat()],
        cwd=Path(__file__).resolve().parent.parent,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _state(state_path)["attempts"] == 1


def test_a_new_week_should_not_reset_the_same_billing_cycle_budget(
    state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, search = _providers(monkeypatch)
    config = _config(state_path, cycle_budget=1)
    asyncio.run(Discovery(config, now=NOW).refresh(API_KEY))

    asyncio.run(Discovery(config, now=NOW + timedelta(days=7)).refresh(API_KEY))

    assert search.await_count == 1
    assert _state(state_path)["attempts"] == 1


def test_should_reset_attempts_only_when_the_provider_renewal_advances(
    state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account, search = _providers(monkeypatch)
    config = _config(state_path, cycle_budget=1)
    asyncio.run(Discovery(config, now=NOW).refresh(API_KEY))
    account.return_value = (1_000, "2026-11-01")

    asyncio.run(
        Discovery(config, now=datetime(2026, 10, 1, 12, tzinfo=UTC)).refresh(API_KEY)
    )

    assert search.await_count == 2
    assert _state(state_path)["renewal"] == "2026-11-01"
    assert _state(state_path)["attempts"] == 1


@pytest.mark.parametrize(
    ("when", "reported_renewal"),
    [
        (NOW + timedelta(days=7), "2026-09-30"),
        (datetime(2026, 10, 1, 12, tzinfo=UTC), RENEWAL),
    ],
    ids=["backwards-renewal", "calendar-advanced-provider-did-not"],
)
def test_should_refuse_an_ambiguous_reset_without_erasing_the_budget(
    state_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    when: datetime,
    reported_renewal: str,
) -> None:
    account, search = _providers(monkeypatch)
    config = _config(state_path, cycle_budget=1)
    asyncio.run(Discovery(config, now=NOW).refresh(API_KEY))
    before = state_path.read_bytes()
    account.return_value = (1_000, reported_renewal)

    with pytest.raises(DiscoveryError):
        asyncio.run(Discovery(config, now=when).refresh(API_KEY))

    assert search.await_count == 1
    assert state_path.read_bytes() == before


@pytest.mark.parametrize("items", [[ITEM], []], ids=["products", "successful-empty"])
def test_should_reuse_successful_queries_in_the_same_week_without_account_calls(
    state_path: Path, monkeypatch: pytest.MonkeyPatch, items: list[dict]
) -> None:
    account, search = _providers(monkeypatch)
    search.return_value = items
    config = _config(state_path)
    asyncio.run(Discovery(config, now=NOW).refresh(API_KEY))
    account.reset_mock()
    search.reset_mock()

    store = Discovery(config, now=NOW + timedelta(days=4))
    status = asyncio.run(store.refresh(API_KEY))

    assert store.cached(QUERY) == items
    assert "Cached" in status
    account.assert_not_awaited()
    search.assert_not_awaited()


def test_should_refresh_only_the_new_query_on_a_same_week_rerun(
    state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, search = _providers(monkeypatch)
    asyncio.run(Discovery(_config(state_path, ("first",)), now=NOW).refresh(API_KEY))
    search.reset_mock()

    asyncio.run(
        Discovery(_config(state_path, ("first", "second")), now=NOW).refresh(API_KEY)
    )

    search.assert_awaited_once_with("second", API_KEY, "de")


@pytest.mark.parametrize(
    ("query", "country", "expected"),
    [(QUERY, "de", [ITEM]), (QUERY.upper(), "de", []), (QUERY, "lv", [])],
    ids=["exact-match", "different-query", "different-country"],
)
def test_should_key_cache_by_exact_query_and_country(
    state_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    country: str,
    expected: list[dict],
) -> None:
    _providers(monkeypatch)
    asyncio.run(Discovery(_config(state_path), now=NOW).refresh(API_KEY))

    actual = Discovery(_config(state_path, country=country), now=NOW).cached(query)

    assert actual == expected


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(days=8), [ITEM]),
        (timedelta(days=8, seconds=1), []),
        (timedelta(seconds=-1), []),
    ],
    ids=["expiry-boundary", "expired", "future-entry"],
)
def test_should_not_return_expired_or_future_discovery(
    state_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    age: timedelta,
    expected: list[dict],
) -> None:
    _providers(monkeypatch)
    config = _config(state_path, cache_days=8)
    asyncio.run(Discovery(config, now=NOW).refresh(API_KEY))

    assert Discovery(config, now=NOW + age).cached(QUERY) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "{broken json",
        "[]",
        "{}",
        json.dumps({"version": 2, "renewal": RENEWAL, "attempts": 3, "entries": {}}),
        json.dumps({"version": 1, "renewal": RENEWAL, "attempts": -1, "entries": {}}),
        json.dumps({"version": 1, "renewal": RENEWAL, "attempts": True, "entries": {}}),
        json.dumps({"version": 1, "renewal": "not-a-date", "attempts": 3, "entries": {}}),
        json.dumps({"version": 1, "renewal": RENEWAL, "attempts": 3, "entries": []}),
        json.dumps({
            "version": 1, "renewal": RENEWAL, "attempts": 3,
            "entries": {"entry": {"at": NOW.isoformat(), "items": "not-a-list"}},
        }),
        json.dumps({
            "version": 1, "renewal": RENEWAL, "attempts": 3,
            "entries": {"entry": {"at": "2026-09-14T12:00:00", "items": []}},
        }),
    ],
    ids=[
        "invalid-json", "array", "missing-fields", "unknown-schema", "negative-attempts",
        "boolean-attempts", "invalid-renewal", "invalid-entries", "invalid-items",
        "ambiguous-timestamp",
    ],
)
def test_malformed_state_should_fail_closed_without_any_provider_calls_or_rewrite(
    state_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    account, search = _providers(monkeypatch)
    state_path.write_text(raw, encoding="utf-8")
    store = Discovery(_config(state_path), now=NOW)

    with pytest.raises(DiscoveryError):
        asyncio.run(store.refresh(API_KEY))
    with pytest.raises(DiscoveryError):
        store.cached(QUERY)

    account.assert_not_awaited()
    search.assert_not_awaited()
    assert state_path.read_text(encoding="utf-8") == raw


def test_should_block_a_concurrent_refresh_before_it_contacts_the_provider(
    state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account, search = _providers(monkeypatch)
    config = _config(state_path)

    async def concurrent_refreshes() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def held_search(*args: object) -> list[dict]:
            entered.set()
            await release.wait()
            return [ITEM]

        search.side_effect = held_search
        first = asyncio.create_task(Discovery(config, now=NOW).refresh(API_KEY))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            with pytest.raises(DiscoveryError):
                await Discovery(config, now=NOW).refresh(API_KEY)
            assert account.await_count == 1
            assert search.await_count == 1
        finally:
            release.set()
            await first

    asyncio.run(concurrent_refreshes())

    assert _state(state_path)["attempts"] == 1
    assert not state_path.with_suffix(".lock").exists()


def test_http_429_should_stop_the_remainder_and_surface_only_a_safe_error(
    state_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(discovery, "account_snapshot", AsyncMock(return_value=(100, RENEWAL)))
    session, response = _http_response(
        monkeypatch, 429, {"error": f"quota exceeded for {API_KEY}: {QUERY}"}
    )
    caplog.set_level(logging.INFO, logger=discovery.__name__)

    with pytest.raises(DiscoveryError, match="429") as error:
        asyncio.run(
            Discovery(_config(state_path, (QUERY, "second query")), now=NOW).refresh(API_KEY)
        )

    assert session.get.call_count == 1
    response.json.assert_not_awaited()
    assert _state(state_path)["attempts"] == 1
    assert _state(state_path)["entries"] == {}
    visible = str(error.value) + caplog.text + state_path.read_text(encoding="utf-8")
    assert API_KEY not in visible
    assert QUERY not in visible


@pytest.mark.parametrize("error_type", [aiohttp.ClientConnectionError, TimeoutError])
def test_transport_failure_should_not_disclose_the_key_or_query(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_type: type[Exception],
) -> None:
    session, _ = _http_response(monkeypatch, 200, {})
    session.get.side_effect = error_type(f"https://serpapi.com/search?api_key={API_KEY}&q={QUERY}")

    with pytest.raises(DiscoveryError) as error:
        asyncio.run(discovery.search(QUERY, API_KEY, "de"))

    assert API_KEY not in str(error.value) + caplog.text
    assert QUERY not in str(error.value) + caplog.text


@pytest.mark.parametrize("status", [None, "Processing", "Error", "success"])
def test_should_require_provider_success_even_if_shopping_results_are_present(
    monkeypatch: pytest.MonkeyPatch, status: str | None
) -> None:
    _http_response(
        monkeypatch, 200,
        {"search_metadata": {"id": SEARCH_ID, "status": status}, "shopping_results": [ITEM]},
    )

    with pytest.raises(DiscoveryError):
        asyncio.run(discovery.search(QUERY, API_KEY, "de"))


def test_successful_search_should_log_its_provider_id_but_not_request_details(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _http_response(
        monkeypatch, 200,
        {
            "search_metadata": {"id": SEARCH_ID, "status": "Success"},
            "search_parameters": {"q": QUERY, "api_key": API_KEY},
            "shopping_results": [ITEM],
        },
    )
    caplog.set_level(logging.INFO, logger=discovery.__name__)

    results = asyncio.run(discovery.search(QUERY, API_KEY, "de"))

    assert len(results) == 1
    assert results[0]["title"] == ITEM["title"]
    assert (results[0].get("link") or results[0].get("product_link")) == ITEM["product_link"]
    assert SEARCH_ID in caplog.text
    assert API_KEY not in caplog.text
    assert QUERY not in caplog.text


def test_should_accept_a_legitimate_empty_success_with_a_provider_explanation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _http_response(
        monkeypatch, 200,
        {
            "search_metadata": {"id": SEARCH_ID, "status": "Success"},
            "error": "Google Shopping hasn't returned any results for this query.",
        },
    )

    assert asyncio.run(discovery.search(QUERY, API_KEY, "de")) == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"total_searches_left": -1, "plan_renewal_date": RENEWAL},
        {"total_searches_left": True, "plan_renewal_date": RENEWAL},
        {"total_searches_left": 100, "plan_renewal_date": "unknown"},
    ],
)
def test_invalid_account_metadata_should_not_authorize_a_search(
    state_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict
) -> None:
    _http_response(monkeypatch, 200, payload)
    search = AsyncMock()
    monkeypatch.setattr(discovery, "search", search)

    with pytest.raises(DiscoveryError):
        asyncio.run(Discovery(_config(state_path), now=NOW).refresh(API_KEY))

    search.assert_not_awaited()


@pytest.mark.parametrize("consumer", ["hunt", "shortlist"])
def test_routine_consumers_should_never_refresh_a_missing_cache_even_with_a_key(
    state_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str
) -> None:
    from dealscout import run_hunt, run_shortlist
    from dealscout.models import Hunt

    account, search = _providers(monkeypatch)
    refresh = AsyncMock(side_effect=AssertionError("Only the weekly job may refresh"))
    monkeypatch.setattr(Discovery, "refresh", refresh)
    monkeypatch.setenv("SERPAPI_KEY", API_KEY)
    hunt = Hunt(id="example", category="footwear", queries=(QUERY,))
    config = _config(state_path)
    config["scrape"] = {"delay_seconds": 0, "max_confirmations": 0}

    if consumer == "hunt":
        results, products = asyncio.run(run_hunt.run_hunt(hunt, config, {}, {}))
        assert results == [] and products == []
    else:
        result = asyncio.run(run_shortlist.shortlist_for(hunt, config, limit=10, per_source=5))
        assert result.checked == 0

    refresh.assert_not_awaited()
    account.assert_not_awaited()
    search.assert_not_awaited()

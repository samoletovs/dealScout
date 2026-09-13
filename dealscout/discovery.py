"""Weekly paid discovery; all other entrypoints are cache-only consumers."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp

from .monitor import canonical_url

logger = logging.getLogger(__name__)
DEFAULT_PATH = Path("state/discovery.json")
RESULT_FIELDS = (
    "title", "extracted_price", "extracted_old_price",
    "source", "second_hand_condition",
)


class DiscoveryError(RuntimeError):
    """Discovery could not complete safely; messages never contain request URLs."""


def write_status(status: str) -> None:
    logger.info("SerpApi discovery: %s", status)
    body = f"### SerpApi discovery\n\n{status}\n"
    output = Path("out/discovery-status.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body, encoding="utf-8")
    if summary := os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(body + "\n")


def enabled(config: dict) -> bool:
    return (config.get("serpapi") or {}).get("enabled", False) is True


def query_terms(config: dict) -> list[str]:
    block = config.get("serpapi") or {}
    queries = [entry["q"] for entry in block.get("queries", []) if entry.get("q")]
    for hunt in config.get("hunts", []):
        if hunt.get("enabled", True):
            queries.extend(hunt.get("queries", []))
    if any(not isinstance(query, str) or not query.strip() for query in queries):
        raise DiscoveryError("SerpApi discovery queries must be nonempty strings")
    return list(dict.fromkeys(query.strip() for query in queries))


def _integer(block: dict, name: str, default: int, minimum: int = 1) -> int:
    value = block.get(name, default)
    if type(value) is not int or value < minimum:
        raise DiscoveryError(f"serpapi.{name} must be an integer >= {minimum}")
    return value


async def _get_json(endpoint: str, params: dict) -> dict:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://serpapi.com/{endpoint}", params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise DiscoveryError(
                        f"SerpApi HTTP {response.status}; discovery stopped, not an empty result"
                    )
                data = await response.json()
    except (aiohttp.ClientError, OSError, ValueError) as exc:
        raise DiscoveryError(
            f"SerpApi request failed ({type(exc).__name__}); discovery stopped"
        ) from None
    if not isinstance(data, dict):
        raise DiscoveryError("SerpApi returned an invalid response")
    return data


async def account_snapshot(api_key: str) -> tuple[int, str]:
    data = await _get_json("account.json", {"api_key": api_key})
    remaining, renewal = data.get("total_searches_left"), data.get("plan_renewal_date")
    if type(remaining) is not int or remaining < 0 or not isinstance(renewal, str):
        raise DiscoveryError("SerpApi account quota or renewal date is unavailable")
    try:
        date.fromisoformat(renewal)
    except ValueError:
        raise DiscoveryError("SerpApi account renewal date is invalid") from None
    return remaining, renewal


def retailer_link(item: dict) -> str:
    """Keep direct merchant product URLs, not search pages or tracking tokens."""
    for field in ("link", "product_link"):
        raw = item.get(field)
        if not isinstance(raw, str):
            continue
        try:
            parts = urlsplit(canonical_url(raw))
            host = parts.hostname or ""
            if (
                parts.scheme != "https" or "." not in host
                or parts.username or parts.password or parts.port not in (None, 443)
                or host.endswith((".local", ".localhost"))
                or any(label in {"google", "googleadservices", "gstatic", "serpapi"} for label in host.split("."))
                or re.fullmatch(r"[0-9.]+", host)
            ):
                continue
        except ValueError:
            continue
        identity = [
            (key, value) for key, value in parse_qsl(parts.query)
            if key.lower() in {"id", "product_id", "sku", "variant"}
            and re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", value)
        ]
        fragment = parts.fragment if re.fullmatch(r"colcode=[0-9]{1,30}", parts.fragment) else ""
        return urlunsplit(("https", host, parts.path, urlencode(identity), fragment))
    return ""


async def search(query: str, api_key: str, country: str) -> list[dict]:
    data = await _get_json("search.json", {
        "engine": "google_shopping", "q": query, "api_key": api_key,
        "gl": country, "on_sale": "true",
    })
    # SerpApi can return an `error` for a successful search with no results.
    if data.get("search_metadata", {}).get("status") != "Success":
        raise DiscoveryError("SerpApi search did not succeed; discovery stopped")
    items = data.get("shopping_results") or []
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise DiscoveryError("SerpApi shopping results are invalid")
    search_id = data.get("search_metadata", {}).get("id", "")
    if isinstance(search_id, str) and re.fullmatch(r"[a-f0-9]{16,64}", search_id):
        logger.info("SerpApi discovery search succeeded: id=%s, items=%d", search_id, len(items))
    direct = []
    for item in items:
        if link := retailer_link(item):
            clean = {
                key: item[key] for key in RESULT_FIELDS
                if key in item and isinstance(item[key], (str, int, float))
            }
            direct.append({**clean, "link": link})
    logger.info(
        "SerpApi discovery: %d direct merchant links; %d search/intermediary links discarded",
        len(direct), len(items) - len(direct),
    )
    return direct


class Discovery:
    def __init__(self, config: dict, now: datetime | None = None) -> None:
        self.config = config
        block = config.get("serpapi") or {}
        self.path = Path(os.getenv("DEALSCOUT_DISCOVERY_PATH") or block.get("state_path") or DEFAULT_PATH)
        self.country = str(block.get("country") or "de").lower()
        self.cycle_budget = _integer(block, "cycle_budget", 60, 0)
        self.reserve = _integer(block, "account_reserve", 50, 0)
        self.run_budget = _integer(block, "max_searches_per_refresh", 12)
        self.cache_days = _integer(block, "cache_days", 8)
        self.now = now or datetime.now(UTC)

    def _key(self, query: str) -> str:
        return hashlib.sha256(
            json.dumps([self.country, query.strip()], ensure_ascii=True).encode()
        ).hexdigest()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "renewal": "", "attempts": 0, "entries": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise DiscoveryError("SerpApi discovery state is unreadable; refusing to reset budget") from None
        if (
            not isinstance(data, dict) or data.get("version") != 1
            or type(data.get("attempts")) is not int or data["attempts"] < 0
            or not isinstance(data.get("renewal"), str)
            or not isinstance(data.get("entries"), dict)
        ):
            raise DiscoveryError("SerpApi discovery state is invalid; refusing to reset budget")
        if data["renewal"]:
            try:
                date.fromisoformat(data["renewal"])
            except ValueError:
                raise DiscoveryError("SerpApi stored renewal is invalid; refusing to reset budget") from None
        for entry in data["entries"].values():
            if (
                not isinstance(entry, dict) or not isinstance(entry.get("items"), list)
                or any(not isinstance(item, dict) for item in entry["items"])
            ):
                raise DiscoveryError("SerpApi cached discovery is invalid")
            try:
                when = datetime.fromisoformat(entry["at"])
                if when.tzinfo is None:
                    raise ValueError("timezone missing")
            except (KeyError, TypeError, ValueError):
                raise DiscoveryError("SerpApi discovery timestamp is invalid") from None
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

    def cached(self, query: str) -> list[dict]:
        if not enabled(self.config):
            logger.info("SerpApi discovery disabled; direct sources remain active")
            return []
        entry = self._load()["entries"].get(self._key(query))
        if entry and self.now - timedelta(days=self.cache_days) <= datetime.fromisoformat(entry["at"]) <= self.now:
            return entry["items"]
        logger.warning("SerpApi discovery cache missing/expired; direct sources remain active")
        return []

    def status(self) -> str:
        if not enabled(self.config):
            return "Disabled: direct retailer sources remain active."
        try:
            data = self._load()
        except DiscoveryError as exc:
            return f"Unavailable: {exc}. Direct retailer sources remain active."
        recent = sum(
            self.now - timedelta(days=self.cache_days) <= datetime.fromisoformat(entry["at"]) <= self.now
            for entry in data["entries"].values()
        )
        return (
            f"Cache-only: {recent} recent discovery queries; no paid searches in this job. "
            f"{data.get('status', 'Waiting for weekly discovery.')} "
            "Cached links are checked at the retailer; cached prices are not live evidence."
        )

    async def refresh(self, api_key: str | None = None) -> str:
        if not enabled(self.config):
            return "Disabled: direct retailer sources remain active."
        api_key = api_key or os.getenv("SERPAPI_KEY")
        if not api_key:
            raise DiscoveryError("SerpApi weekly discovery has no API key; direct sources remain active")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(".lock")
        try:
            handle = lock.open("x", encoding="utf-8")
        except FileExistsError:
            raise DiscoveryError("Another discovery refresh holds the state lock; no searches made") from None
        try:
            with handle:
                return await self._refresh(api_key)
        finally:
            lock.unlink()

    async def _refresh(self, api_key: str) -> str:
        data = self._load()
        queries = query_terms(self.config)
        week = self.now.isocalendar()[:2]
        pending = [
            query for query in queries
            if (entry := data["entries"].get(self._key(query))) is None
            or datetime.fromisoformat(entry["at"]).isocalendar()[:2] != week
        ]
        if not pending:
            return "Cached: this week's discovery is already available; 0 searches."
        remaining, renewal = await account_snapshot(api_key)
        if date.fromisoformat(renewal) <= self.now.date():
            raise DiscoveryError("SerpApi renewal has not advanced; refusing an ambiguous budget reset")
        if data["renewal"] != renewal:
            if data["renewal"] and renewal < data["renewal"]:
                raise DiscoveryError("SerpApi renewal moved backwards; refusing to reset budget")
            data["renewal"], data["attempts"] = renewal, 0
        attempted = 0
        for query in pending:
            if (
                remaining <= self.reserve or data["attempts"] >= self.cycle_budget
                or attempted >= self.run_budget
            ):
                status = (
                    f"Paused: discovery budget/reserve reached; {attempted} searches this run, "
                    f"{data['attempts']}/{self.cycle_budget} this cycle, "
                    f"{remaining} account credits left; renews {renewal}. Direct sources continue."
                )
                data["status"] = status
                self._save(data)
                return status
            # Reserve before dispatch: timeouts can still have consumed a provider credit.
            data["attempts"] += 1
            attempted += 1
            self._save(data)
            try:
                items = await search(query, api_key, self.country)
            except DiscoveryError as exc:
                data["status"] = f"Failed: {exc}. Reserved attempt retained."
                self._save(data)
                raise
            data["entries"][self._key(query)] = {"at": self.now.isoformat(), "items": items}
            active = {self._key(term) for term in queries}
            data["entries"] = {key: value for key, value in data["entries"].items() if key in active}
            self._save(data)
            remaining, current_renewal = await account_snapshot(api_key)
            if current_renewal != renewal:
                raise DiscoveryError("SerpApi billing cycle changed during refresh; restart next run")
        status = (
            f"Updated: {attempted} discovery searches; {data['attempts']}/{self.cycle_budget} "
            f"cycle attempts, {remaining} account credits left; renews {renewal}."
        )
        data["status"] = status
        self._save(data)
        return status

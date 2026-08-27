"""Remember the one thing about a confirmed boot that is safe to remember: its RRP.

Roughly half of every run's confirmation requests are not chasing a size at all — they are
re-fetching a product page for a ``reference_price`` the listing omitted, so the email can
say "-58%". That number was learned last run and thrown away, then learned again from
scratch this run, spending a slot each time on a boot whose size we may already know.

Caching *stock* would be dangerous: sizes sell out between runs, and a boot shown "in EU
37.5" when it is gone costs the owner an order and a return — the one mistake this engine
must never make. But an **RRP is a manufacturer's list price**; it moves at a season
boundary, not between runs, and getting it slightly stale only mis-states a discount
percentage, never a size or availability. So this remembers RRP and nothing else.

The store is a small JSON map, ``canonical_url -> {price, at}``, beside the other ``state/``
files. It is disposable in the project's usual way: a missing or corrupt file means "no
memory", never a crash. Entries older than ``keep_days`` are dropped on save, so a boot the
shops stopped selling does not haunt the cache, and a genuinely re-priced boot's stale RRP
cannot survive indefinitely — the same anti-rot discipline the price history and the boot
catalogue already carry.

Applied *before* planning confirmations: a boot whose RRP is remembered no longer owes one,
so its slot is freed for a boot that still owes a size. The RRP is only ever *filled in*
where the listing gave none — a live RRP from the page always wins, so a shop that corrects
its own list price is believed over the cache immediately.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import Product
from .monitor import canonical_url

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("state/rrp.json")
# An RRP is a season-scale fact. Half a year outlives any single price correction while
# still expiring a boot the shops have retired, so a wrong number cannot live forever.
DEFAULT_KEEP_DAYS = 180


@dataclass(frozen=True)
class RrpConfig:
    """Where the RRP memory lives and how long a remembered price is trusted."""

    path: Path = DEFAULT_PATH
    keep_days: int = DEFAULT_KEEP_DAYS

    @classmethod
    def from_config(cls, config: dict) -> RrpConfig:
        block = (config.get("scrape") or {})
        path = block.get("rrp_cache_path")
        days = block.get("rrp_cache_days")
        try:
            keep = int(days)
        except (TypeError, ValueError):
            keep = DEFAULT_KEEP_DAYS
        return cls(
            path=Path(path) if path else DEFAULT_PATH,
            keep_days=keep if keep > 0 else DEFAULT_KEEP_DAYS,
        )


def load(path: Path = DEFAULT_PATH) -> dict[str, float]:
    """Read the remembered RRPs keyed by canonical URL; absence means no memory."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("rrp cache unreadable (%s) — no RRP memory this run", exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    prices: dict[str, float] = {}
    for url, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        try:
            prices[str(url)] = float(entry["price"])
        except (KeyError, TypeError, ValueError):
            continue
    return prices


def apply(products: Iterable[Product], remembered: dict[str, float]) -> list[Product]:
    """Fill in a missing RRP from memory, never overriding one the page stated (pure).

    A live RRP always wins: this only speaks where the listing was silent, so a shop that
    corrects its own list price is believed immediately and the cache follows on save.
    """
    out: list[Product] = []
    for product in products:
        if product.reference_price is None:
            price = remembered.get(canonical_url(product.url))
            if price is not None:
                product = replace(product, reference_price=price)
        out.append(product)
    return out


def learn(
    products: Iterable[Product],
    remembered: dict[str, float],
) -> dict[str, float]:
    """Fold this run's known RRPs into the memory, returning a NEW map (input untouched).

    Records any RRP a product now carries. A value filled from the cache re-enters
    unchanged, and :func:`save` keeps each already-known URL's original timestamp (from the
    file, via ``stamps``) rather than refreshing it — so re-learning a cached value is a
    no-op that cannot dodge expiry. Only a URL learned for the first time is stamped now.
    """
    updated = dict(remembered)
    for product in products:
        if product.reference_price is not None:
            updated[canonical_url(product.url)] = float(product.reference_price)
    return updated


def save(
    remembered: dict[str, float],
    stamps: dict[str, str] | None = None,
    keep_days: int = DEFAULT_KEEP_DAYS,
    path: Path = DEFAULT_PATH,
    now: datetime | None = None,
) -> Path:
    """Persist the memory, timestamping fresh entries and dropping expired ones.

    ``stamps`` carries the ``at`` for URLs already in the file so an untouched entry keeps
    its original age and can expire; a URL learned this run is stamped now.
    """
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(days=max(1, keep_days))
    stamps = stamps or {}
    payload: dict[str, dict] = {}
    for url, price in remembered.items():
        at = stamps.get(url)
        try:
            when = datetime.fromisoformat(at) if at else moment
        except ValueError:
            when = moment
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        if when < cutoff:
            continue
        payload[url] = {"price": price, "at": when.isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    logger.info("rrp cache: %d remembered -> %s", len(payload), path)
    return path


def load_stamps(path: Path = DEFAULT_PATH) -> dict[str, str]:
    """Read each entry's ``at`` timestamp, so unchanged entries keep their real age."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    stamps: dict[str, str] = {}
    for url, entry in raw.items():
        if isinstance(entry, dict) and entry.get("at"):
            stamps[str(url)] = str(entry["at"])
    return stamps

"""Monitor — remember what we've seen so a scheduled hunt reports news, not noise.

A hunt on a cron is only useful if it stays quiet. Without memory, every run re-emails
the same boots and the signal is ignored within a week. The monitor keeps a small JSON
ledger of every product a hunt has seen (price, best price ever, stock, timestamps) and
classifies each sighting as **new**, **price-drop**, **back-in-stock** or **seen**.

Only the first three are worth an email. That also gives the price history a later
Fabric/Lakehouse analytics layer would ingest.

State is plain JSON so it can be committed by a CI job, kept in a blob, or thrown away
without consequence — losing it costs one noisy run, nothing more.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Change, Product

logger = logging.getLogger(__name__)

# Tracking parameters that change per click and would otherwise mint a "new" product
# on every run.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = frozenset(
    {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "ref", "referrer", "_branch_match_id"}
)

DEFAULT_STATE_PATH = Path("state/hunts.json")
DEFAULT_MIN_DROP_PCT = 5.0
DEFAULT_FORGET_AFTER_DAYS = 90


def canonical_url(url: str) -> str:
    """Strip tracking parameters so the same product keys consistently across runs.

    The fragment is DELIBERATELY preserved: some retailers identify a colourway there
    (e.g. ``…-084181#colcode=08418103``), so dropping it would merge distinct products.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_KEYS
        and not any(k.lower().startswith(p) for p in _TRACKING_PREFIXES)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


def load_state(path: Path = DEFAULT_STATE_PATH) -> dict[str, dict]:
    """Load the seen-products ledger; an unreadable or missing file means 'first run'."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("monitor state unreadable (%s) — starting fresh", exc)
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict[str, dict], path: Path = DEFAULT_STATE_PATH) -> Path:
    """Write the ledger, creating the directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    logger.info("monitor state: %d product(s) -> %s", len(state), path)
    return path


def _in_stock(product: Product, wanted_sizes: tuple[str, ...]) -> bool | None:
    """Tri-state stock for the sizes we care about (None = the page didn't say)."""
    if not wanted_sizes or not product.sizes_known:
        return None
    from .spec import size_matches  # local import keeps the module dependency-light

    return size_matches(wanted_sizes, product.sizes)


def classify(
    product: Product,
    state: dict[str, dict],
    wanted_sizes: tuple[str, ...] = (),
    min_drop_pct: float = DEFAULT_MIN_DROP_PCT,
) -> Change:
    """Classify one sighting against the ledger (pure — does not mutate state)."""
    previous = state.get(canonical_url(product.url))
    if previous is None:
        return Change(product, "new")

    prior_price = previous.get("price")
    try:
        prior_price = float(prior_price) if prior_price is not None else None
    except (TypeError, ValueError):
        prior_price = None

    if prior_price and prior_price > 0:
        drop = (prior_price - product.price) / prior_price * 100.0
        if drop >= min_drop_pct:
            return Change(product, "price-drop", prior_price)

    now_stocked = _in_stock(product, wanted_sizes)
    if now_stocked is True and previous.get("in_stock") is False:
        return Change(product, "back-in-stock", prior_price)

    return Change(product, "seen", prior_price)


def record(
    state: dict[str, dict],
    products: list[Product],
    wanted_sizes: tuple[str, ...] = (),
    now: datetime | None = None,
) -> dict[str, dict]:
    """Return a NEW ledger with these sightings folded in (does not mutate the input)."""
    stamp = (now or datetime.now(UTC)).isoformat()
    updated = {k: dict(v) for k, v in state.items()}
    for product in products:
        key = canonical_url(product.url)
        entry = updated.get(key, {"first_seen": stamp, "title": product.title})
        best = entry.get("best_price")
        try:
            best = min(float(best), product.price) if best is not None else product.price
        except (TypeError, ValueError):
            best = product.price
        entry.update(
            {
                "title": product.title,
                "price": product.price,
                "reference_price": product.reference_price,
                "currency": product.currency,
                "source": product.source,
                "best_price": best,
                "in_stock": _in_stock(product, wanted_sizes),
                "last_seen": stamp,
            }
        )
        entry.setdefault("first_seen", stamp)
        updated[key] = entry
    return updated


def forget_stale(
    state: dict[str, dict],
    older_than_days: int = DEFAULT_FORGET_AFTER_DAYS,
    now: datetime | None = None,
) -> dict[str, dict]:
    """Drop products not seen for a long time, so the ledger can't grow forever.

    A delisted product that reappears months later is genuinely news again.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=older_than_days)
    kept: dict[str, dict] = {}
    for key, entry in state.items():
        raw = entry.get("last_seen")
        try:
            seen = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            kept[key] = entry  # unparseable timestamp: keep rather than lose history
            continue
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=UTC)
        if seen >= cutoff:
            kept[key] = entry
    dropped = len(state) - len(kept)
    if dropped:
        logger.info("monitor: forgot %d product(s) unseen for %dd", dropped, older_than_days)
    return kept

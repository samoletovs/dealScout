"""Price memory — what a product has *actually* been selling for.

The email can already say "RRP €330, -70%". That number comes from the retailer, and
retailers set it themselves: a "was" price is often a launch price from two seasons ago
that nobody ever paid. So a headline discount is evidence about the retailer's marketing,
not about whether this is a good moment to buy.

The question the owner is actually asking is *"is this price low for this boot?"*, and
answering it needs the one thing the monitor never kept: prices over time. The monitor's
ledger stores the **last** price and the best ever, both of which are single numbers with
no shape — you cannot ask them what the usual price is, or how long today's price has held.

So this module keeps an append-only log of observations (one per product per run) and
turns it into a :class:`PriceMemory` — a structured, unopinionated read that the renderer
decides how to word.

The hard part is **restraint**. With two observations you cannot claim "lowest in 90 days",
and saying so would be exactly the confident-and-wrong statement this project refuses.
Below a floor of observations *and* of elapsed time, :class:`PriceMemory` says so in a
field of its own — ``enough_history=False``, every claim left ``None`` — rather than
returning a zero that reads like a measurement.

State lives beside the monitor's ledger under ``state/``. It is disposable: a missing or
corrupt history means "no opinion", never a crash, and costs at most a few quiet runs.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

from .models import Product
from .monitor import canonical_url, in_stock

logger = logging.getLogger(__name__)

# The identity a price is *about*: the boot, not the listing. A URL is a retailer's
# identifier — the same Predator Elite is a different URL at every shop, so keying price
# memory on the URL answers "cheapest this *listing* has been", never "cheapest this
# *boot* has been anywhere", which is the claim an honest cross-retailer archive makes.
# These are the attributes that make two listings the same boot: brand, silo (model line),
# generation, tier, the soleplate the boot is built on and the audience the size grid is
# cut for. They are resolved once, by the same catalogue/vocabulary path the judge uses,
# and passed in — this module stays dependency-light and never disagrees with the judge
# about what a boot is.
#
# **Soleplate is in the key, and this is a correctness rule, not an optimisation.** The FG
# and SG of the same boot are different SKUs at different prices and are *not substitutes*:
# a firm-ground player cannot wear soft-ground studs on turf. Pooling them would let the
# Scout quote an SG clearance price as the low for an FG boot — the confident, checkable,
# and wrong price claim this whole project exists to refuse. So when a listing does not
# state its soleplate, the honest reading is a distinct "unknown" bucket, *not* a merge
# into the FG history: a missed pooling (two FG listings that don't share history because
# one stayed silent) costs a little depth; a wrong merge costs the trust the number carries.
#
# Colourway is deliberately **not** in the key. A Mercurial in blue and the same Mercurial
# in white are the same boot, and pooling colourways is exactly what lets the archive say
# "this boot has been cheaper than this". It is stored as an attribute on the observation
# all the same, because a discontinued colourway is often *why* a price fell — the drop is
# real, and the colourway is the explanation the renderer may want to show.
_IDENTITY_ATTRS: tuple[str, ...] = ("brand", "silo", "generation", "tier", "soleplate")

DEFAULT_HISTORY_PATH = Path("state/prices.jsonl")
DEFAULT_KEEP_DAYS = 180
DEFAULT_MAX_POINTS = 200
# The floor below which the honest answer is "not enough history yet". Three observations
# spread over a week is the least that can distinguish a real low from the first two
# numbers we happened to see.
DEFAULT_MIN_OBSERVATIONS = 3
DEFAULT_MIN_SPAN_DAYS = 7.0

# Prices are money, so compare them at cent resolution rather than by float identity.
_CENT = 0.005


@dataclass(frozen=True)
class Observation:
    """One sighting of one product's price at one moment.

    ``url`` keys the retailer's listing (tracking-stripped), so a run can link exactly what
    it saw. ``boot_key`` keys the *boot* — a stable identity (brand, silo, generation, tier,
    soleplate, audience) resolved from the catalogue — so price memory can answer "cheapest
    this boot has been, at any shop". It is empty when the boot could not be classified (the
    catalogue's honest ``unknown``), in which case the memory falls back to the URL rather
    than merging two boots it cannot tell apart. ``size`` pins the specific EU size a
    per-size price refers to, so a junior EU 37 low is never contaminated by an adult EU 44
    of the "same" model — just as the soleplate in ``boot_key`` keeps an SG price from ever
    standing in for an FG one.
    """

    url: str  # canonical (tracking-stripped), so it keys the same across runs
    price: float
    at: datetime
    source: str = ""
    in_stock: bool | None = None  # None = the page never said
    boot_key: str = ""  # resolved boot identity; "" when the boot is unclassified
    size: str = ""  # the EU size this price/stock is for; "" when the page never said

    def to_json(self) -> str:
        """Serialise to a single JSONL line."""
        return json.dumps(
            {
                "url": self.url,
                "price": self.price,
                "at": self.at.isoformat(),
                "source": self.source,
                "in_stock": self.in_stock,
                "boot_key": self.boot_key,
                "size": self.size,
            },
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, data: dict) -> Observation | None:
        """Parse one logged observation, or None if the line cannot be trusted.

        ``boot_key`` and ``size`` default to empty when absent, so a log written before the
        identity fields existed still reads — the old lines simply key on their URL, as
        they always did, and new lines key on the boot.
        """
        try:
            url = str(data["url"]).strip()
            price = float(data["price"])
            at = datetime.fromisoformat(str(data["at"]))
        except (KeyError, TypeError, ValueError):
            return None
        if not url:
            return None
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        stock = data.get("in_stock")
        return cls(
            url=url,
            price=price,
            at=at,
            source=str(data.get("source") or ""),
            in_stock=bool(stock) if stock is not None else None,
            boot_key=str(data.get("boot_key") or ""),
            size=str(data.get("size") or ""),
        )

    @property
    def key(self) -> str:
        """What this observation is *about*: the boot when known, else the listing.

        A resolved boot key lets two retailers' listings of one boot share a price history;
        an unclassified boot has no honest identity to share, so it keeps its own URL and is
        never silently folded into another.
        """
        return self.boot_key or self.url


def boot_key(attrs: dict[str, str]) -> str:
    """A stable identity string for a boot, from already-resolved attributes.

    ``brand/silo/generation/tier/soleplate/audience`` — the fields that make two listings
    the same boot. Pure and closed over its input: the caller resolves attributes via the
    catalogue (the same path the judge uses) and passes them here, so this module needs no
    catalogue dependency and can never read a title by an older rule than the judge.

    Soleplate carries a hard rule: it is part of the key, and an *unstated* soleplate keys
    as ``unknown`` rather than being folded into any stated one. FG and SG are different
    boots at different prices and are not substitutes, so quoting one as the low for the
    other is the dishonesty this project refuses — the missed pooling of a silent listing
    is the cheaper mistake.

    Returns ``""`` when the identity is too thin to trust — no brand, or the catalogue
    declined to name a tier. An empty key is the signal to fall back to the URL, never a
    key that would merge every unclassified boot into one.
    """
    parts = [str(attrs.get(name) or "").strip().lower() for name in _IDENTITY_ATTRS]
    brand, silo, generation, tier, soleplate = parts
    if not brand or not tier or tier == "unknown":
        return ""
    audience = _audience(attrs)
    return f"{brand}/{silo}/{generation}/{tier}/{soleplate or 'unknown'}/{audience}"


def _audience(attrs: dict[str, str]) -> str:
    """Who the size grid is cut for: 'junior', 'adult', or '' when unstated.

    A junior flagship and an adult flagship are different boots at different prices, so the
    audience belongs in the identity. It is read from the tier first (the catalogue marks a
    junior flagship as such) and the ``fit`` attribute second.
    """
    tier = str(attrs.get("tier") or "").strip().lower()
    if tier.startswith("junior"):
        return "junior"
    if tier.startswith("adult"):
        return "adult"
    fit = str(attrs.get("fit") or "").strip().lower()
    return fit if fit in {"junior", "adult"} else ""



@dataclass(frozen=True)
class PriceMemory:
    """What the history supports saying about one price — and nothing more.

    ``enough_history`` is the whole point of the type. When it is False the history is too
    thin to carry a claim and every other field is ``None``, so a renderer cannot mistake
    "we have not looked long enough" for "it is €0 above its low".
    """

    observations: int
    span_days: float
    enough_history: bool
    low: float | None = None
    median: float | None = None
    high: float | None = None
    is_lowest: bool | None = None
    above_low: float | None = None  # ≥ 0
    above_median: float | None = None  # signed: negative means below the usual price
    days_at_price: float | None = None


@dataclass(frozen=True)
class HistoryConfig:
    """Where the history lives, how much of it is kept, and when it may be quoted."""

    path: Path = DEFAULT_HISTORY_PATH
    keep_days: int = DEFAULT_KEEP_DAYS
    max_points: int = DEFAULT_MAX_POINTS
    min_observations: int = DEFAULT_MIN_OBSERVATIONS
    min_span_days: float = DEFAULT_MIN_SPAN_DAYS

    @classmethod
    def from_config(cls, config: dict) -> HistoryConfig:
        """Read the limits from the existing ``monitor:`` block, defaulting silently."""
        block = config.get("monitor") or {}
        return cls(
            path=Path(block.get("price_history_path") or DEFAULT_HISTORY_PATH),
            keep_days=_positive_int(block.get("price_history_days"), DEFAULT_KEEP_DAYS),
            max_points=_positive_int(block.get("price_history_max_points"), DEFAULT_MAX_POINTS),
            min_observations=_positive_int(
                block.get("price_min_observations"), DEFAULT_MIN_OBSERVATIONS
            ),
            min_span_days=_positive_float(block.get("price_min_span_days"), DEFAULT_MIN_SPAN_DAYS),
        )


def _positive_int(value: object, fallback: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _positive_float(value: object, fallback: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def observe(
    products: Iterable[Product],
    wanted_sizes: tuple[str, ...] = (),
    now: datetime | None = None,
    identify: Callable[[Product], tuple[str, str]] | None = None,
) -> list[Observation]:
    """One observation per product per source for this run.

    ``identify`` resolves a product to its ``(boot_key, size)`` — the caller supplies it
    because the caller already holds the hunt and vocabulary the catalogue needs, and this
    keeps the module dependency-light. Without it an observation keys on its URL alone, as
    it always did.

    Two hunts can reach the same boot, and a listing can link it twice with different click
    tracking. Logging each sighting would let a popular product outvote itself and make the
    median a measure of how often we saw it rather than what it cost — so a run keeps one
    observation per ``(boot, source)``. It is deliberately not one per *boot*: two shops
    selling the same boot at two prices are the whole point of a cross-retailer low, and
    collapsing them would throw away the cheaper one.
    """
    stamp = now or datetime.now(UTC)
    latest: dict[tuple[str, str], Observation] = {}
    for product in products:
        url = canonical_url(product.url)
        key, size = identify(product) if identify else ("", "")
        observation = Observation(
            url=url,
            price=product.price,
            at=stamp,
            source=product.source,
            in_stock=in_stock(product, wanted_sizes),
            boot_key=key,
            size=size,
        )
        # Dedupe within the run on (boot-or-listing, source): the same boot at two shops is
        # two observations, the same listing linked twice is one.
        latest[(observation.key, product.source)] = observation
    return list(latest.values())


def load_history(path: Path = DEFAULT_HISTORY_PATH) -> dict[str, list[Observation]]:
    """Read the log into per-product observations, oldest first.

    A missing, unreadable or partly corrupt file yields no opinion rather than an error:
    the history is an optimisation, and a run that cannot read it must still run.
    """
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("price history unreadable (%s) — no price opinion this run", exc)
        return {}

    history: dict[str, list[Observation]] = {}
    skipped = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        observation = Observation.from_dict(data) if isinstance(data, dict) else None
        if observation is None:
            skipped += 1
            continue
        history.setdefault(observation.key, []).append(observation)

    if skipped:
        logger.warning("price history: skipped %d unreadable line(s)", skipped)
    for observations in history.values():
        observations.sort(key=lambda o: o.at)
    return history


def append(observations: Sequence[Observation], path: Path = DEFAULT_HISTORY_PATH) -> Path:
    """Append this run's observations to the log, creating the directory if needed."""
    if not observations:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for observation in observations:
            fh.write(observation.to_json() + "\n")
    logger.info("price history: +%d observation(s) -> %s", len(observations), path)
    return path


def extend(
    history: dict[str, list[Observation]], observations: Sequence[Observation]
) -> dict[str, list[Observation]]:
    """Return a NEW history with these observations folded in (input untouched).

    Lets a run read its own fresh observation, so "cheapest seen" is a claim about the
    price in front of the reader rather than about the previous run's.
    """
    merged = {key: list(points) for key, points in history.items()}
    for observation in observations:
        merged.setdefault(observation.key, []).append(observation)
    for points in merged.values():
        points.sort(key=lambda o: o.at)
    return merged


def prune(
    history: dict[str, list[Observation]],
    keep_days: int = DEFAULT_KEEP_DAYS,
    max_points: int = DEFAULT_MAX_POINTS,
    now: datetime | None = None,
) -> dict[str, list[Observation]]:
    """Bound the log by age and by observations per product, newest kept.

    Twice-daily runs over a thousand products would otherwise write three quarters of a
    million lines a year, and the oldest of them describe a boot nobody now sells.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=max(1, keep_days))
    cap = max(1, max_points)
    kept: dict[str, list[Observation]] = {}
    for url, points in history.items():
        fresh = [p for p in points if p.at >= cutoff]
        if fresh:
            kept[url] = fresh[-cap:]
    dropped = sum(len(p) for p in history.values()) - sum(len(p) for p in kept.values())
    if dropped:
        logger.info("price history: pruned %d observation(s)", dropped)
    return kept


def rewrite(history: dict[str, list[Observation]], path: Path = DEFAULT_HISTORY_PATH) -> Path:
    """Replace the log with exactly this history, in chronological order.

    Appending is what makes a run's observation durable even if the run later fails;
    rewriting is how the log is bounded. Doing both means a crash costs an unpruned file
    rather than a lost observation.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    points = sorted((p for group in history.values() for p in group), key=lambda o: (o.at, o.url))
    path.write_text("".join(p.to_json() + "\n" for p in points), encoding="utf-8")
    logger.info("price history: %d observation(s) -> %s", len(points), path)
    return path


def summarise(
    price: float,
    observations: Sequence[Observation],
    now: datetime | None = None,
    min_observations: int = DEFAULT_MIN_OBSERVATIONS,
    min_span_days: float = DEFAULT_MIN_SPAN_DAYS,
) -> PriceMemory:
    """Read a product's history as facts about ``price`` — or decline to.

    Pure. Below either floor the result carries ``enough_history=False`` and no claims,
    because "we have seen this twice, an hour apart" cannot distinguish a genuine low from
    the only two numbers we happen to have.
    """
    points = sorted(observations, key=lambda o: o.at)
    if not points:
        return PriceMemory(observations=0, span_days=0.0, enough_history=False)

    span_days = (points[-1].at - points[0].at).total_seconds() / 86400.0
    if len(points) < max(1, min_observations) or span_days < min_span_days:
        return PriceMemory(observations=len(points), span_days=span_days, enough_history=False)

    prices = [p.price for p in points]
    low, mid, high = min(prices), float(median(prices)), max(prices)
    return PriceMemory(
        observations=len(points),
        span_days=span_days,
        enough_history=True,
        low=low,
        median=mid,
        high=high,
        is_lowest=price <= low + _CENT,
        above_low=max(0.0, price - low),
        above_median=price - mid,
        days_at_price=_days_at_price(price, points, now),
    )


def _days_at_price(price: float, points: Sequence[Observation], now: datetime | None) -> float:
    """How long this price has held, walking back while the log agrees with it.

    Zero when the newest observation disagrees — the price has only just moved, which is
    news of a different kind and belongs to the monitor, not here.
    """
    moment = now or datetime.now(UTC)
    since: datetime | None = None
    for observation in reversed(points):
        if abs(observation.price - price) > _CENT:
            break
        since = observation.at
    if since is None:
        return 0.0
    return max(0.0, (moment - since).total_seconds() / 86400.0)


def summarise_all(
    products: Iterable[Product],
    history: dict[str, list[Observation]],
    now: datetime | None = None,
    limits: HistoryConfig | None = None,
    identify: Callable[[Product], tuple[str, str]] | None = None,
) -> dict[str, PriceMemory]:
    """Memory for each product, keyed on the tracking-stripped URL the renderer will use.

    The *lookup* into ``history`` is by boot identity when the caller supplies ``identify``,
    so a boot's memory draws on every retailer that has ever sold it — but the returned
    dict stays keyed on the URL, because that is what the renderer holds for each row. An
    unclassified boot (empty ``boot_key``) falls back to its own URL, exactly as before.
    """
    limits = limits or HistoryConfig()
    memories: dict[str, PriceMemory] = {}
    for product in products:
        url = canonical_url(product.url)
        boot = (identify(product)[0] if identify else "") or url
        memories[url] = summarise(
            product.price,
            history.get(boot, ()),
            now,
            limits.min_observations,
            limits.min_span_days,
        )
    return memories

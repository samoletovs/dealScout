"""Data models for dealScout."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class LogoPolicy(str, Enum):
    """How tolerant the owner is of visible branding."""

    NONE = "none"
    TONAL_ONLY = "tonal_only"
    SMALL_OK = "small_ok"
    ANY = "any"


@dataclass(frozen=True)
class WatchItem:
    """An item the owner wants tracked."""

    url: str
    category: str
    note: str = ""


@dataclass(frozen=True)
class Product:
    """A snapshot of a product as seen on a page or feed."""

    title: str
    category: str
    price: float
    reference_price: float | None  # normal/RRP, for discount calculation
    currency: str
    url: str
    materials: dict[str, float] = field(default_factory=dict)  # e.g. {"wool": 1.0}
    has_big_logo: bool = False
    quality_signals: frozenset[str] = frozenset()
    care: str = ""  # e.g. "machine wash" / "dry clean only"
    brand: str = ""  # used for brand-tier filtering
    source: str = ""  # store/seller (from Shopping) — for grouping + marketplace filtering
    condition: str = "new"  # "new" | "used" | "refurbished" | "pre-owned" (from Shopping)
    attrs: dict[str, str] = field(default_factory=dict)  # extracted spec, e.g. {"soleplate": "AG"}
    sizes: frozenset[str] = frozenset()  # in-stock sizes, normalised (e.g. {"37", "37.5"})
    sizes_known: bool = False  # False = the page never told us; don't infer "out of stock"


@dataclass(frozen=True)
class Verdict:
    """The judge's decision about a product."""

    is_deal: bool
    score: float
    reasons: tuple[str, ...]
    band: str = "reject"  # must-buy | good | regular | reject


@dataclass(frozen=True)
class Feedback:
    """A user's 👍/👎 reaction to a surfaced deal, read back from an email reply."""

    url: str
    verdict: str  # "up" | "down"
    when: str = ""  # ISO date, if known


@dataclass(frozen=True)
class Change:
    """What the monitor noticed about a product since the previous run."""

    product: Product
    kind: str  # "new" | "price-drop" | "back-in-stock" | "seen"
    previous_price: float | None = None

    @property
    def is_news(self) -> bool:
        """True when this is worth emailing about (i.e. not just still-there)."""
        return self.kind != "seen"


@dataclass(frozen=True)
class SaleEvent:
    """A sale announced in a brand newsletter (brand-level, not per-item)."""

    brand: str
    headline: str
    max_discount_pct: float
    categories: tuple[str, ...]
    url: str
    source: str = ""  # sender address / domain


def _tuple(value: object) -> tuple[str, ...]:
    """Coerce a scalar or list from YAML into a tuple of trimmed strings."""
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    text = str(value).strip()
    return (text,) if text else ()


def _price(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Hunt:
    """One declarative search: what to find, for whom, and what makes it a deal.

    A hunt is the unit of repeatability. "Elite football boots, EU 37, under €100"
    and "running shoes for me" are two hunts over the same engine — the difference
    is entirely config, never code.

    Requirements are matched against ``Product.attrs`` (see ``spec.extract_attrs``).
    An attribute the page never stated is *unknown*, not *failed*: the product is
    still surfaced but flagged unverified and capped below "must-buy", so a human
    confirms on click. That keeps the engine a co-pilot rather than a gatekeeper.
    """

    enabled: bool = True
    id: str = ""
    label: str = ""
    category: str = ""
    for_whom: str = ""
    currency: str = "EUR"
    deliver_to: str = ""
    sizes: tuple[str, ...] = ()  # acceptable sizes, normalised (e.g. ("37", "37.5"))
    brands: tuple[str, ...] = ()  # ranked, most-wanted first — drives preference scoring
    require: dict[str, tuple[str, ...]] = field(default_factory=dict)  # attr -> allowed values
    prefer: dict[str, tuple[str, ...]] = field(default_factory=dict)  # attr -> ranked values
    exclude_models: tuple[str, ...] = ()  # substring match on title — e.g. one already owned
    exclude_urls: tuple[str, ...] = ()
    exclude_sources: tuple[str, ...] = ()
    must_buy: float | None = None  # 🟢 grab instantly
    good_offer: float | None = None  # 🟡 worth it
    never_above: float | None = None  # ⛔ hard reject
    min_reference_price: float | None = None  # RRP gate — proves it is a flagship, not a lookalike
    min_discount_pct: float = 0.0
    require_size_in_stock: bool = True
    require_new: bool = True
    queries: tuple[str, ...] = ()  # search strings for the scout
    watch: tuple[str, ...] = ()  # listing/product URLs to poll directly
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> Hunt:
        """Build a Hunt from a YAML mapping, tolerating scalars where lists are allowed."""
        price = data.get("price") or {}
        exclude = data.get("exclude") or {}
        return cls(
            enabled=bool(data.get("enabled", True)),
            id=str(data.get("id") or "").strip(),
            label=str(data.get("label") or "").strip(),
            category=str(data.get("category") or "").strip(),
            for_whom=str(data.get("for") or data.get("for_whom") or "").strip(),
            currency=str(data.get("currency") or "EUR").strip(),
            deliver_to=str(data.get("deliver_to") or "").strip(),
            sizes=_tuple(data.get("sizes")),
            brands=_tuple(data.get("brands")),
            require={k: _tuple(v) for k, v in (data.get("require") or {}).items()},
            prefer={k: _tuple(v) for k, v in (data.get("prefer") or {}).items()},
            exclude_models=_tuple(exclude.get("models")),
            exclude_urls=_tuple(exclude.get("urls")),
            exclude_sources=_tuple(exclude.get("sources")),
            must_buy=_price(price.get("must_buy")),
            good_offer=_price(price.get("good_offer")),
            never_above=_price(price.get("never_above")),
            min_reference_price=_price(price.get("min_reference_price")),
            min_discount_pct=float(price.get("min_discount_pct") or 0),
            require_size_in_stock=bool(data.get("require_size_in_stock", True)),
            require_new=bool(data.get("require_new", True)),
            queries=_tuple(data.get("queries")),
            watch=_tuple(data.get("watch")),
            notes=str(data.get("notes") or "").strip(),
        )

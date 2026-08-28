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
    # Product image URLs as the feed served them, most-representative first. These come from
    # the *same* payload we already read for price/size/stock (Shopify `/products.json`), so
    # keeping them costs no extra request. Retrieval is not a licence to republish: a URL
    # here is fit for an internal design spike, and for public display only once an affiliate
    # (or other) licence covers the image. `image_seen_at` is the ISO-8601 UTC instant the
    # feed served these URLs — merchant CDNs rotate image URLs (Shopify appends a `?v=`
    # cache-buster), so a stored link is only trustworthy relative to when it was last seen.
    images: tuple[str, ...] = ()
    image_seen_at: str = ""  # ISO-8601 UTC, e.g. "2026-08-28T14:03:11Z"; "" = never captured


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
    # Per-brand overrides, because a size is a brand's opinion rather than a measurement.
    # adidas sizes in thirds and its EU 37 is 37⅓; Nike's equivalent for the same foot is
    # 37.5 and it makes no thirds at all. Searching both lists against both brands surfaces
    # boots that will not fit and hides ones that will, so the brand picks the list.
    # Falls back to `sizes` for a brand not named here.
    sizes_by_brand: dict[str, tuple[str, ...]] = field(default_factory=dict)
    brands: tuple[str, ...] = ()  # ranked, most-wanted first — drives preference scoring
    require: dict[str, tuple[str, ...]] = field(default_factory=dict)  # attr -> allowed values
    prefer: dict[str, tuple[str, ...]] = field(default_factory=dict)  # attr -> ranked values
    exclude_models: tuple[str, ...] = ()  # substring match on title — e.g. one already owned
    exclude_urls: tuple[str, ...] = ()
    exclude_sources: tuple[str, ...] = ()
    # Attributes where "the page didn't say" means reject, not flag. Use for anything read
    # only from the title: no amount of clicking will make a title state a tier it omits,
    # so flagging it would surface the same unresolvable candidate on every future run.
    require_stated: tuple[str, ...] = ()
    must_buy: float | None = None  # 🟢 grab instantly
    good_offer: float | None = None  # 🟡 worth it
    never_above: float | None = None  # ⛔ hard reject
    min_reference_price: float | None = None  # RRP gate — proves it is a flagship, not a lookalike
    min_discount_pct: float = 0.0
    require_size_in_stock: bool = True
    require_new: bool = True
    brands_only: bool = False  # when True, `brands` is a hard filter, not just a ranking
    queries: tuple[str, ...] = ()  # search strings for the scout
    watch: tuple[str, ...] = ()  # listing/product URLs to poll directly
    # Magento storefronts read through their own GraphQL API, for shops that render
    # nothing server-side. Each entry: {sitemap, graphql, origin, match}. See magento.py.
    catalogs: tuple[dict, ...] = ()
    notes: str = ""

    def sizes_for(self, product_brand: str, product_title: str = "") -> tuple[str, ...]:
        """The sizes wanted *for this brand* — falling back to the hunt's default list.

        A shoe size is a brand's opinion, not a measurement: adidas EU 37 is printed 37⅓,
        while Nike's equivalent for the same foot is 37.5 and Nike makes no thirds at all.
        Matching both lists against both brands both surfaces boots that will not fit and,
        worse, buries the ones that will.

        The brand is read from the product's brand field first and its title second, since
        a single-brand retailer often names neither in the title but does set the brand.
        """
        if not self.sizes_by_brand:
            return self.sizes
        haystack = f"{product_brand} {product_title}".lower()
        for brand, sizes in self.sizes_by_brand.items():
            if brand and brand in haystack:
                return sizes or self.sizes
        return self.sizes

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
            sizes_by_brand={
                str(k).strip().lower(): _tuple(v)
                for k, v in (data.get("sizes_by_brand") or {}).items()
            },
            brands=_tuple(data.get("brands")),
            require={k: _tuple(v) for k, v in (data.get("require") or {}).items()},
            require_stated=_tuple(data.get("require_stated")),
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
            brands_only=bool(data.get("brands_only", False)),
            queries=_tuple(data.get("queries")),
            watch=_tuple(data.get("watch")),
            catalogs=tuple(c for c in (data.get("catalogs") or []) if isinstance(c, dict)),
            notes=str(data.get("notes") or "").strip(),
        )

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


@dataclass(frozen=True)
class Verdict:
    """The judge's decision about a product."""

    is_deal: bool
    score: float
    reasons: tuple[str, ...]

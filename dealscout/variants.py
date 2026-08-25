"""Read per-size stock out of a product page's embedded variant payload.

schema.org ld+json is the happy path, but plenty of retailers publish a single Offer
for the whole product and keep the per-size truth in a JSON blob that hydrates the size
picker. Without that blob a hunt cannot answer the only question that decides a purchase
— "does it exist in EU 37?" — so every candidate comes back "verify on click" and the
human ends up doing the job the tool exists to do.

The extractor is deliberately *structural* rather than per-retailer: find a JSON array
whose objects carry both a size label and an availability field, and read it. That shape
is common across store platforms, so a new retailer usually needs no new code.

Pure and side-effect free.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from .spec import looks_like_eu, normalise_size

logger = logging.getLogger(__name__)

# Keys whose value is plausibly an array of per-size variants. Restricting to a known
# set keeps us from parsing every array on a 1 MB page.
VARIANT_KEYS = (
    "stock",
    "variants",
    "variant",
    "skus",
    "sizes",
    "size_options",
    "options",
    "inventory",
)

_SIZE_FIELDS = ("name_short", "size_name", "size", "name", "label", "title", "value")
_STOCK_FIELDS = (
    "availability",
    "available",
    "is_available",
    "in_stock",
    "inStock",
    "stock_status",
    "stock",
    "quantity",
    "qty",
)
_RRP_FIELDS = (
    "recommended_retail_price",
    "rrp",
    "list_price",
    "compare_at_price",
    "old_price",
    "was_price",
    "regular_price",
    "original_price",
)
_PRICE_FIELDS = ("price", "sale_price", "current_price", "final_price")

_OUT_WORDS = ("out of stock", "outofstock", "sold out", "soldout", "unavailable", "discontinued")
_IN_WORDS = ("in stock", "instock", "available", "limited", "backorder", "preorder")


@dataclass(frozen=True)
class SizeStock:
    """Per-size availability read off a product page."""

    sizes: frozenset[str] = frozenset()  # normalised sizes that can be bought now
    known: bool = False  # did the page state its sizes at all?
    reference_price: float | None = None  # highest RRP seen across the variants

    @property
    def is_empty(self) -> bool:
        return not self.known


def _to_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _first(node: dict, fields: tuple[str, ...]) -> object:
    """First present, non-empty value among ``fields``."""
    for field in fields:
        if field in node and node[field] not in (None, ""):
            return node[field]
    return None


def in_stock(value: object) -> bool | None:
    """Interpret an availability value (None when it says nothing intelligible)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    text = str(value or "").strip().lower()
    if not text:
        return None
    if any(word in text for word in _OUT_WORDS):
        return False
    if any(word in text for word in _IN_WORDS):
        return True
    return None


def _deescape(html: str) -> str:
    """Turn embedded escaped JSON (``\\"stock\\":[...]``) back into parseable JSON."""
    return html.replace('\\"', '"').replace("\\/", "/")


def _balanced(text: str, start: int) -> str | None:
    """The balanced ``[...]``/``{...}`` beginning at ``start``, or None if unterminated."""
    opener = text[start]
    closer = {"[": "]", "{": "}"}.get(opener)
    if closer is None:
        return None
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def find_variant_arrays(text: str, keys: tuple[str, ...] = VARIANT_KEYS) -> list[list[dict]]:
    """Every parseable ``"<key>": [ {...}, ... ]`` array of objects in ``text``."""
    found: list[list[dict]] = []
    for key in keys:
        for match in re.finditer(rf'"{re.escape(key)}"\s*:\s*(?=\[)', text):
            blob = _balanced(text, match.end())
            if not blob:
                continue
            try:
                data = json.loads(blob)
            except (json.JSONDecodeError, ValueError):
                continue
            rows = [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
            if rows:
                found.append(rows)
    return found


def read_variants(rows: list[dict]) -> SizeStock:
    """Read one candidate variant array into per-size stock (empty if it isn't one)."""
    available: set[str] = set()
    labelled: list[str] = []
    rrps: list[float] = []

    for row in rows:
        size = normalise_size(_first(row, _SIZE_FIELDS))
        if not size:
            continue
        state = in_stock(_first(row, _STOCK_FIELDS))
        if state is None:
            # A row with a size but nothing about stock is not a variant table we can
            # trust; counting it would invent availability.
            continue
        labelled.append(size)
        if state:
            available.add(size)
        rrp = _to_float(_first(row, _RRP_FIELDS))
        price = _to_float(_first(row, _PRICE_FIELDS))
        if rrp and (price is None or rrp > price):
            rrps.append(rrp)

    if not labelled or not looks_like_eu(labelled):
        # Either no size rows at all, or sizes stated in a system we cannot compare
        # against a hunt's EU sizes (see spec.looks_like_eu). Both mean "unknown".
        return SizeStock()
    return SizeStock(frozenset(available), True, max(rrps) if rrps else None)


def extract_size_stock(html: str) -> SizeStock:
    """Best per-size stock reading for a product page (empty when the page is silent).

    Prefers the array that labels the most sizes: a page often carries several arrays and
    the richest one is the size picker.
    """
    best = SizeStock()
    for rows in find_variant_arrays(_deescape(html)):
        found = read_variants(rows)
        if found.known and len(found.sizes) >= len(best.sizes) and not best.known:
            best = found
        elif found.known and len(found.sizes) > len(best.sizes):
            best = found
    return best

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
from html import unescape

from .sizeconvert import US, us_to_eu
from .spec import is_eu_size, looks_like_eu, normalise_size

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


# Magento 2's swatch renderer hydrates the size picker from a ``jsonConfig`` blob whose
# shape is fixed across the platform: an ``attributes`` map keyed by attribute id, each
# entry carrying a ``code`` (``"size"``), a list of ``options``, and — per option — a
# ``products`` array. That array is the retailer's own statement of stock: **empty means
# that size is not available, non-empty means it is**. teamsport.lv (Nike's Latvian
# distributor) is the case in hand.
#
# The catch is that teamsport prints those option labels in **US** sizes, under a selector
# its markup labels ``US izmēri``. Read as EU they are impossible; read as US they are a
# Nike men's ladder. So the labels are converted to EU here, but only when the page states
# the system it is using — inferring US vs UK would risk a confident wrong answer about
# whether a boot fits (see dealscout.sizeconvert).
_SIZE_SYSTEM_MARKERS = {
    # substring found on the page -> (system, brand whose conversion ladder to use)
    "us izmēri": "us",
    "us izmeri": "us",  # in case the page is served without Latvian diacritics
}
_JSONCONFIG_RE = re.compile(r'"jsonConfig"\s*:\s*(?=\{)')

# Nike prints youth sizes with a trailing ``Y`` on this same shop, under the same
# ``US izmēri`` label (e.g. the Star Runner 4 GS lists ``3,5Y  5,5Y  6,5Y``). Youth US and
# men's US are *different* ladders that diverge exactly where it matters: youth ``5.5Y`` is
# ~EU 37.5 but men's ``5.5`` is EU 38 — and EU 37.5 is the owner's son's size. Reading a
# youth label on the men's table would be a confident wrong answer at precisely that size.
# We only carry the men's ladder, so a youth-marked label must be refused explicitly here,
# not left to be dropped by accident because ``normalise_size`` happens to reject a suffix.
_YOUTH_SIZE_RE = re.compile(r"\d\s*(?:y|c|k)\b", re.IGNORECASE)


def _is_youth_size(label: object) -> bool:
    """True if a size label is Nike's youth/child ladder (a ``Y``/``C``/``K`` suffix).

    These share teamsport's ``US izmēri`` label with men's sizes but are a distinct ladder
    this engine does not carry, so they must never be placed on the men's one.
    """
    return bool(_YOUTH_SIZE_RE.search(str(label or "")))


def _declared_size_system(html: str) -> str | None:
    """The size system the page states it is using ('us'), or None if it states none.

    teamsport renders ``<div class="size-additional-info">US izmēri</div>`` beside the size
    swatch. That label is the shop's own statement, and it is the only thing that makes the
    US numbers safe to convert: without it, a bare ``9`` could be US or UK.
    """
    low = html.lower()
    for marker, system in _SIZE_SYSTEM_MARKERS.items():
        if marker in low:
            return system
    return None


def read_magento_swatch(html: str, brand: str = "nike") -> SizeStock:
    """Per-size stock from a Magento ``jsonConfig`` swatch blob, converting US -> EU.

    Returns empty (``known=False``) unless *both* hold: the page names its size system
    (so the numbers can be trusted as US), and that system has a recorded conversion for
    ``brand``. A US size the ladder cannot place is dropped rather than guessed, so a boot
    is never reported present in a size it isn't.
    """
    system = _declared_size_system(html)
    if system != US:
        # Either the page states no system, or one we have no ladder for. Reading the raw
        # numbers as EU would be the confident wrong answer this engine refuses to give.
        return SizeStock()

    match = _JSONCONFIG_RE.search(html)
    if not match:
        return SizeStock()
    blob = _balanced(html, match.end())
    if not blob:
        return SizeStock()
    try:
        config = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return SizeStock()

    available: set[str] = set()
    placed: list[str] = []  # US labels we could convert — the ones we can actually judge
    for attribute in (config.get("attributes") or {}).values():
        if not isinstance(attribute, dict):
            continue
        if "size" not in str(attribute.get("code") or "").lower():
            continue
        for option in attribute.get("options") or []:
            if not isinstance(option, dict):
                continue
            label = option.get("label")
            if _is_youth_size(label):
                # A youth ladder shares the ``US izmēri`` label with men's but converts
                # differently, and we carry only the men's table. Refuse the whole swatch
                # rather than place some sizes and silently drop the youth ones: a partial
                # men's reading of a youth boot is the wrong answer at the son's size.
                return SizeStock()
            eu = us_to_eu(label, brand)
            if not eu:
                # A label outside the recorded ladder (or not a size at all): dropping it
                # is safer than inventing a nearest EU size.
                continue
            placed.append(eu)
            # In Magento's swatch config a size with no purchasable variant carries an
            # empty ``products`` array; a non-empty one lists the buyable simple product(s).
            if option.get("products"):
                available.add(eu)

    if not placed:
        return SizeStock()
    return SizeStock(frozenset(available), True, None)


def extract_size_stock(html: str) -> SizeStock:
    """Best per-size stock reading for a product page (empty when the page is silent).

    Tries the embedded JSON payload first, then a rendered size ``<select>``. Prefers the
    reading that labels the most sizes: a page often carries several, and the richest one
    is the size picker.
    """
    # A Magento swatch blob is checked first: teamsport's states US sizes the generic
    # readers below would (correctly) reject as non-EU, so without this the shop yields
    # nothing. It self-guards to the declared-system case, so it is silent elsewhere.
    swatch = read_magento_swatch(html)
    if swatch.known:
        return swatch

    best = SizeStock()
    for rows in find_variant_arrays(_deescape(html)):
        found = read_variants(rows)
        if found.known and (not best.known or len(found.sizes) > len(best.sizes)):
            best = found
    if best.known:
        return best
    found = read_select_options(html)
    return found if found.known else read_size_boxes(html)


# A size picker rendered as radio "boxes" rather than a <select>. The OpenCart vs-design
# theme (futbola-apavi.lv and its sibling storefronts) is the case in hand:
#   <label class="size-box"><input ... data-qty="50" ...><p>39</p></label>
# The size is the label's own text, and the quantity rides on the input.
_SIZE_BOX_RE = re.compile(
    r'<label\b([^>]*\bclass="[^"]*size-box[^"]*"[^>]*)>(.*?)</label>',
    re.IGNORECASE | re.DOTALL,
)
_QTY_RE = re.compile(r'data-qty\s*=\s*"(\d+)"', re.IGNORECASE)


def read_size_boxes(html: str) -> SizeStock:
    """Per-size stock from a size picker rendered as labelled radio boxes.

    Measured on futbola-apavi.lv (2026-08-26): the storefront renders a box **only** for a
    size it can actually sell — a boot down to its last pair shows a single box carrying
    ``data-qty="1"`` — so a rendered box means buyable unless it says otherwise. Both
    ``data-qty="0"`` and the usual disabled markers are still honoured, because relying on
    omission alone would turn a theme change into a silent false positive.
    """
    available: set[str] = set()
    labelled: list[str] = []
    for attrs, inner in _SIZE_BOX_RE.findall(html):
        text = unescape(_TAG_RE.sub(" ", inner)).strip()
        size = normalise_size(text)
        if not is_eu_size(size):
            continue
        labelled.append(size)
        quantity = _QTY_RE.search(inner) or _QTY_RE.search(attrs)
        if quantity and int(quantity.group(1)) == 0:
            continue
        if _option_available(attrs, text):
            available.add(size)
    if not labelled or not looks_like_eu(labelled):
        return SizeStock()
    return SizeStock(frozenset(available), True, None)


_SELECT_RE = re.compile(r"<select\b[^>]*>(.*?)</select>", re.IGNORECASE | re.DOTALL)
_OPTION_RE = re.compile(r"<option\b([^>]*)>(.*?)</option>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

# Markers a storefront puts on an option you cannot actually buy.
_DISABLED_CLASSES = ("greyout", "grey-out", "disabled", "unavailable", "soldout", "sold-out")


def _option_available(attrs: str, label: str) -> bool:
    low = attrs.lower()
    if "disabled" in low:
        return False
    if any(marker in low.replace("_", "") for marker in _DISABLED_CLASSES):
        return False
    quantity = re.search(r'data-stock-qty\s*=\s*"(\d+)"', low)
    if quantity and int(quantity.group(1)) == 0:
        return False
    return in_stock(label) is not False


def read_select_options(html: str) -> SizeStock:
    """Per-size stock from a rendered size ``<select>``.

    Sports Direct and other Frasers storefronts publish no per-size JSON at all; the only
    statement of what is buyable is the dropdown, where an unavailable size is greyed out
    and carries ``data-stock-qty="0"``. Reading it is the difference between "we don't
    know" and "that boot does not exist in your size".
    """
    best = SizeStock()
    for block in _SELECT_RE.findall(html):
        available: set[str] = set()
        labelled: list[str] = []
        for attrs, inner in _OPTION_RE.findall(block):
            value = re.search(r'value\s*=\s*"([^"]*)"', attrs)
            text = unescape(_TAG_RE.sub(" ", inner)).strip()
            size = normalise_size(unescape(value.group(1)) if value else "") or normalise_size(text)
            if not is_eu_size(size):
                continue  # a placeholder like "Please choose" (value="0"), or a UK size
            labelled.append(size)
            if _option_available(attrs, text):
                available.add(size)
        if not labelled or not looks_like_eu(labelled):
            continue  # a quantity or colour picker, not sizes
        if not best.known or len(labelled) > len(best.sizes):
            best = SizeStock(frozenset(available), True, None)
    return best

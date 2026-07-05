"""Parse brand newsletters into SaleEvents and judge them for the digest.

Newsletters are the anti-bot-proof sale signal: brands email us the discount.
We extract a brand-level SaleEvent (brand, max discount, categories, link) and
judge it by brand tier + discount depth into a digest band. Pure functions, so
they are easy to unit-test without a live inbox.
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from .judge import brand_tier
from .models import SaleEvent

logger = logging.getLogger(__name__)

_DISCOUNT_RE = re.compile(r"(\d{1,2})\s*%")
_SALE_WORDS = ("sale", "off", "discount", "outlet", "clearance")
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "tee": ("t-shirt", "tee", "polo"),
    "shirt": ("shirt",),
    "knitwear": ("knit", "jumper", "sweater", "cardigan", "pullover"),
    "trousers": ("trouser", "chino", "pants", "jeans"),
    "jacket": ("jacket", "blazer"),
    "outerwear": ("coat", "parka", "overcoat", "outerwear"),
    "shoes": ("shoe", "sneaker", "trainer", "boot", "loafer"),
    "accessory": ("belt", "wallet", "bag", "scarf", "sock"),
}


def _brand_from_sender(sender: str) -> str:
    """Best-effort brand from a sender like 'BOSS <news@hugoboss.com>'."""
    name = sender.split("<")[0].strip().strip('"')
    if name:
        return name
    match = re.search(r"@([\w.-]+)", sender)
    if match:
        return match.group(1).split(".")[0]
    return sender.strip()


def _first_link(body_html: str) -> str:
    if not body_html:
        return ""
    anchor = BeautifulSoup(body_html, "html.parser").find("a", href=True)
    return anchor["href"] if anchor else ""


def parse_newsletter(sender: str, subject: str, body_html: str) -> SaleEvent | None:
    """Extract a SaleEvent from a newsletter email, or None if it is not a sale."""
    text = BeautifulSoup(body_html or "", "html.parser").get_text(" ", strip=True)
    hay = f"{subject} {text}"
    low = hay.lower()

    discounts = [int(m) for m in _DISCOUNT_RE.findall(hay) if 0 < int(m) <= 90]
    max_discount = float(max(discounts)) if discounts else 0.0

    if max_discount == 0.0 and not any(word in low for word in _SALE_WORDS):
        return None

    categories = tuple(
        cat for cat, kws in _CATEGORY_KEYWORDS.items() if any(k in low for k in kws)
    )
    return SaleEvent(
        brand=_brand_from_sender(sender),
        headline=subject.strip(),
        max_discount_pct=max_discount,
        categories=categories,
        url=_first_link(body_html),
        source=sender,
    )


def event_band(event: SaleEvent, config: dict) -> str:
    """Digest band for a sale event: 'must-look' / 'good' / 'skip'.

    Rejects below-tier brands, then ranks by discount depth.
    """
    tier = brand_tier(event.brand, config.get("brands", {}))
    min_tier = config.get("filters", {}).get("min_brand_tier", "any")
    if min_tier == "basket" and tier == "worse":
        return "skip"
    if min_tier == "better" and tier != "better":
        return "skip"

    if event.max_discount_pct >= 50:
        return "must-look"
    if event.max_discount_pct >= 30:
        return "good"
    return "skip"

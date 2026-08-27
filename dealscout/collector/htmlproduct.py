"""HTML product-page parsing — reading a page that publishes no ld+json Product at all.

Sports Direct is the archetype: the page carries only a breadcrumb blob, and the price
and per-size stock exist purely as rendered HTML. The regex tables here are ordered
best-first, so a machine-readable price always beats a rendered one.

``_money`` — reading a rendered price string as a float — lives here because it is the
"rendered HTML price" concern, and the tile reader imports it for the same job.
"""

from __future__ import annotations

import logging
import re
from html import unescape
from urllib.parse import urlsplit

from ..models import Product
from ..variants import extract_size_stock

logger = logging.getLogger(__name__)

_TAG_STRIP_RE = re.compile(r"<[^>]+>")

# Ordered best-first: an explicit machine-readable price beats a rendered one.
_PRICE_PATTERNS = (
    r'itemprop="price"[^>]*content="([\d.,]+)"',
    r'property="product:price:amount"[^>]*content="([\d.,]+)"',
    r'id="lblSellingPrice"[^>]*>\s*([^<]+?)<',
    r'class="[^"]*(?:curPrice|sellingPrice|price--current)[^"]*"[^>]*>\s*([^<]+?)<',
    # futbola-apavi.lv (OpenCart) renders `<div class="price"><span class="new">59,99€
    # </span><span class="old">69,99€</span></div>`. Matched on the exact class — a
    # wildcard on "new" would also catch `class="news"` — and listed last, so it only
    # applies when no machine-readable price was published at all.
    r'class="new"[^>]*>\s*([^<]+?)<',
)
_RRP_PATTERNS = (
    r'id="lblTicketPrice"[^>]*>\s*([^<]+?)<',
    r'class="[^"]*(?:ticketPrice|wasPrice|price--was|rrp)[^"]*"[^>]*>\s*([^<]+?)<',
    r'class="old"[^>]*>\s*([^<]+?)<',
)
_MONEY_RE = re.compile(r"(\d{1,6}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)")


def _money(text: object) -> float | None:
    """Read a rendered price ('167,39 €', '1 234.50') as a float."""
    match = _MONEY_RE.search(unescape(str(text or "")).replace("\xa0", " ").replace(" ", ""))
    if not match:
        return None
    raw = match.group(1)
    # Whichever separator comes last is the decimal one.
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".") if len(raw.split(",")[-1]) <= 2 else raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _first_match(html: str, patterns: tuple[str, ...]) -> float | None:
    for pattern in patterns:
        for found in re.findall(pattern, html, re.IGNORECASE):
            value = _money(found)
            if value:
                return value
    return None


def parse_html_product(html: str, url: str, category: str) -> Product | None:
    """Read a product page that publishes no ld+json Product at all.

    Sports Direct — the owner's own known-good retailer — is one of these: the page
    carries only a breadcrumb blob, and the price and per-size stock exist purely as
    rendered HTML. Returns None rather than a half-product when there is no price.
    """
    price = _first_match(html, _PRICE_PATTERNS)
    if price is None:
        return None

    title = ""
    og_title = re.search(r'property="og:title"[^>]*content="([^"]*)"', html, re.IGNORECASE)
    if og_title:
        title = unescape(og_title.group(1)).strip()
    if not title:
        page_title = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = unescape(_TAG_STRIP_RE.sub(" ", page_title.group(1))).strip() if page_title else ""

    reference = _first_match(html, _RRP_PATTERNS)
    stock = extract_size_stock(html)
    return Product(
        title=title or url,
        category=category,
        price=price,
        reference_price=reference if reference and reference > price else None,
        currency="EUR",
        url=url,
        source=urlsplit(url).netloc.removeprefix("www."),
        sizes=stock.sizes,
        sizes_known=stock.known,
    )

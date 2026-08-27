"""Link discovery from a listing page that publishes no structured data.

A last resort for retailers with neither Product nor ItemList ld+json: recover the
``(name, URL)`` pairs a listing links to, so a caller can pre-filter by title before
spending a request on each product page. Every OpenCart storefront, for instance, renders
its listing as bare image anchors, so the only name on offer is the URL slug.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin, urlsplit

# A product URL on most storefronts ends in a numeric product id. Good enough to tell a
# product link from navigation, and it is only ever used to *shortlist* pages to read.
_PRODUCT_HREF_RE = re.compile(r'href="([^"#?]*?-(\d{5,})[^"]*)"', re.IGNORECASE)
# Shopware storefronts (11teamsports.com) publish ids nowhere in the URL — a product is
# just `/de-de/p/<slug>`. Without this the id-based matcher above found only the handful
# of products whose colour code happened to contain five digits.
_PRODUCT_PATH_RE = re.compile(r'href="([^"#?]*/p/[a-z0-9][^"]*)"', re.IGNORECASE)
_TILE_BRAND_RE = re.compile(r'productdescriptionbrand"[^>]*>([^<]*)<', re.IGNORECASE)
_TILE_NAME_RE = re.compile(r'productdescriptionname"[^>]*>([^<]*)<', re.IGNORECASE)
# Trailing product id and file extension on an SEO slug: ".../nike-phantom-fg-42559".
# Stripped in two passes because the id sits *before* the extension, so a single
# end-anchored alternation only ever removes whichever one happens to be last.
# The id shape matches _PRODUCT_HREF_RE above, so a 2-3 digit model number like
# "Copa 20" or "F50" survives.
_SLUG_EXT_RE = re.compile(r"\.(?:html?|php|aspx)$", re.IGNORECASE)
_SLUG_ID_RE = re.compile(r"(?:-\d{5,})+$")


def title_from_slug(link: str) -> str:
    """A readable product name recovered from an SEO URL slug ('' when there isn't one).

    The tile regexes above are one retailer's markup. Plenty of shops — every OpenCart
    storefront, for instance — render a listing as bare image anchors with no text, so the
    only name on offer is the slug: ``/nike-tiempo-maestro-club-fg-mg-42559``. A nameless
    link cannot be pre-filtered, and under ``brands_only`` it is not merely unfiltered but
    actively *rejected*, so a whole retailer silently yields nothing. The slug is a good
    enough name for that pre-filter, which only ever reads brand and attribute words.
    """
    slug = urlsplit(link).path.rstrip("/").rsplit("/", 1)[-1]
    slug = _SLUG_ID_RE.sub("", _SLUG_EXT_RE.sub("", slug))
    words = [w for w in re.split(r"[-_]+", slug) if w]
    # A slug of pure digits is an id, not a name; one word is too thin to filter on.
    if len(words) < 2 or all(w.isdigit() for w in words):
        return ""
    return " ".join(words)


def parse_html_links(html: str, url: str) -> list[tuple[str, str]]:
    """Product-shaped anchors on a listing page that publishes no structured data.

    A last resort for retailers with neither Product nor ItemList ld+json. A tile usually
    links to the same product more than once — an image anchor and a text anchor — so
    every occurrence is examined and the first that yields a name wins. Taking only the
    first would return an empty name whenever the image anchor came first, and a nameless
    link cannot be pre-filtered, which is the whole point of reading it.
    """
    titles: dict[str, str] = {}
    order: list[str] = []
    here = urlsplit(url).path.rstrip("/")
    host = urlsplit(url).netloc
    matches = sorted(
        [*_PRODUCT_HREF_RE.finditer(html), *_PRODUCT_PATH_RE.finditer(html)],
        key=lambda m: m.start(),
    )
    for match in matches:
        absolute = urljoin(url, unescape(match.group(1)))
        parts = urlsplit(absolute)
        if parts.netloc != host:
            continue
        path = parts.path.rstrip("/")
        if not path or here.startswith(path):
            continue
        if absolute not in titles:
            titles[absolute] = ""
            order.append(absolute)
        if titles[absolute]:
            continue
        window = html[match.end() : match.end() + 3000]
        brand = _TILE_BRAND_RE.search(window)
        name = _TILE_NAME_RE.search(window)
        titles[absolute] = " ".join(
            unescape(part.group(1)).strip() for part in (brand, name) if part
        ).strip()
    # Fall back to the slug for anything the tile markup left nameless, so a retailer
    # whose listing is pure image anchors still offers something to pre-filter on.
    return [(titles[link] or title_from_slug(link), link) for link in order]

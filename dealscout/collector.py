"""Collector — turns a watch item (URL or feed row) into a Product snapshot.

v1 is a stub. Real implementation should prefer affiliate product feeds; where
none exists, fetch the page and parse schema.org ld+json for price, availability,
and materials. Scrape only the owner's own watch-list pages, at a polite rate.
"""

from __future__ import annotations

import logging

from .models import Product, WatchItem

logger = logging.getLogger(__name__)


async def collect(item: WatchItem) -> Product | None:
    """Fetch a product snapshot for a watch item.

    Returns None if the item cannot be read (out of stock, blocked, etc.).
    TODO: implement feed lookup / ld+json parsing.
    """
    logger.warning("collector.collect is a stub — returning None for %s", item.url)
    return None

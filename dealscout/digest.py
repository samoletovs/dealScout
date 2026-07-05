"""Compose the periodic digest from judged newsletter sale-events."""

from __future__ import annotations

import logging

from .models import SaleEvent

logger = logging.getLogger(__name__)

_BAND_TITLE = {"must-look": "🟢 Must-look", "good": "🟡 Good offers"}


def compose_digest(events: list[tuple[SaleEvent, str]]) -> str:
    """Compose a markdown digest from (SaleEvent, band) pairs.

    Only 'must-look' and 'good' bands are included, grouped by band and sorted
    by discount depth. Returns a friendly 'nothing' note when there's no signal.
    """
    keep = [(event, band) for event, band in events if band in _BAND_TITLE]
    if not keep:
        return "# dealScout digest — nothing worth your time this run\n"

    keep.sort(key=lambda pair: pair[0].max_discount_pct, reverse=True)
    lines = ["# dealScout digest\n"]
    for band in ("must-look", "good"):
        group = [event for event, b in keep if b == band]
        if not group:
            continue
        lines.append(f"## {_BAND_TITLE[band]}\n")
        for event in group:
            cats = ", ".join(event.categories) if event.categories else "various"
            disc = f"up to {event.max_discount_pct:.0f}% off" if event.max_discount_pct else "sale"
            link = f" — [shop]({event.url})" if event.url else ""
            lines.append(f"- **{event.brand}** — {disc} ({cats}){link}")
            lines.append(f"  - _{event.headline}_")
        lines.append("")
    return "\n".join(lines)

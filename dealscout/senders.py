"""Summarize newsletter senders — a subscription-health signal.

If a brand you subscribed to never appears here, the opt-in probably wasn't
confirmed (or the brand simply hasn't sent a campaign yet). Silent brands are
the ones to re-check.
"""

from __future__ import annotations

import re
from collections import Counter


def sender_domain(sender: str) -> str:
    """Extract the domain from a From header like 'BOSS <news@hugoboss.com>'."""
    match = re.search(r"@([\w.-]+)", sender)
    return match.group(1).lower() if match else sender.strip().lower()


def summarize_senders(messages: list[tuple[str, str, str]]) -> list[tuple[str, int]]:
    """Return [(domain, count)] sorted by count desc from (sender, subject, html)."""
    counts = Counter(sender_domain(sender) for sender, _subject, _html in messages)
    return counts.most_common()

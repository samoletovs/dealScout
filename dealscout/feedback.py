"""User feedback on surfaced deals — the "did I act on it?" signal.

Each deal in an email carries 👍/👎 ``mailto:`` links that reply to the dealScout
mailbox with a verdict token and the product URL. The digest run reads those replies
back from the inbox (the mailbox *is* the ledger — no separate store) and reports the
tally, closing the loop: a 👎 on a surfaced deal is a false positive that can be added
to ``evals/golden.yaml`` to sharpen the judge.

Everything here is pure and side-effect free, so it is easy to unit-test.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

from .models import Feedback

logger = logging.getLogger(__name__)

#: Subject marker used to route replies back to the feedback ledger.
FEEDBACK_SUBJECT = "dealScout feedback"
UP = "up"
DOWN = "down"
_VERDICTS = (UP, DOWN)

_URL_RE = re.compile(r"https?://[^\s<>\"')]+")
# The verdict token immediately precedes the product URL in the reply body we emit.
_TAGGED_URL_RE = re.compile(r"\b(up|down)\b\s+(https?://[^\s<>\"')]+)", re.IGNORECASE)


def feedback_mailto(address: str, product_url: str, verdict: str) -> str:
    """Build a ``mailto:`` link that replies with the verdict token and product URL."""
    if verdict not in _VERDICTS:
        raise ValueError(f"verdict must be one of {_VERDICTS}, got {verdict!r}")
    subject = quote(f"{FEEDBACK_SUBJECT}: {verdict}")
    body = quote(f"{verdict} {product_url}")
    return f"mailto:{address}?subject={subject}&body={body}"


def feedback_text(address: str, product_url: str) -> str:
    """A one-line 👍/👎 prompt with ``mailto:`` links, or "" if no address is set."""
    if not address:
        return ""
    up = feedback_mailto(address, product_url, UP)
    down = feedback_mailto(address, product_url, DOWN)
    return f"rate: 👍 keep {up}  ·  👎 skip {down}"


def parse_feedback(subject: str, body: str) -> Feedback | None:
    """Extract a Feedback from a reply's subject + body. None if it isn't feedback."""
    subject = subject or ""
    body = body or ""
    if FEEDBACK_SUBJECT.lower() not in subject.lower():
        return None

    verdict = _verdict_from(subject) or _verdict_from(body)
    if verdict is None:
        return None

    tagged = _TAGGED_URL_RE.search(body)
    if tagged:
        url = tagged.group(2)
    else:
        loose = _URL_RE.search(body)
        url = loose.group(0) if loose else ""
    return Feedback(url=url, verdict=verdict)


def _verdict_from(text: str) -> str | None:
    """Detect a verdict from a subject or body: explicit token or 👍/👎 emoji."""
    low = text.lower()
    if "👎" in text or re.search(r"\b(down|skip|no)\b", low):
        return DOWN
    if "👍" in text or re.search(r"\b(up|keep|yes)\b", low):
        return UP
    return None


def collect_feedback(messages: list[tuple[str, str, str]]) -> list[Feedback]:
    """Parse (sender, subject, body) inbox tuples into Feedback entries."""
    out = [
        fb
        for _sender, subject, body in messages
        if (fb := parse_feedback(subject, body)) is not None
    ]
    logger.info("parsed %d feedback reply(ies)", len(out))
    return out


def summarize_feedback(entries: list[Feedback]) -> str:
    """Render a Markdown tally of 👍/👎 feedback for the digest."""
    if not entries:
        return (
            "## 👍/👎 Feedback\n\n"
            "_No ratings yet — tap 👍/👎 on a deal to train the judge._\n"
        )
    ups = [e for e in entries if e.verdict == UP]
    downs = [e for e in entries if e.verdict == DOWN]
    lines = [
        "## 👍/👎 Feedback",
        "",
        f"**{len(ups)} kept · {len(downs)} skipped** — from {len(entries)} rating(s)",
    ]
    skipped = [e.url for e in downs if e.url]
    if skipped:
        lines += ["", "_Skipped — candidates for evals/golden.yaml (false positives):_"]
        lines += [f"- 👎 {url}" for url in skipped]
    lines.append("")
    return "\n".join(lines)

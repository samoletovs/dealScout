"""User feedback on surfaced deals — the "did I act on it?" signal.

Each deal in an email carries 👍/👎 links to courier's ``/api/feedback`` endpoint,
which appends the vote to a per-project blob. The next run reads the tally back via
``/api/feedback/export`` and reports it, closing the loop: a 👎 on a surfaced deal is a
false positive that can be added to ``evals/golden.yaml`` to sharpen the judge.

(The ``mailto:`` reply parser — ``parse_feedback``/``collect_feedback`` — is retained
for the dormant newsletter-digest path; the live scan uses the HTTP ledger below.)

Everything here is pure and side-effect free, so it is easy to unit-test.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote

from .models import Feedback

logger = logging.getLogger(__name__)

#: Subject marker used to route replies back to the feedback ledger (legacy IMAP path).
FEEDBACK_SUBJECT = "dealScout feedback"
UP = "up"
DOWN = "down"
_VERDICTS = (UP, DOWN)

_URL_RE = re.compile(r"https?://[^\s<>\"')]+")
# The verdict token immediately precedes the product URL in the reply body we emit.
_TAGGED_URL_RE = re.compile(r"\b(up|down)\b\s+(https?://[^\s<>\"')]+)", re.IGNORECASE)


def feedback_link(base_url: str, product_url: str, verdict: str, project: str = "dealscout") -> str:
    """Build an HTTPS link to courier's feedback endpoint for a verdict + product URL."""
    if verdict not in _VERDICTS:
        raise ValueError(f"verdict must be one of {_VERDICTS}, got {verdict!r}")
    query = f"p={quote(project)}&v={verdict}&u={quote(product_url, safe='')}"
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}{query}"


def feedback_text(base_url: str, product_url: str, project: str = "dealscout") -> str:
    """A one-line 👍/👎 prompt as markdown links, or "" if no feedback URL.

    Markdown links render as clickable buttons in the HTML email alternative and stay
    readable in the plain-text part.
    """
    if not base_url:
        return ""
    up = feedback_link(base_url, product_url, UP, project)
    down = feedback_link(base_url, product_url, DOWN, project)
    return f"rate: [👍 keep]({up}) · [👎 skip]({down})"


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


def parse_feedback_jsonl(text: str) -> list[Feedback]:
    """Parse courier's feedback export (one JSON object per line) into Feedback entries."""
    out: list[Feedback] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        verdict = str(row.get("verdict", "")).lower()
        if verdict not in _VERDICTS:
            continue
        out.append(
            Feedback(url=str(row.get("url", "")), verdict=verdict, when=str(row.get("ts", "")))
        )
    logger.info("parsed %d feedback row(s)", len(out))
    return out


def latest_by_url(entries: list[Feedback]) -> list[Feedback]:
    """Collapse to the most recent verdict per URL (dedupes email prefetch / re-votes)."""
    latest: dict[str, Feedback] = {}
    for fb in entries:
        current = latest.get(fb.url)
        if current is None or fb.when >= current.when:
            latest[fb.url] = fb
    return list(latest.values())


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

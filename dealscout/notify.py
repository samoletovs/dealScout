"""Notify — email a buy-signal and write a buy-signals report for VS Code review.

Email goes through the shared `courier` service (Azure Communication Services Email),
so dealScout needs no mail account of its own. The markdown report is the primary
artefact for the VS Code cockpit.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import aiohttp

from .feedback import feedback_link, latest_by_url, parse_feedback_jsonl
from .models import Feedback, Product, Verdict

try:
    import markdown as _markdown
except ImportError:  # pragma: no cover - markdown is a declared dependency
    _markdown = None

logger = logging.getLogger(__name__)

_BAND_EMOJI = {"must-buy": "🟢", "good": "🟡"}


def _is_brand_store(brand: str, source: str) -> bool:
    """True when the store looks like the brand's own shop (e.g. BOSS at 'Hugo Boss').

    Token-based so a short brand ('COS') doesn't false-match a substring ('Costco').
    """
    brand_tokens = {t for t in re.split(r"[^a-z0-9]+", brand.lower()) if len(t) >= 3}
    source_tokens = {t for t in re.split(r"[^a-z0-9]+", source.lower()) if len(t) >= 3}
    return bool(brand_tokens & source_tokens)


def markdown_to_html(body: str) -> str | None:
    """Render a markdown email body to HTML, or None if markdown isn't available.

    Attached as an alternative so 👍/👎 feedback links render as clickable buttons —
    plain-text ``mailto:`` links aren't reliably clickable outside Gmail.
    """
    if _markdown is None:
        return None
    rendered = _markdown.markdown(body)
    return (
        "<!DOCTYPE html><html><body style=\"font-family:system-ui,-apple-system,"
        "'Segoe UI',Roboto,sans-serif;line-height:1.5;max-width:640px;margin:0 auto;"
        "padding:8px;\">"
        f"{rendered}</body></html>"
    )


def render_report(signals: list[tuple[Product, Verdict]], feedback_base_url: str = "") -> str:
    """Render a compact markdown buy-signals report, grouped by store.

    Deals are grouped by store, most deals first, so you can pick several items from one
    shop and get a single delivery. Used items are filtered out upstream, so everything
    here is new. A courier feedback base URL adds inline 👍/👎 links per deal.
    """
    if not signals:
        return "# dealScout — no buy-signals this run\n"

    groups: dict[str, list[tuple[Product, Verdict]]] = {}
    for product, verdict in signals:
        groups.setdefault(product.source or "Other stores", []).append((product, verdict))
    is_brand = {
        store: any(_is_brand_store(p.brand, store) for p, _ in items)
        for store, items in groups.items()
    }
    # Brand's own shops first (the preferred channel), then stores with the most deals.
    ordered = sorted(
        groups.items(), key=lambda kv: (not is_brand[kv[0]], -len(kv[1]), kv[0].lower())
    )

    lines = ["# dealScout — buy-signals\n"]
    for store, items in ordered:
        suffix = " (brand store)" if is_brand[store] else ""
        lines.append(f"## {store} — {len(items)} deal(s){suffix}")
        for product, verdict in items:
            tag = _BAND_EMOJI.get(verdict.band, "")
            head = f"{tag} " if tag else ""
            bits = [f"{head}[{product.title} — €{product.price:.0f}]({product.url})"]
            ref = product.reference_price
            if ref and ref > product.price:
                pct = round(100 * (1 - product.price / ref))
                bits.append(f"was €{ref:.0f} (-{pct}%)")
            bits.append("new")
            if feedback_base_url:
                up = feedback_link(feedback_base_url, product.url, "up")
                down = feedback_link(feedback_base_url, product.url, "down")
                bits.append(f"[👍]({up}) [👎]({down})")
            lines.append(f"- {' · '.join(bits)}")
        lines.append("")
    lines.append("_Prices via Google Shopping — verify fabric & exact item on click._")
    return "\n".join(lines)


def write_report(
    signals: list[tuple[Product, Verdict]], path: Path, feedback_base_url: str = ""
) -> Path:
    """Write the buy-signals report to disk and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(signals, feedback_base_url), encoding="utf-8")
    logger.info("wrote buy-signals report -> %s", path)
    return path


async def send_email(subject: str, body: str) -> bool:
    """Email the buy-signal via the shared courier service (ACS Email).

    Reads COURIER_URL, COURIER_KEY and DEALSCOUT_EMAIL_TO. If any is missing, logs
    and skips (so local/CI runs don't fail). The recipient must be on courier's
    allowlist. Returns True if courier accepted the message.
    """
    url = os.getenv("COURIER_URL")
    key = os.getenv("COURIER_KEY")
    to_addr = os.getenv("DEALSCOUT_EMAIL_TO")
    if not url or not key or not to_addr:
        logger.warning(
            "courier not configured (COURIER_URL/COURIER_KEY/DEALSCOUT_EMAIL_TO) — skipping send"
        )
        return False

    payload = {"to": to_addr, "subject": subject, "text": body}
    html = markdown_to_html(body)
    if html:
        payload["html"] = html

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                params={"code": key},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status >= 400:
                    logger.error("courier send failed: HTTP %s", resp.status)
                    return False
    except aiohttp.ClientError as exc:
        logger.error("courier send failed: %s", exc)
        return False

    logger.info("sent buy-signal email via courier to %s", to_addr)
    return True


def feedback_base_url() -> str:
    """Courier's feedback endpoint, derived from COURIER_URL (…/api/send → …/api/feedback)."""
    url = os.getenv("COURIER_URL", "")
    return url.replace("/api/send", "/api/feedback") if url else ""


async def read_feedback(project: str = "dealscout") -> list[Feedback]:
    """Read the 👍/👎 tally back from courier's export endpoint (latest vote per URL).

    Best-effort: returns [] if courier isn't configured or the call fails, so a run
    never breaks just because feedback couldn't be read.
    """
    base = feedback_base_url()
    key = os.getenv("COURIER_KEY")
    if not base or not key:
        return []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base}/export",
                params={"code": key, "p": project},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status >= 400:
                    logger.warning("courier feedback export failed: HTTP %s", resp.status)
                    return []
                text = await resp.text()
    except aiohttp.ClientError as exc:
        logger.warning("courier feedback export failed: %s", exc)
        return []
    return latest_by_url(parse_feedback_jsonl(text))

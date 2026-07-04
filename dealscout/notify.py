"""Notify — email a buy-signal and write a buy-signals report for VS Code review.

v1 email is a stub; wire SMTP (env: DEALSCOUT_SMTP_*) or Azure Communication
Services later. The markdown report is the primary artefact for the VS Code cockpit.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .models import Product, Verdict

logger = logging.getLogger(__name__)


def render_report(signals: list[tuple[Product, Verdict]]) -> str:
    """Render a markdown buy-signals report."""
    if not signals:
        return "# dealScout — no buy-signals this run\n"
    lines = ["# dealScout — buy-signals\n"]
    for product, verdict in signals:
        lines.append(f"## {product.title} — €{product.price:.0f} (score {verdict.score})")
        lines.append(f"- {product.url}")
        lines.append(f"- why: {', '.join(verdict.reasons)}")
        lines.append("")
    return "\n".join(lines)


def write_report(signals: list[tuple[Product, Verdict]], path: Path) -> Path:
    """Write the buy-signals report to disk and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(signals), encoding="utf-8")
    logger.info("wrote buy-signals report -> %s", path)
    return path


async def send_email(subject: str, body: str) -> None:
    """Send the buy-signal email.

    TODO: wire SMTP via env (DEALSCOUT_SMTP_*) or Azure Communication Services.
    """
    logger.warning("notify.send_email is a stub — would send: %s", subject)

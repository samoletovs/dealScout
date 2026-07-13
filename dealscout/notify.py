"""Notify — email a buy-signal and write a buy-signals report for VS Code review.

v1 email is a stub; wire SMTP (env: DEALSCOUT_SMTP_*) or Azure Communication
Services later. The markdown report is the primary artefact for the VS Code cockpit.
"""

from __future__ import annotations

import logging
import os
from email.message import EmailMessage
from pathlib import Path

import aiosmtplib

from .feedback import feedback_text
from .models import Product, Verdict

try:
    import markdown as _markdown
except ImportError:  # pragma: no cover - markdown is a declared dependency
    _markdown = None

logger = logging.getLogger(__name__)


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


def render_report(signals: list[tuple[Product, Verdict]], feedback_address: str = "") -> str:
    """Render a markdown buy-signals report.

    When a feedback address is given, each deal gets a 👍/👎 prompt whose replies
    are read back from the mailbox (see dealscout.feedback).
    """
    if not signals:
        return "# dealScout — no buy-signals this run\n"
    lines = ["# dealScout — buy-signals\n"]
    for product, verdict in signals:
        lines.append(f"## {product.title} — €{product.price:.0f} (score {verdict.score})")
        lines.append(f"- {product.url}")
        lines.append(f"- why: {', '.join(verdict.reasons)}")
        prompt = feedback_text(feedback_address, product.url)
        if prompt:
            lines.append(f"- {prompt}")
        lines.append("")
    return "\n".join(lines)


def write_report(
    signals: list[tuple[Product, Verdict]], path: Path, feedback_address: str = ""
) -> Path:
    """Write the buy-signals report to disk and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(signals, feedback_address), encoding="utf-8")
    logger.info("wrote buy-signals report -> %s", path)
    return path


async def send_email(subject: str, body: str) -> bool:
    """Send the buy-signal email over SMTP (env-configured).

    Reads DEALSCOUT_SMTP_HOST/PORT/USER/PASS and DEALSCOUT_EMAIL_TO. If the host
    or recipient is not configured, logs and skips (so local/CI runs don't fail).
    Returns True if an email was sent.
    """
    host = os.getenv("DEALSCOUT_SMTP_HOST")
    to_addr = os.getenv("DEALSCOUT_EMAIL_TO")
    if not host or not to_addr:
        logger.warning("email not configured (DEALSCOUT_SMTP_HOST/EMAIL_TO) — skipping send")
        return False

    message = EmailMessage()
    message["From"] = os.getenv("DEALSCOUT_SMTP_USER") or to_addr
    message["To"] = to_addr
    message["Subject"] = subject
    message.set_content(body)
    html = markdown_to_html(body)
    if html:
        message.add_alternative(html, subtype="html")

    await aiosmtplib.send(
        message,
        hostname=host,
        port=int(os.getenv("DEALSCOUT_SMTP_PORT") or "587"),
        username=os.getenv("DEALSCOUT_SMTP_USER"),
        password=os.getenv("DEALSCOUT_SMTP_PASS"),
        start_tls=True,
    )
    logger.info("sent buy-signal email to %s", to_addr)
    return True

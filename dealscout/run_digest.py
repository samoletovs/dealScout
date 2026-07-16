"""dealScout digest entrypoint: read inbox -> parse -> judge -> email digest."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .config import load_config
from .digest import compose_digest, render_senders
from .feedback import collect_feedback, summarize_feedback
from .inbox import fetch_feedback, fetch_recent, fetch_since, health, mailbox_counts
from .models import SaleEvent
from .newsletters import event_band, parse_newsletter
from .notify import feedback_base_url, send_email
from .senders import summarize_senders

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("dealscout.digest")


async def run(config_path: Path) -> str:
    """Read the newsletter inbox, judge sales, write + email the digest."""
    config = load_config(config_path)
    status = health()
    if status == "not_configured":
        logger.warning("mailbox not configured (DEALSCOUT_IMAP_USER/PASS) — skipping run")
        return ""
    if status == "auth_failed":
        raise SystemExit(
            "dealScout cannot log into the mailbox — deals are NOT being monitored. "
            "Regenerate the Gmail app password and update the DEALSCOUT_IMAP_PASS "
            "and DEALSCOUT_SMTP_PASS secrets."
        )
    mailbox_counts()  # deliverability diagnostic (logged)
    deal_messages = fetch_recent()  # new/unseen → deal alerts (marked read after)
    events: list[tuple[SaleEvent, str]] = []
    for sender, subject, html in deal_messages:
        event = parse_newsletter(sender, subject, html)
        if event is not None:
            events.append((event, event_band(event, config)))

    senders = summarize_senders(fetch_since(7))  # last 7 days → subscription health
    feedback = collect_feedback(fetch_feedback())
    digest = (
        compose_digest(events, feedback_base_url())
        + "\n" + summarize_feedback(feedback)
        + "\n" + render_senders(senders)
    )
    out = Path("out/digest.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(digest, encoding="utf-8")
    logger.info(
        "digest: %d new deal(s), %d rating(s); active senders=%s",
        len(events), len(feedback), [d for d, _ in senders],
    )

    kept = [pair for pair in events if pair[1] in ("must-look", "good")]
    if kept:
        await send_email(f"dealScout digest — {len(kept)} deal(s)", digest)
    elif not senders:
        # No deals AND no newsletters arrived — subscriptions may be dead; worth an alert.
        await send_email("dealScout — no newsletters this week?", digest)
    else:
        logger.info(
            "digest: 0 deal(s) but %d active sender(s) — skipping email to cut noise "
            "(report written to out/digest.md)",
            len(senders),
        )
    return digest


def main() -> None:
    """CLI entrypoint. Uses config.local.yaml if present, else config.example.yaml."""
    config_path = Path("config.local.yaml")
    if not config_path.exists():
        config_path = Path("config.example.yaml")
        logger.info("config.local.yaml not found — using %s", config_path)
    asyncio.run(run(config_path))


if __name__ == "__main__":
    main()

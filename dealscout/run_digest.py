"""dealScout digest entrypoint: read inbox -> parse -> judge -> email digest."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .config import load_config
from .digest import compose_digest, render_senders
from .inbox import fetch_recent
from .models import SaleEvent
from .newsletters import event_band, parse_newsletter
from .notify import send_email
from .senders import summarize_senders

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("dealscout.digest")


async def run(config_path: Path) -> str:
    """Read the newsletter inbox, judge sales, write + email the digest."""
    config = load_config(config_path)
    messages = fetch_recent()
    events: list[tuple[SaleEvent, str]] = []
    for sender, subject, html in messages:
        event = parse_newsletter(sender, subject, html)
        if event is not None:
            events.append((event, event_band(event, config)))

    senders = summarize_senders(messages)
    digest = compose_digest(events) + "\n" + render_senders(senders)
    out = Path("out/digest.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(digest, encoding="utf-8")
    logger.info(
        "digest: %d event(s) from %d newsletter(s); senders=%s",
        len(events), len(messages), [d for d, _ in senders],
    )

    kept = [pair for pair in events if pair[1] in ("must-look", "good")]
    if kept or messages:
        await send_email(f"dealScout — {len(kept)} deal(s), {len(messages)} newsletter(s)", digest)
    else:
        logger.info("inbox empty — no email (if this persists, check subscriptions)")
    return digest


def main() -> None:
    """CLI entrypoint. Uses config.local.yaml if present, else config.example.yaml."""
    config_path = Path("config.local.yaml")
    if not config_path.exists():
        config_path = Path("config.example.yaml")
        logger.warning("config.local.yaml not found — using %s", config_path)
    asyncio.run(run(config_path))


if __name__ == "__main__":
    main()

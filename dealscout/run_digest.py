"""dealScout digest entrypoint: read inbox -> parse -> judge -> email digest."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .config import load_config
from .digest import compose_digest
from .inbox import fetch_recent
from .models import SaleEvent
from .newsletters import event_band, parse_newsletter
from .notify import send_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("dealscout.digest")


async def run(config_path: Path) -> str:
    """Read the newsletter inbox, judge sales, write + email the digest."""
    config = load_config(config_path)
    events: list[tuple[SaleEvent, str]] = []
    for sender, subject, html in fetch_recent():
        event = parse_newsletter(sender, subject, html)
        if event is not None:
            events.append((event, event_band(event, config)))

    digest = compose_digest(events)
    out = Path("out/digest.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(digest, encoding="utf-8")
    logger.info("digest: %d sale event(s) considered", len(events))

    kept = [pair for pair in events if pair[1] in ("must-look", "good")]
    if kept:
        await send_email(f"dealScout digest — {len(kept)} deal(s)", digest)
    else:
        logger.info("no must-look / good offers this run")
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

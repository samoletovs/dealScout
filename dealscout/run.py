"""dealScout entrypoint: load config -> collect -> judge -> notify."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .collector import collect
from .config import load_config
from .judge import judge
from .models import Product, Verdict, WatchItem
from .notify import feedback_base_url, send_email, write_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("dealscout")


class NothingReached(RuntimeError):
    """The run reached nobody: nothing could be read, or nothing could be sent."""


async def run(config_path: Path) -> list[tuple[Product, Verdict]]:
    """Run one dealScout pass and return the buy-signals found."""
    config = load_config(config_path)
    items = [WatchItem(**w) for w in config.get("watch", [])]
    logger.info("dealScout run: %d watch item(s)", len(items))

    signals: list[tuple[Product, Verdict]] = []
    collected = 0
    for item in items:
        product = await collect(item)
        if product is None:
            continue
        collected += 1
        verdict = judge(product, config)
        if verdict.is_deal:
            signals.append((product, verdict))

    report = write_report(signals, Path("out/buy-signals.md"), feedback_base_url())
    if signals:
        if not await send_email(
            f"dealScout: {len(signals)} deal(s)", report.read_text(encoding="utf-8")
        ):
            raise NothingReached(f"{len(signals)} deal(s) found but the email could not be sent")
    else:
        logger.info("no unmissable deals this run")

    if items and not collected:
        # Every page failed to load. This ran daily for months against
        # `https://www.example.com/product/123` — the placeholder in the shipped example
        # config — and logged two 404s and a success, because "nothing was a deal" and
        # "nothing could be read" produced the same silence. A watch list that yields no
        # product at all is a broken configuration, not a quiet week.
        raise NothingReached(f"none of the {len(items)} watch item(s) could be read")
    return signals


def main() -> int:
    """CLI entrypoint. Uses config.local.yaml if present, else config.example.yaml."""
    config_path = Path("config.local.yaml")
    if not config_path.exists():
        config_path = Path("config.example.yaml")
        logger.info("config.local.yaml not found — using %s", config_path)
    try:
        asyncio.run(run(config_path))
    except NothingReached as empty:
        logger.error("%s", empty)
        return 1
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

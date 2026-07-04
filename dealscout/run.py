"""dealScout entrypoint: load config -> collect -> judge -> notify."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .collector import collect
from .config import load_config
from .judge import judge
from .models import Product, Verdict, WatchItem
from .notify import send_email, write_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("dealscout")


async def run(config_path: Path) -> list[tuple[Product, Verdict]]:
    """Run one dealScout pass and return the buy-signals found."""
    config = load_config(config_path)
    items = [WatchItem(**w) for w in config.get("watch", [])]
    logger.info("dealScout run: %d watch item(s)", len(items))

    signals: list[tuple[Product, Verdict]] = []
    for item in items:
        product = await collect(item)
        if product is None:
            continue
        verdict = judge(product, config)
        if verdict.is_deal:
            signals.append((product, verdict))

    report = write_report(signals, Path("out/buy-signals.md"))
    if signals:
        await send_email(f"dealScout: {len(signals)} deal(s)", report.read_text(encoding="utf-8"))
    else:
        logger.info("no unmissable deals this run")
    return signals


def main() -> None:
    """CLI entrypoint. Uses config.local.yaml if present, else config.example.yaml."""
    config_path = Path("config.local.yaml")
    if not config_path.exists():
        config_path = Path("config.example.yaml")
        logger.warning("config.local.yaml not found — using %s", config_path)
    asyncio.run(run(config_path))


if __name__ == "__main__":
    main()

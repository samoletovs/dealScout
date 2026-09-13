"""dealScout SerpApi entrypoint: scan Google Shopping -> judge -> notify.

Dormant unless SERPAPI_KEY is set and config `serpapi.enabled: true`. Because Google
Shopping carries no fabric composition, the judge is run with the natural-fibre gate
off — these are candidates whose fabric/logo the human confirms on click.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .config import load_config
from .discovery import Discovery, DiscoveryError, write_status
from .feedback import downvoted_urls, summarize_feedback
from .judge import judge
from .models import Product, Verdict
from .notify import feedback_base_url, read_feedback, render_report, send_email
from .serpsearch import scan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("dealscout.serpapi")


async def run(config_path: Path, *, send: bool = True) -> list[tuple[Product, Verdict]]:
    """Refresh weekly discovery, then optionally email new Shopping candidates."""
    config = load_config(config_path)
    status = await Discovery(config).refresh()
    write_status(status)
    if not status.startswith("Updated:"):
        return []
    candidates = await scan(config)
    if not candidates:
        logger.info("weekly discovery completed; no qualifying Shopping candidates")
        return []

    # Shopping has no fabric data, so judge with the fibre gate off; brand tier, price
    # band, never-above and discount still apply. Fabric/logo is verified on click.
    cand_config = {**config, "filters": {**config.get("filters", {}), "natural_fibre_min": 0}}

    entries = await read_feedback()
    rejected = downvoted_urls(entries)  # never re-surface a deal you 👎'd

    signals: list[tuple[Product, Verdict]] = []
    for product in candidates:
        if product.url in rejected:
            continue
        verdict = judge(product, cand_config)
        if verdict.is_deal:
            verdict = Verdict(
                verdict.is_deal,
                verdict.score,
                verdict.reasons + ("fabric unverified — check on click",),
                verdict.band,
            )
            signals.append((product, verdict))
    if rejected:
        logger.info("respecting %d 👎 vote(s) — rejected deals won't be re-surfaced", len(rejected))

    base = feedback_base_url()
    body = render_report(signals, base) + "\n" + summarize_feedback(entries)
    out = Path("out/serpapi-signals.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    logger.info("wrote buy-signals report -> %s", out)
    if signals and send:
        if not await send_email(f"dealScout scan: {len(signals)} deal(s)", body):
            raise DiscoveryError("Weekly discovery completed but its findings could not be emailed")
    elif not signals:
        logger.info("scan found no on-profile deals this run")
    return signals


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Uses config.local.yaml if present, else config.example.yaml."""
    parser = argparse.ArgumentParser(description="Refresh bounded weekly SerpApi discovery.")
    parser.add_argument("--no-email", action="store_true", help="refresh without sending an email")
    opts = parser.parse_args(argv)
    config_path = Path("config.local.yaml")
    if not config_path.exists():
        config_path = Path("config.example.yaml")
        logger.info("config.local.yaml not found — using %s", config_path)
    try:
        asyncio.run(run(config_path, send=not opts.no_email))
    except DiscoveryError as exc:
        write_status(f"Failed: {exc}. Direct retailer sources remain active.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

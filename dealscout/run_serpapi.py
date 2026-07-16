"""dealScout SerpApi entrypoint: scan Google Shopping -> judge -> notify.

Dormant unless SERPAPI_KEY is set and config `serpapi.enabled: true`. Because Google
Shopping carries no fabric composition, the judge is run with the natural-fibre gate
off — these are candidates whose fabric/logo the human confirms on click.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .config import load_config
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


async def run(config_path: Path) -> list[tuple[Product, Verdict]]:
    """Scan Google Shopping for on-profile bargains and email the buy-signals."""
    config = load_config(config_path)
    candidates = await scan(config)
    if not candidates:
        logger.info("no SerpApi candidates (disabled, or nothing on sale)")
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
    if signals:
        await send_email(f"dealScout scan: {len(signals)} deal(s)", body)
    else:
        logger.info("scan found no on-profile deals this run")
    return signals


def main() -> None:
    """CLI entrypoint. Uses config.local.yaml if present, else config.example.yaml."""
    config_path = Path("config.local.yaml")
    if not config_path.exists():
        config_path = Path("config.example.yaml")
        logger.info("config.local.yaml not found — using %s", config_path)
    asyncio.run(run(config_path))


if __name__ == "__main__":
    main()

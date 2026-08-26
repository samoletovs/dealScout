"""dealScout hunt entrypoint: scout -> judge -> monitor -> notify.

Runs every hunt declared in config (or just the one named on the command line):

    python -m dealscout.run_hunt              # all hunts
    python -m dealscout.run_hunt boots-junior # one hunt

Designed for a cron. The monitor means a run that finds the same boots at the same
price stays silent, so an email from dealScout always means something changed.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from .collector import enrich_all
from .config import load_config
from .feedback import downvoted_urls
from .hunt import judge_hunt
from .models import Change, Hunt, Product, Verdict
from .monitor import (
    DEFAULT_FORGET_AFTER_DAYS,
    DEFAULT_MIN_DROP_PCT,
    DEFAULT_STATE_PATH,
    classify,
    forget_stale,
    load_state,
    record,
    save_state,
)
from .notify import feedback_base_url, read_feedback, render_hunt_report, send_email
from .pricehistory import HistoryConfig, append, extend, load_history, observe, prune
from .pricehistory import rewrite as rewrite_history
from .pricehistory import summarise_all
from .scout import scout
from .spec import merge_vocab

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("dealscout.hunt")

Result = tuple[Product, Verdict, Change | None]


def load_hunts(config: dict, only: str = "") -> list[Hunt]:
    """Build Hunt objects from config: enabled ones, or exactly the one named."""
    hunts = [Hunt.from_dict(h) for h in (config.get("hunts") or []) if h.get("id")]
    if only:
        hunts = [h for h in hunts if h.id == only]  # naming a hunt overrides `enabled`
        if not hunts:
            logger.error("no hunt with id %r in config", only)
        return hunts
    return [h for h in hunts if h.enabled]


async def run_hunt(
    hunt: Hunt,
    config: dict,
    state: dict[str, dict],
    vocab: dict,
    rejected: frozenset[str] | set[str] = frozenset(),
    report_news_only: bool = True,
) -> tuple[list[Result], list[Product]]:
    """Run one hunt. Returns (results worth reporting, all candidates seen)."""
    candidates = [p for p in await scout(hunt, config, vocab=vocab) if p.url not in rejected]

    scrape = config.get("scrape") or {}
    delay = float(scrape.get("delay_seconds", 1.0))
    limit = int(scrape.get("max_confirmations", 25))

    # Triage on what the listing said, then confirm only the survivors on their own page.
    # A listing page rarely states sizes: confirming everything would cost a request per
    # product, and confirming nothing would mean emailing boots that do not exist in the
    # size we actually need.
    shortlisted = {p.url for p in candidates if judge_hunt(p, hunt, vocab).is_deal}
    to_confirm = [
        p
        for p in candidates
        if p.url in shortlisted and (not p.sizes_known or p.reference_price is None)
    ][:limit]
    if to_confirm:
        logger.info("hunt %s: confirming %d shortlisted product(s)", hunt.id, len(to_confirm))
        confirmed = {p.url: p for p in await enrich_all(to_confirm, delay)}
        candidates = [confirmed.get(p.url, p) for p in candidates]

    min_drop = float((config.get("monitor") or {}).get("min_drop_pct", DEFAULT_MIN_DROP_PCT))
    results: list[Result] = []
    for product in candidates:
        verdict = judge_hunt(product, hunt, vocab)
        if not verdict.is_deal:
            continue
        change = classify(product, state, hunt.sizes, min_drop)
        if report_news_only and not change.is_news:
            continue
        results.append((product, verdict, change))

    results.sort(key=lambda result: result[1].score, reverse=True)
    logger.info(
        "hunt %s: %d candidate(s) -> %d to report", hunt.id, len(candidates), len(results)
    )
    return results, candidates


async def run(config_path: Path, only: str = "") -> dict[str, list[Result]]:
    """Run every configured hunt and email a single digest of what changed."""
    config = load_config(config_path)
    hunts = load_hunts(config, only)
    if not hunts:
        logger.info("no hunts configured — nothing to do")
        return {}

    vocab = merge_vocab(config.get("vocab"))
    monitor_conf = config.get("monitor") or {}
    state_path = Path(monitor_conf.get("state_path") or DEFAULT_STATE_PATH)
    state = load_state(state_path)
    limits = HistoryConfig.from_config(config)
    history = load_history(limits.path)

    entries = await read_feedback()
    rejected = downvoted_urls(entries)  # never re-surface a deal you 👎'd
    base = feedback_base_url()

    findings: dict[str, list[Result]] = {}
    sections: list[str] = []
    logged: set[str] = set()  # config ships several hunts; a shared product logs once
    for hunt in hunts:
        results, candidates = await run_hunt(hunt, config, state, vocab, rejected)
        findings[hunt.id] = results

        # Log this run's prices before rendering, so "cheapest seen" is a claim about the
        # price in front of the reader rather than about the previous run's.
        fresh = [o for o in observe(candidates, hunt.sizes) if o.url not in logged]
        logged.update(o.url for o in fresh)
        append(fresh, limits.path)
        history = extend(history, fresh)
        memory = summarise_all([p for p, _, _ in results], history, limits=limits)

        body = render_hunt_report(hunt, results, base, vocab, memory)
        out = Path("out") / f"hunt-{hunt.id}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        logger.info("wrote %s", out)
        if results:
            sections.append(body)

        state = record(state, candidates, hunt.sizes)

    state = forget_stale(state, int(monitor_conf.get("forget_after_days", DEFAULT_FORGET_AFTER_DAYS)))
    save_state(state, state_path)
    rewrite_history(prune(history, limits.keep_days, limits.max_points), limits.path)

    total = sum(len(v) for v in findings.values())
    if total:
        subject = f"dealScout: {total} new find(s)"
        await send_email(subject, "\n\n---\n\n".join(sections))
    else:
        logger.info("nothing new across %d hunt(s) — staying quiet", len(hunts))
    return findings


def main() -> None:
    """CLI entrypoint. Uses config.local.yaml if present, else config.example.yaml."""
    config_path = Path("config.local.yaml")
    if not config_path.exists():
        config_path = Path("config.example.yaml")
        logger.info("config.local.yaml not found — using %s", config_path)
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    asyncio.run(run(config_path, only))


if __name__ == "__main__":
    main()

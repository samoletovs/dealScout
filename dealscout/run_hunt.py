"""dealScout hunt entrypoint: scout -> judge -> monitor -> notify.

Runs every hunt declared in config (or just the one named on the command line):

    python -m dealscout.run_hunt              # all hunts
    python -m dealscout.run_hunt boots-junior # one hunt

Designed for a cron. The monitor means a run that finds the same boots at the same
price stays silent, so an email from dealScout always means something changed.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import logging
import sys
from pathlib import Path

from .collector import enrich_all
from .config import load_config
from .feedback import downvoted_urls
from .hunt import judge_hunt, product_identity, validate_hunt
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
from .pricehistory import (
    HistoryConfig,
    append_dir,
    extend,
    load_history_dir,
    observe,
    prune,
    rewrite_dir,
    summarise_all,
)
from .scout import scout
from .spec import merge_vocab

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("dealscout.hunt")


class EmailNotDelivered(RuntimeError):
    """Findings were made but could not be sent, so the run reached nobody."""

    def __init__(self, findings: int) -> None:
        super().__init__(
            f"{findings} find(s) could not be emailed - the run produced nothing a human sees"
        )
        self.findings = findings

Result = tuple[Product, Verdict, Change | None]


def load_hunts(config: dict, only: str = "") -> list[Hunt]:
    """Build Hunt objects from config: enabled ones, or exactly the one named.

    Each is validated as it loads, so a config that has drifted out of step with the
    engine fails here rather than silently matching nothing for weeks.
    """
    vocab = merge_vocab(config.get("vocab"))
    hunts = [Hunt.from_dict(h) for h in (config.get("hunts") or []) if h.get("id")]
    for hunt in hunts:
        validate_hunt(hunt, vocab)
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


async def run(
    config_path: Path, only: str = "", *, send: bool = True
) -> dict[str, list[Result]]:
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
    history = load_history_dir(limits.dir, limits.path)

    entries = await read_feedback()
    rejected = downvoted_urls(entries)  # never re-surface a deal you 👎'd
    base = feedback_base_url()

    findings: dict[str, list[Result]] = {}
    sections: list[str] = []
    logged: set[tuple[str, str]] = set()  # config ships several hunts; a shared product logs once
    for hunt in hunts:
        results, candidates = await run_hunt(hunt, config, state, vocab, rejected)
        findings[hunt.id] = results

        # Log this run's prices before rendering, so "cheapest seen" is a claim about the
        # price in front of the reader rather than about the previous run's.
        identify = functools.partial(product_identity, hunt=hunt, vocab=vocab)
        fresh = [
            o
            for o in observe(candidates, hunt.sizes, identify=identify)
            if (o.key, o.source) not in logged
        ]
        logged.update((o.key, o.source) for o in fresh)
        append_dir(fresh, limits.dir)
        history = extend(history, fresh)
        memory = summarise_all(
            [p for p, _, _ in results], history, limits=limits, identify=identify
        )

        # An observation-only hunt has done its job once its prices are logged: it exists
        # for breadth, to keep a price for the whole range, and must never reach the digest.
        # Its prices are already in `fresh` above; skip its findings, render and section so a
        # three-hundred-boot range hunt cannot bury the owner's real finds.
        if hunt.observe_only:
            findings[hunt.id] = []
            logger.info("hunt %s: observe-only, logged %d price(s), not reported", hunt.id, len(fresh))
            state = record(state, candidates, hunt.sizes)
            continue

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
    rewrite_dir(prune(history, limits.keep_days, limits.max_points), limits.dir)

    total = sum(len(v) for v in findings.values())
    if total and send:
        subject = f"dealScout: {total} new find(s)"
        if not await send_email(subject, "\n\n---\n\n".join(sections)):
            # This ran twice a day for months and never reached anybody: the courier
            # secrets were unset, so `send_email` logged a warning and returned False,
            # and this line discarded it. The workflow then exited 0 and GitHub drew a
            # green tick. Finding boots nobody is told about is not a successful run.
            raise EmailNotDelivered(total)
    elif total:
        logger.info("%d new find(s) — not sending, as asked", total)
    else:
        logger.info("nothing new across %d hunt(s) — staying quiet", len(hunts))
    return findings


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the entrypoint's arguments, rejecting anything unrecognised.

    ``--no-email`` exists because failing to send is now an error. Without a way to say
    "I meant it", every local run that found something would exit 1 on a machine with no
    courier credentials — which is most machines, and none of them are broken.

    It also closes the same footgun `run_shortlist` had: `only` used to be
    ``sys.argv[1]``, so a mistyped flag became a hunt id, matched no hunt, and the run
    reported that there was nothing to do.
    """
    parser = argparse.ArgumentParser(
        prog="python -m dealscout.run_hunt",
        description="Scout every source for one hunt and email anything new.",
    )
    parser.add_argument(
        "hunt",
        nargs="?",
        default="",
        help="id of the hunt to run; overrides `enabled`. Defaults to every enabled hunt.",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="find and report, but do not send — and do not fail for not sending.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Uses config.local.yaml if present, else config.example.yaml."""
    opts = parse_args(sys.argv[1:] if argv is None else argv)
    config_path = Path("config.local.yaml")
    if not config_path.exists():
        config_path = Path("config.example.yaml")
        logger.info("config.local.yaml not found — using %s", config_path)
    try:
        asyncio.run(run(config_path, opts.hunt, send=not opts.no_email))
    except EmailNotDelivered as undelivered:
        logger.error("%s", undelivered)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

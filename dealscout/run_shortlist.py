"""dealScout shortlist entrypoint: scout -> judge -> rank -> email.

Where ``run_hunt`` answers "what changed since last time?" and stays silent when nothing
did, this answers "what should I buy right now?" and always reports. It is the thing to
run when a decision is actually being made, rather than on a cron.

    python -m dealscout.run_shortlist                 # every enabled hunt
    python -m dealscout.run_shortlist boots-junior    # one hunt
    python -m dealscout.run_shortlist boots-junior --no-email

The price ceiling is deliberately ignored here: a shortlist is for comparing, and knowing
the cheapest confirmed boot is €45 while the cheapest local one is €120 *is* the decision.
Everything else the hunt rejects — wrong tier, wrong soleplate, already owned, out of
stock in the wanted size — still applies.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

from .collector import enrich_all
from .config import load_config
from .feedback import downvoted_urls
from .hunt import judge_hunt
from .models import Hunt, Product
from .notify import feedback_base_url, read_feedback, render_shortlist, send_email
from .pricehistory import HistoryConfig, append, extend, load_history, observe, prune
from .pricehistory import rewrite as rewrite_history
from .pricehistory import summarise_all
from .run_hunt import load_hunts
from .scout import scout
from .shortlist import (
    DEFAULT_LIMIT,
    DEFAULT_PER_SOURCE,
    Delivery,
    SourceCoverage,
    expected_sources,
    pick_diverse,
    source_coverage,
    split_by_size_confidence,
    stamp_house_brands,
)
from .spec import merge_vocab

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("dealscout.shortlist")


def delivery_table(config: dict) -> dict[str, Delivery]:
    """Per-source delivery terms from config, keyed by bare host."""
    table: dict[str, Delivery] = {}
    for host, terms in (config.get("delivery") or {}).items():
        if isinstance(terms, dict):
            table[str(host).lower().removeprefix("www.")] = Delivery.from_dict(terms)
    return table


def _uncapped(hunt: Hunt) -> Hunt:
    """The same hunt with the price ceiling lifted, for comparison rather than alerting.

    Only the ceiling goes. ``must_buy`` is kept so a genuine bargain still bands as one,
    and ``good_offer`` is opened rather than removed: the judge treats a product outside
    every band as "not a deal", so clearing the bands would reject the entire catalogue —
    which is what the first version of this did.
    """
    return replace(hunt, never_above=None, good_offer=float("inf"))


async def shortlist_for(
    hunt: Hunt, config: dict, limit: int, per_source: int, rejected: frozenset[str] = frozenset()
) -> tuple[list[Product], list[Product], int, list[SourceCoverage]]:
    """Scout, judge without the price ceiling, and rank into two ranked lists."""
    vocab = merge_vocab(config.get("vocabulary"))
    candidates = [p for p in await scout(hunt, config, vocab=vocab) if p.url not in rejected]
    table = delivery_table(config)
    # A single-brand shop leaves its own brand out of its product names; restore it before
    # judging, or `brands_only` rejects the entire storefront.
    candidates = stamp_house_brands(candidates, table)
    open_hunt = _uncapped(hunt)

    scrape = config.get("scrape") or {}
    delay = float(scrape.get("delay_seconds", 1.0))
    confirm_limit = int(scrape.get("max_confirmations", 25))

    # Same triage as the hunt: judge on what the listing said, then re-read the product
    # pages of the survivors that still owe us a size or an RRP. A shortlist sorted on
    # unconfirmed sizes would be the wrong list entirely.
    keep = {p.url for p in candidates if judge_hunt(p, open_hunt, vocab).is_deal}
    to_confirm = [
        p for p in candidates if p.url in keep and (not p.sizes_known or p.reference_price is None)
    ][:confirm_limit]
    if to_confirm:
        logger.info("hunt %s: confirming %d product(s)", hunt.id, len(to_confirm))
        confirmed_pages = {p.url: p for p in await enrich_all(to_confirm, delay)}
        candidates = [confirmed_pages.get(p.url, p) for p in candidates]

    kept = [p for p in candidates if judge_hunt(p, open_hunt, vocab).is_deal]
    confirmed, unconfirmed = split_by_size_confidence(kept, hunt)
    logger.info(
        "hunt %s: %d candidate(s) -> %d kept, %d in-size, %d unstated",
        hunt.id,
        len(candidates),
        len(kept),
        len(confirmed),
        len(unconfirmed),
    )
    picked_confirmed = pick_diverse(confirmed, table, limit, per_source)
    picked_unconfirmed = pick_diverse(unconfirmed, table, limit, per_source)
    coverage = source_coverage(
        [*picked_confirmed, *picked_unconfirmed],
        table,
        expected_sources(hunt, table),
        pool=kept,
    )
    return picked_confirmed, picked_unconfirmed, len(candidates), coverage


def config_path() -> Path:
    """The owner's config if present, else the shipped example.

    Mirrors ``run_hunt.main``. The previous hardcoded ``config.yaml`` exists in neither a
    fresh clone nor CI, so this entrypoint raised FileNotFoundError before reading a page.
    """
    local = Path("config.local.yaml")
    if local.exists():
        return local
    logger.info("config.local.yaml not found — using config.example.yaml")
    return Path("config.example.yaml")


async def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("-")]
    send = "--no-email" not in argv
    config = load_config(config_path())
    hunts = load_hunts(config, args[0] if args else "")
    if not hunts:
        logger.error("no hunt to run")
        return 1

    table = delivery_table(config)
    vocab = merge_vocab(config.get("vocabulary"))
    limits = HistoryConfig.from_config(config)
    history = load_history(limits.path)
    logged: set[str] = set()  # config ships several hunts; a shared product logs once
    # A 👎 in a previous email is the owner saying "not this one". The shortlist emits
    # those rating links, so it must also honour them — otherwise a rejected boot returns
    # every single run and the feedback loop is decorative. run_hunt already does this.
    rejected = downvoted_urls(await read_feedback())
    if rejected:
        logger.info("%d product(s) previously rejected by 👎 — excluded", len(rejected))
    sent = 0
    for hunt in hunts:
        confirmed, unconfirmed, checked, coverage = await shortlist_for(
            hunt, config, DEFAULT_LIMIT, DEFAULT_PER_SOURCE, frozenset(rejected)
        )
        shown = [*confirmed, *unconfirmed]
        logger.info(
            "hunt %s: shortlist spread %s",
            hunt.id,
            ", ".join(f"{c.label}={c.count}/{c.found}" for c in coverage) or "empty",
        )

        # Log this run's prices before rendering, so a row can say where today's price
        # sits rather than where the previous run's did.
        fresh = [o for o in observe(shown, hunt.sizes) if o.url not in logged]
        logged.update(o.url for o in fresh)
        append(fresh, limits.path)
        history = extend(history, fresh)
        memory = summarise_all(shown, history, limits=limits)

        body = render_shortlist(
            hunt,
            confirmed,
            unconfirmed,
            table,
            feedback_base_url(),
            vocab,
            checked=checked,
            sources=len({p.source for p in shown}),
            memory=memory,
            coverage=coverage,
        )
        path = Path("out") / f"shortlist-{hunt.id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        logger.info("wrote %s", path)

        if send:
            subject = (
                f"dealScout — {len(confirmed)} in your size, "
                f"{len(unconfirmed)} to check · {hunt.label or hunt.id}"
            )
            if await send_email(subject, body):
                sent += 1
    rewrite_history(prune(history, limits.keep_days, limits.max_points), limits.path)
    logger.info("shortlist complete; %d email(s) sent", sent)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))

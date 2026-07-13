# dealScout — agent instructions

Personal bargain-hunting wardrobe assistant. A **co-pilot, not an autopilot**: detect an unmissable deal → notify → the human approves and checks out.

## Golden rules (never break)

1. **No unattended auto-buy.** EU Strong Customer Authentication (PSD2) requires a human to approve every card payment > €30. dealScout only *detects and notifies*.
2. **No payment credentials in the repo, ever.** No card data, no secrets committed.
3. **Never hardcode a user.** Brands, sizes, targets, contacts live in per-user config (`config.*.yaml`), not in code. The engine must stay user-agnostic so family/friends can be added by config alone.
4. **Prefer affiliate feeds over scraping.** Scrape only the owner's own watch-list pages, at a polite rate; respect robots/ToS and the EU database right.
5. **Quality bar is a hard filter.** Big logos/wordmarks/monograms are rejected; enforce the natural-fibre threshold; treat visible branding as a negative signal.

## Architecture

- `dealscout/config.py` — load per-user YAML config.
- `dealscout/models.py` — `WatchItem`, `Product`, `Verdict` dataclasses.
- `dealscout/collector.py` — turn a watch item into a `Product` snapshot (stub → implement via ld+json / feeds).
- `dealscout/judge.py` — the deal judge (pure, well-tested): price vs target + quality/logo/fabric rules. **This is the heart.**
- `dealscout/notify.py` — email + markdown buy-signals report.
- `dealscout/newsletters.py` — parse brand newsletters → SaleEvents, judge by tier + discount (P3 signal source).
- `dealscout/senders.py` — summarize inbox senders (subscription-health signal).
- `dealscout/inbox.py` — read recent newsletters from the dedicated Gmail via IMAP (App Password).
- `dealscout/digest.py` — compose the periodic 🟢/🟡 digest.
- `dealscout/run.py` — watch-list entrypoint: load → collect → judge → notify.
- `dealscout/run_digest.py` — digest entrypoint: read inbox → parse → judge → email digest.
- `dealscout/eval.py` — golden-set scorer for the judge (drift scorecard: band accuracy + deal precision/recall). Cases live in `evals/golden.yaml`; run `python -m dealscout.eval`.

## Where the owner's real profile lives

The design brief and the filled personal profile (sizes, preferences, budget bands, brand shortlist) live in the owner's **private vault** (mindVault), not in this repo. This repo carries only the schema (`config.example.yaml`) and the engine.

## Conventions

- Python 3.11+, type hints everywhere, `logging` not `print`, async for I/O, dataclasses for structured data.
- Tests: `pytest` (AAA, one behaviour per test, mock external calls).
- Golden path: GitHub Actions cron for v1; Azure Functions + Cosmos later. Managed identity, no API keys.

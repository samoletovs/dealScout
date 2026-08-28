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
- `dealscout/hunt.py` — the *hunt* judge: evaluates a declarative `Hunt` (tier/soleplate/size/brand/price) three-state, so an unstated attribute is *unknown* rather than failed. Also validates a hunt's `require:` against the values the engine can actually assign, so stale config fails loudly.
- `dealscout/catalogue.py` + `data/<category>.yaml` — the tier catalogue. Reads a title against a brand's real ladder and returns `adult-flagship` / `junior-flagship` / `takedown` / `unknown`, plus model line, generation and whether that generation is current. Replaces `"elite" in title`, which called a €70 Diadora *Academy* boot a flagship and a €130 junior Elite the same thing as a €280 adult one. It **classifies, never filters** — the owner buys for a child, so a junior flagship is a legitimate find; the job is to make sure he can tell which he is being offered. Consulted *before* the vocabulary, because `extract_attrs` matches in declaration order and `elite` shadows `academy` permanently. `data/*.yaml` carries `last_verified`: generation status is a season snapshot and rots silently. It also reads two things a retailer's spelling would otherwise hide: `title_rewrites` repairs how a shop writes a name (SportsDirect truncates "Copa Pure 3 Elite" to `CopaP3Elt`, and spells grounds out in words so Nike's `SG-Pro` *soleplate* strands a bare "Pro" that demotes a flagship), and `legacy_numbered` reads adidas's retired `.1`/`.2`/`.3`/`.4` tiers — `.1` is a flagship, level with `+`, and `.2`/`.3`/`.4` are Pro/League/Club. That numbering is read from the **raw** title because the dot is the whole signal: normalised, `Copa Pure 3` (a current generation) and `.3` (a League takedown) are the same characters.
- `dealscout/notify.py` — buy-signals report (markdown) + email via the shared `courier` service (ACS).
- `dealscout/newsletters.py` — parse brand newsletters → SaleEvents, judge by tier + discount (P3 signal source).
- `dealscout/senders.py` — summarize inbox senders (subscription-health signal).
- `dealscout/inbox.py` — read recent newsletters from the dedicated Gmail via IMAP (App Password).
- `dealscout/digest.py` — compose the periodic 🟢/🟡 digest.
- `dealscout/feedback.py` — 👍/👎 act-on loop: emit rating links in emails, read replies back from the inbox (the mailbox is the ledger), tally them. A 👎 on a surfaced deal is a golden-set candidate.
- `dealscout/monitor.py` — the seen-products ledger: classifies a sighting as new / price-drop / back-in-stock / seen, so a cron reports news rather than repeating itself.
- `dealscout/pricehistory.py` — price memory: an append-only log of one observation per product per run, read back as an honest `PriceMemory` ("cheapest seen in 45 days" / "not enough history yet"). The retailer's RRP is its own claim; this is the one that can be checked.
- `dealscout/run.py` — watch-list entrypoint: load → collect → judge → notify.
- `dealscout/run_digest.py` — digest entrypoint: read inbox → parse → judge → email digest.
- `dealscout/serpsearch.py` — opt-in Google Shopping scan via SerpApi → candidate Products (dormant unless `SERPAPI_KEY` + `serpapi.enabled`).
- `dealscout/run_serpapi.py` — scan entrypoint: SerpApi scan → judge (fibre off, fabric verified on click) → notify.
- `dealscout/eval.py` — golden-set scorer (drift scorecard). Cases in `evals/golden.yaml` are scored by the wardrobe judge, or — when they name a `hunt:` — by `judge_hunt`. `expected.attrs` pins the attributes behind a verdict so a case cannot pass for the wrong reason. Run `python -m dealscout.eval`.

## Where the owner's real profile lives

The design brief and the filled personal profile (sizes, preferences, budget bands, brand shortlist) live in the owner's **private vault** (mindVault), not in this repo. This repo carries only the schema (`config.example.yaml`) and the engine.

## Conventions

- Python 3.11+, type hints everywhere, `logging` not `print`, async for I/O, dataclasses for structured data.
- Tests: `pytest` (AAA, one behaviour per test, mock external calls).
- Golden path: GitHub Actions cron for v1; Azure Functions + Cosmos later. Managed identity, no API keys.

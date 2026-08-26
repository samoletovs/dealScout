"""Notify — email a buy-signal and write a buy-signals report for VS Code review.

Email goes through the shared `courier` service (Azure Communication Services Email),
so dealScout needs no mail account of its own. The markdown report is the primary
artefact for the VS Code cockpit.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import aiohttp

from .feedback import feedback_link, latest_by_url, parse_feedback_jsonl
from .hunt import resolve_attrs
from .models import Change, Feedback, Hunt, Product, Verdict
from .monitor import canonical_url
from .pricehistory import PriceMemory
from .shortlist import Delivery, SourceCoverage, delivery_for, landed_cost, matched_sizes

try:
    import markdown as _markdown
except ImportError:  # pragma: no cover - markdown is a declared dependency
    _markdown = None

logger = logging.getLogger(__name__)

_BAND_EMOJI = {"must-buy": "🟢", "good": "🟡"}


def _is_brand_store(brand: str, source: str) -> bool:
    """True when the store looks like the brand's own shop (e.g. BOSS at 'Hugo Boss').

    Token-based so a short brand ('COS') doesn't false-match a substring ('Costco').
    """
    brand_tokens = {t for t in re.split(r"[^a-z0-9]+", brand.lower()) if len(t) >= 3}
    source_tokens = {t for t in re.split(r"[^a-z0-9]+", source.lower()) if len(t) >= 3}
    return bool(brand_tokens & source_tokens)


def markdown_to_html(body: str) -> str | None:
    """Render a markdown email body to HTML, or None if markdown isn't available.

    Attached as an alternative so 👍/👎 feedback links render as clickable buttons —
    plain-text ``mailto:`` links aren't reliably clickable outside Gmail.

    ``tables`` is not on by default in Python-Markdown, and without it a pipe table
    reaches the reader as a paragraph of literal ``|`` characters — which is how the
    shortlist's per-source breakdown would arrive on a phone.
    """
    if _markdown is None:
        return None
    rendered = _markdown.markdown(body, extensions=["tables"])
    return (
        "<!DOCTYPE html><html><body style=\"font-family:system-ui,-apple-system,"
        "'Segoe UI',Roboto,sans-serif;line-height:1.5;max-width:640px;margin:0 auto;"
        "padding:8px;\">"
        f"{rendered}</body></html>"
    )


def render_report(signals: list[tuple[Product, Verdict]], feedback_base_url: str = "") -> str:
    """Render a compact markdown buy-signals report, grouped by store.

    Deals are grouped by store, most deals first, so you can pick several items from one
    shop and get a single delivery. Used items are filtered out upstream, so everything
    here is new. A courier feedback base URL adds inline 👍/👎 links per deal.
    """
    if not signals:
        return "# dealScout — no buy-signals this run\n"

    groups: dict[str, list[tuple[Product, Verdict]]] = {}
    for product, verdict in signals:
        groups.setdefault(product.source or "Other stores", []).append((product, verdict))
    is_brand = {
        store: any(_is_brand_store(p.brand, store) for p, _ in items)
        for store, items in groups.items()
    }
    # Brand's own shops first (the preferred channel), then stores with the most deals.
    ordered = sorted(
        groups.items(), key=lambda kv: (not is_brand[kv[0]], -len(kv[1]), kv[0].lower())
    )

    lines = ["# dealScout — buy-signals\n"]
    for store, items in ordered:
        suffix = " (brand store)" if is_brand[store] else ""
        lines.append(f"## {store} — {len(items)} deal(s){suffix}")
        for product, verdict in items:
            tag = _BAND_EMOJI.get(verdict.band, "")
            head = f"{tag} " if tag else ""
            bits = [f"{head}[{product.title} — €{product.price:.0f}]({product.url})"]
            ref = product.reference_price
            if ref and ref > product.price:
                pct = round(100 * (1 - product.price / ref))
                bits.append(f"was €{ref:.0f} (-{pct}%)")
            bits.append("new")
            if feedback_base_url:
                up = feedback_link(feedback_base_url, product.url, "up")
                down = feedback_link(feedback_base_url, product.url, "down")
                bits.append(f"[👍]({up}) [👎]({down})")
            lines.append(f"- {' · '.join(bits)}")
        lines.append("")
    lines.append("_Prices via Google Shopping — verify fabric & exact item on click._")
    return "\n".join(lines)


def write_report(
    signals: list[tuple[Product, Verdict]], path: Path, feedback_base_url: str = ""
) -> Path:
    """Write the buy-signals report to disk and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(signals, feedback_base_url), encoding="utf-8")
    logger.info("wrote buy-signals report -> %s", path)
    return path


_CHANGE_BADGE = {
    "new": "**NEW**",
    "price-drop": "**↓ PRICE DROP**",
    "back-in-stock": "**BACK IN STOCK**",
}

_ATTR_ORDER = ("tier", "soleplate", "silo", "plate", "fit")

# What each tier means in words the owner can act on. The junior gloss is the reason this
# exists: a €130 junior Elite and a €280 adult Elite are both truthfully "Elite", and
# printing that word alone lets the cheaper one read as the bargain of the year.
_TIER_LABEL = {
    "adult-flagship": "**adult flagship**",
    "junior-flagship": "**junior flagship** — top of the junior range, comfort-tuned "
    "plate, not adult construction",
    "takedown": "takedown — not the flagship",
}

_STATUS_LABEL = {
    "current": "current generation",
    "superseded": "superseded generation",
    "discontinued": "discontinued generation",
    "evergreen": "evergreen model — outside the tier ladder",
}


def tier_phrase(attrs: dict) -> str:
    """Say which flagship this is, and how old — or say nothing.

    Silent for an unknown tier: the row already carries a "verify on click" caveat in
    that case, and inventing a label would be the confident-wrong answer the catalogue
    exists to prevent.
    """
    label = _TIER_LABEL.get(str(attrs.get("tier") or ""))
    if not label:
        return ""
    status = _STATUS_LABEL.get(str(attrs.get("generation_status") or ""))
    if not status:
        return label
    year = attrs.get("generation_year")
    return f"{label} · {status}{f' ({year})' if year else ''}"


def price_memory_phrase(memory: PriceMemory | None) -> str:
    """Where this price sits against what the product has actually sold for — or nothing.

    The retailer's "-70%" is its own claim about its own RRP; this is the sentence that
    can be checked. It is deliberately silent when the history cannot carry a claim: a row
    reading "no price history" spends the reader's attention to tell them nothing, and a
    row reading "lowest in 90 days" after two runs would be worse than useless.

    The window quoted is the history we actually hold, floored to whole days, so the
    sentence never claims to have watched for longer than it has.
    """
    if memory is None or not memory.enough_history:
        return ""
    days = int(memory.span_days)
    if days < 1:
        return ""
    if memory.is_lowest:
        return f"**cheapest seen in {days} days**"
    gap = round(memory.above_low or 0.0)
    if gap >= 1:
        return f"€{gap:.0f} above its {days}-day low"
    return ""


def _spec_bits(attrs: dict, tier_said: bool) -> list[str]:
    """The short spec list. `tier` is dropped when the tier phrase already carried it,
    so a boot does not read "adult flagship · adult-flagship". Categories with no
    catalogue (running shoes) keep showing their vocabulary tier as before."""
    order = [a for a in _ATTR_ORDER if not (tier_said and a == "tier")]
    return [attrs[a] for a in order if a in attrs]


def _deal_line(
    product: Product,
    verdict: Verdict,
    change: Change | None,
    attrs: dict,
    base_url: str,
    memory: PriceMemory | None = None,
) -> str:
    """One deal as a markdown bullet: what it is, what it costs, why it's news."""
    bits: list[str] = []
    if change is not None and change.kind in _CHANGE_BADGE:
        bits.append(_CHANGE_BADGE[change.kind])
    emoji = _BAND_EMOJI.get(verdict.band, "")
    label = f"{emoji} " if emoji else ""
    bits.append(f"{label}[{product.title} — €{product.price:.0f}]({product.url})")

    ref = product.reference_price
    if ref and ref > product.price:
        bits.append(f"was €{ref:.0f} (-{round(100 * (1 - product.price / ref))}%)")
    if change is not None and change.kind == "price-drop" and change.previous_price:
        bits.append(f"was €{change.previous_price:.0f} last run")
    remembered = price_memory_phrase(memory)
    if remembered:
        bits.append(remembered)

    said = tier_phrase(attrs)
    if said:
        bits.append(said)
    spec = _spec_bits(attrs, bool(said))
    if spec:
        bits.append(" · ".join(spec))
    if product.source:
        bits.append(product.source)
    if base_url:
        up = feedback_link(base_url, product.url, "up")
        down = feedback_link(base_url, product.url, "down")
        bits.append(f"[👍]({up}) [👎]({down})")
    line = f"- {' · '.join(bits)}"

    caveats = [r for r in verdict.reasons if r.startswith("verify on click")]
    if caveats:
        line += f"\n  - ⚠️ {caveats[0]}"
    return line


def render_hunt_report(
    hunt: Hunt,
    results: list[tuple[Product, Verdict, Change | None]],
    feedback_base_url: str = "",
    vocab: dict | None = None,
    memory: dict[str, PriceMemory] | None = None,
) -> str:
    """Render one hunt's findings, best first, grouped by band."""
    title = hunt.label or hunt.id
    lines = [f"# dealScout — {title}\n"]
    if hunt.notes:
        lines.append(f"_{hunt.notes}_\n")

    if not results:
        lines.append("No new matches this run. The hunt stays armed and will report on change.\n")
        return "\n".join(lines)

    remembered = memory or {}
    ranked = sorted(results, key=lambda r: -r[1].score)
    for band, heading in (("must-buy", "🟢 Buy now"), ("good", "🟡 Worth it")):
        group = [r for r in ranked if r[1].band == band]
        if not group:
            continue
        lines.append(f"## {heading} — {len(group)}")
        for product, verdict, change in group:
            attrs = resolve_attrs(product, hunt, vocab)
            lines.append(
                _deal_line(
                    product,
                    verdict,
                    change,
                    attrs,
                    feedback_base_url,
                    remembered.get(canonical_url(product.url)),
                )
            )
        lines.append("")

    lines.append(
        "_You check out — dealScout never buys. Confirm size, soleplate and the "
        "retailer's delivery to your country on click._"
    )
    return "\n".join(lines)


_SHORTLIST_FOOTER = (
    "_You check out — dealScout never buys. Prices and stock move fast; confirm size, "
    "soleplate and delivery on the retailer's page before paying._"
)


NOTE_LIMIT = 60  # characters; a per-row aside, not an explanation


def short_note(note: str) -> str:
    """A delivery note trimmed to something that belongs on a row.

    ``note`` is printed beside every product from a source, so a long one is repeated ten
    times and buries the price it was meant to qualify. This has happened: a note
    explaining *how* a shipping rate was established — useful reasoning, but reasoning —
    turned each row into a paragraph. The cap keeps a config mistake from reaching the
    reader, and the reasoning belongs in a config comment where it costs nothing.
    """
    text = " ".join(note.split())
    if len(text) <= NOTE_LIMIT:
        return text
    return text[: NOTE_LIMIT - 1].rstrip(" ,;.—-") + "…"


def _shortlist_row(
    product: Product,
    hunt: Hunt,
    table: dict[str, Delivery],
    attrs: dict,
    rank: int,
    base_url: str = "",
    memory: PriceMemory | None = None,
) -> str:
    """One shortlist entry: landed cost first, because that is the number being compared."""
    delivery = delivery_for(product.source, table)
    total = landed_cost(product, delivery)
    postage = total - product.price

    head = f"**{rank}. [{product.title}]({product.url}) — €{total:.0f}**"
    if postage > 0:
        head += f" _(€{product.price:.0f} + €{postage:.0f} delivery)_"
    elif delivery.pickup:
        head += " _(collect in Rīga)_"

    facts: list[str] = []
    ref = product.reference_price
    if ref and ref > product.price:
        facts.append(f"RRP €{ref:.0f}, **-{round(100 * (1 - product.price / ref))}%**")
    # Straight after the retailer's own discount claim, because this is the line that
    # says whether that claim means anything.
    remembered = price_memory_phrase(memory)
    if remembered:
        facts.append(remembered)
    said = tier_phrase(attrs)
    if said:
        facts.append(said)
    spec = _spec_bits(attrs, bool(said))
    if spec:
        facts.append(" · ".join(spec))

    sizes = matched_sizes(product, hunt)
    if sizes:
        facts.append(f"in stock: **EU {', '.join(sizes)}**")
    elif product.sizes:
        # The unconfirmed list: show what the shop does publish, so the reader can judge.
        offered = sorted(product.sizes)[:10]
        facts.append(f"sizes listed: {', '.join(offered)}")

    facts.append(delivery.label or product.source)
    if delivery.pickup:
        facts.append("🏬 try on before buying")
    if delivery.note:
        facts.append(short_note(delivery.note))

    line = f"- {head}\n  - " + " · ".join(facts)
    if base_url:
        up = feedback_link(base_url, product.url, "up")
        down = feedback_link(base_url, product.url, "down")
        line += f" · [👍]({up}) [👎]({down})"
    return line


def _coverage_block(coverage: list[SourceCoverage]) -> list[str]:
    """The per-source breakdown: who contributed how much, out of how much, and who didn't.

    Diversity nobody can see is indistinguishable from none, so the spread is stated rather
    than left to be counted off the rows. The "found" column is what makes it answer the
    real question: six rows from one shop reads as a ranking bug until you can see that
    shop had fifteen candidates and the next two had two each.

    The silent-source line matters more than the table, but only when it means something.
    There are three ways to contribute no rows and they are not interchangeable:

    * nothing was read at all — the reader has probably broken, and this is the one that
      earns an alarm;
    * products were read but none suited the hunt — normal, and worth stating plainly so
      the reader knows the shop was checked;
    * products qualified but were beaten off a limited list — saying "nothing matched"
      there would simply be false.

    Firing the alarm for all three would make it fire most weeks, and an alarm that always
    fires is ignored by the week it matters.
    """
    contributed = [c for c in coverage if c.count]
    silent = [c for c in coverage if not c.count]
    if not contributed and not silent:
        return []

    lines = ["### Where these came from\n"]
    if contributed:
        lines.append("| Source | Rows | Cheapest | Found |")
        lines.append("|---|---:|---:|---:|")
        for row in contributed:
            price = f"€{row.cheapest:.0f}" if row.cheapest is not None else "—"
            lines.append(f"| {row.label} | {row.count} | {price} | {row.found} |")
        lines.append("")

    # Qualified but lost a place on a limited list. Unreachable while sources are fewer
    # than the row limit, but the list is growing and "nothing matched" would be a lie.
    beaten = [c for c in silent if c.found]
    # Read fine, nothing suitable: worth stating, not worth worrying about.
    quiet = [c for c in silent if c.scouted and not c.found]
    # Nothing came back at all — the reader is the likely cause.
    broken = [c for c in silent if not c.scouted]
    if beaten:
        detail = ", ".join(f"{c.label} ({c.found} qualified)" for c in beaten)
        lines.append(f"_Qualified but did not make the list: {detail}._\n")
    if quiet:
        detail = ", ".join(f"{c.label} ({c.scouted} read)" for c in quiet)
        lines.append(f"_Checked, nothing matching this hunt: {detail}._\n")
    if broken:
        names = ", ".join(c.label for c in broken)
        lines.append(
            f"_⚠️ Nothing at all from {names} this run — not even a candidate to reject. "
            f"That is usually a broken reader rather than an empty shelf, so it is worth "
            f"a look._\n"
        )
    return lines


def render_shortlist(
    hunt: Hunt,
    confirmed: list[Product],
    unconfirmed: list[Product],
    table: dict[str, Delivery],
    feedback_base_url: str = "",
    vocab: dict | None = None,
    checked: int = 0,
    sources: int = 0,
    memory: dict[str, PriceMemory] | None = None,
    coverage: list[SourceCoverage] | None = None,
) -> str:
    """The buy-now shortlist: what to buy, ranked by what it actually costs to receive."""
    remembered = memory or {}
    lines = [f"# dealScout — {hunt.label or hunt.id}\n"]
    if hunt.sizes_by_brand:
        # Naming the fallback list here would misdescribe the search: adidas is only ever
        # matched on 37⅓ and Nike only on 37.5, so say that rather than their union.
        per_brand = " · ".join(
            f"{brand} EU {', '.join(sizes)}" for brand, sizes in hunt.sizes_by_brand.items()
        )
        wanted_label = per_brand
    else:
        wanted_label = f"EU {', '.join(hunt.sizes)}"
    lines.append(
        f"_Top-tier only · {wanted_label} · ranked by **landed cost** "
        f"(price + delivery to Latvia), not shelf price._\n"
    )

    lines.append(f"## ✅ Confirmed in your size — {len(confirmed)}")
    if confirmed:
        lines.append("_The shop states these are in stock in a size you want._\n")
        for i, product in enumerate(confirmed, 1):
            attrs = resolve_attrs(product, hunt, vocab)
            lines.append(
                _shortlist_row(
                    product,
                    hunt,
                    table,
                    attrs,
                    i,
                    feedback_base_url,
                    remembered.get(canonical_url(product.url)),
                )
            )
    else:
        lines.append("_Nothing in your size right now from a shop that publishes sizes._")
    lines.append("")

    lines.append(f"## ❔ Size not published — {len(unconfirmed)}")
    if unconfirmed:
        lines.append(
            "_These shops publish a price but not per-size stock, so the size has to be "
            "checked on the page. Both are in Rīga, so they can also be phoned or "
            "visited — which is the point of listing them separately rather than "
            "pretending they are confirmed._\n"
        )
        for i, product in enumerate(unconfirmed, 1):
            attrs = resolve_attrs(product, hunt, vocab)
            lines.append(
                _shortlist_row(
                    product,
                    hunt,
                    table,
                    attrs,
                    i,
                    feedback_base_url,
                    remembered.get(canonical_url(product.url)),
                )
            )
    else:
        lines.append("_Nothing to verify this run._")
    lines.append("")

    if coverage:
        lines.extend(_coverage_block(coverage))

    if checked:
        lines.append(
            f"---\n\n_Checked {checked} products across {sources} sources. "
            f"Excluded: boots already owned, and anything the shop states is out of "
            f"stock in your size ({wanted_label})._\n"
        )
    lines.append(_SHORTLIST_FOOTER)
    return "\n".join(lines)


async def send_email(subject: str, body: str) -> bool:
    """Email the buy-signal via the shared courier service (ACS Email).

    Reads COURIER_URL, COURIER_KEY and DEALSCOUT_EMAIL_TO. If any is missing, logs
    and skips (so local/CI runs don't fail). The recipient must be on courier's
    allowlist. Returns True if courier accepted the message.
    """
    url = os.getenv("COURIER_URL")
    key = os.getenv("COURIER_KEY")
    to_addr = os.getenv("DEALSCOUT_EMAIL_TO")
    if not url or not key or not to_addr:
        logger.warning(
            "courier not configured (COURIER_URL/COURIER_KEY/DEALSCOUT_EMAIL_TO) — skipping send"
        )
        return False

    payload = {"to": to_addr, "subject": subject, "text": body}
    html = markdown_to_html(body)
    if html:
        payload["html"] = html

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                params={"code": key},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status >= 400:
                    logger.error("courier send failed: HTTP %s", resp.status)
                    return False
    except aiohttp.ClientError as exc:
        logger.error("courier send failed: %s", exc)
        return False

    logger.info("sent buy-signal email via courier to %s", to_addr)
    return True


def feedback_base_url() -> str:
    """Courier's feedback endpoint, derived from COURIER_URL (…/api/send → …/api/feedback)."""
    url = os.getenv("COURIER_URL", "")
    return url.replace("/api/send", "/api/feedback") if url else ""


async def read_feedback(project: str = "dealscout") -> list[Feedback]:
    """Read the 👍/👎 tally back from courier's export endpoint (latest vote per URL).

    Best-effort: returns [] if courier isn't configured or the call fails, so a run
    never breaks just because feedback couldn't be read.
    """
    base = feedback_base_url()
    key = os.getenv("COURIER_KEY")
    if not base or not key:
        return []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base}/export",
                params={"code": key, "p": project},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status >= 400:
                    logger.warning("courier feedback export failed: HTTP %s", resp.status)
                    return []
                text = await resp.text()
    except aiohttp.ClientError as exc:
        logger.warning("courier feedback export failed: %s", exc)
        return []
    return latest_by_url(parse_feedback_jsonl(text))

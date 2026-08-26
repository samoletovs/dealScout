"""The hunt judge — decides whether a product satisfies a declarative Hunt.

Sibling of :mod:`dealscout.judge` (which encodes the owner's *wardrobe* rules). This
one is domain-neutral: it evaluates a :class:`~dealscout.models.Hunt` spec, so the same
code judges Elite football boots in EU 37, carbon-plated running shoes, or a bike.

Three-state requirements are the important idea. A page that doesn't state the
soleplate has not *failed* the soleplate rule — it is **unknown**. Unknowns keep the
product in play but flag it and cap it below "must-buy", so the human verifies on
click. Silently dropping unknowns loses real deals; silently passing them produces
confident nonsense.

Pure and side-effect free.
"""

from __future__ import annotations

import logging

from .judge import discount_pct
from .models import Hunt, Product, Verdict
from .spec import extract_attrs, normalise_sizes

logger = logging.getLogger(__name__)

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"

# Score weights. Brand/preference bonuses are deliberately smaller than the band
# bonus: a cheap second-choice brand should still outrank a pricey first choice.
_BAND_BONUS = {"must-buy": 25.0, "good": 10.0}
_BRAND_BONUS = 24.0  # for the top-ranked brand, decaying down the list
_PREFER_BONUS = 15.0
_UNKNOWN_PENALTY = 5.0


def _ranked_bonus(value: str, ranking: tuple[str, ...], top: float) -> float:
    """Bonus for ``value``'s position in a ranked preference list (0 if absent)."""
    low = value.strip().lower()
    if not low:
        return 0.0
    for index, name in enumerate(ranking):
        candidate = str(name).strip().lower()
        if candidate and (candidate in low or low in candidate):
            return round(top * (1.0 - index / max(len(ranking), 1)), 2)
    return 0.0


def _check(actual: str | None, allowed: tuple[str, ...]) -> str:
    """Tri-state match of an extracted attribute against the allowed values."""
    if not allowed:
        return PASS
    if not actual:
        return UNKNOWN
    low = actual.strip().lower()
    return PASS if any(str(a).strip().lower() == low for a in allowed) else FAIL


def resolve_attrs(product: Product, hunt: Hunt, vocab: dict | None = None) -> dict[str, str]:
    """Attributes for a product: whatever the collector supplied, plus title-derived."""
    derived = extract_attrs(product.title, hunt.category or product.category, vocab)
    return {**derived, **product.attrs}  # collector wins over guessing from the title


def judge_hunt(
    product: Product, hunt: Hunt, vocab: dict | None = None
) -> Verdict:
    """Decide whether ``product`` satisfies ``hunt``."""
    reasons: list[str] = []
    title_low = product.title.lower()

    # --- exclusions: things we already own, or sellers we don't trust ---
    if any(u and u.split("#")[0] in product.url for u in hunt.exclude_urls):
        return Verdict(False, 0.0, ("excluded: already owned / dismissed",))
    for model in hunt.exclude_models:
        if model.strip().lower() in title_low:
            return Verdict(False, 0.0, (f"excluded: '{model}' — already owned",))
    for seller in hunt.exclude_sources:
        if seller.strip().lower() in product.source.strip().lower():
            return Verdict(False, 0.0, (f"excluded: seller {product.source}",))

    if hunt.require_new and product.condition != "new":
        return Verdict(False, 0.0, (f"rejected: condition is {product.condition}",))

    # A ranked brand list normally only *scores*. When the hunt says these brands and no
    # others, it gates: a 79%-off Skechers is not a cheap Nike, it is a different boot.
    if hunt.brands_only and hunt.brands:
        haystack = f"{product.brand} {product.title}".lower()
        if not any(b.strip().lower() in haystack for b in hunt.brands if b.strip()):
            return Verdict(
                False, 0.0, (f"rejected: brand not in {'/'.join(hunt.brands)}",)
            )

    # --- price gates ---
    if hunt.never_above is not None and product.price > hunt.never_above:
        return Verdict(
            False, 0.0, (f"rejected: €{product.price:.0f} over ceiling €{hunt.never_above:.0f}",)
        )

    unknowns: list[str] = []

    # RRP gate: the honest proxy for "is this really the flagship model".
    if hunt.min_reference_price is not None:
        ref = product.reference_price
        if ref is None:
            unknowns.append("RRP")
        elif ref < hunt.min_reference_price:
            return Verdict(
                False,
                0.0,
                (f"rejected: RRP €{ref:.0f} < €{hunt.min_reference_price:.0f} — not a flagship",),
            )

    dpct = discount_pct(product.price, product.reference_price)
    if hunt.min_discount_pct and product.reference_price:
        if dpct < hunt.min_discount_pct:
            return Verdict(
                False, 0.0, (f"rejected: {dpct:.0f}% off < {hunt.min_discount_pct:.0f}% required",)
            )

    # --- attribute requirements ---
    attrs = resolve_attrs(product, hunt, vocab)
    for attr, allowed in hunt.require.items():
        state = _check(attrs.get(attr), allowed)
        if state == FAIL:
            return Verdict(
                False, 0.0, (f"rejected: {attr}={attrs.get(attr)} not in {'/'.join(allowed)}",)
            )
        if state == UNKNOWN:
            if attr in hunt.require_stated:
                return Verdict(
                    False, 0.0, (f"rejected: {attr} not stated — cannot be confirmed",)
                )
            unknowns.append(attr)

    # --- size availability ---
    wanted_sizes = hunt.sizes_for(product.brand, product.title)
    if wanted_sizes:
        available = normalise_sizes(wanted_sizes) & normalise_sizes(product.sizes)
        if not product.sizes_known:
            unknowns.append("size")
        elif available:
            reasons.append(f"size {'/'.join(sorted(available))} in stock")
        elif hunt.require_size_in_stock:
            return Verdict(
                False, 0.0, (f"rejected: size {'/'.join(wanted_sizes)} not in stock",)
            )
        else:
            # The page listed its sizes and ours was not among them. This hunt opted out
            # of the hard gate, so flag it — but never claim stock we did not see.
            unknowns.append("size")

    # --- band ---
    if hunt.must_buy is not None and product.price <= hunt.must_buy:
        band = "must-buy"
    elif hunt.good_offer is not None and product.price <= hunt.good_offer:
        band = "good"
    else:
        band = "regular"

    # An unverified claim must never be presented as a certainty.
    if unknowns and band == "must-buy":
        band = "good"

    if dpct > 0:
        reasons.append(f"{dpct:.0f}% off (was €{product.reference_price:.0f})")
    reasons.append(f"€{product.price:.0f} → {band}")

    # --- preference scoring (never gates) ---
    score = dpct + _BAND_BONUS.get(band, 0.0)
    brand_bonus = _ranked_bonus(product.brand or product.title, hunt.brands, _BRAND_BONUS)
    if brand_bonus:
        score += brand_bonus
    for attr, ranking in hunt.prefer.items():
        value = attrs.get(attr)
        if not value:
            continue
        bonus = _ranked_bonus(value, ranking, _PREFER_BONUS)
        if bonus:
            score += bonus
            reasons.append(f"preferred {attr}: {value}")

    for attr in sorted(set(attrs) - set(hunt.require) - set(hunt.prefer)):
        if attr in {"silo", "tier"}:
            reasons.append(f"{attr}: {attrs[attr]}")

    if unknowns:
        score -= _UNKNOWN_PENALTY * len(unknowns)
        reasons.append("verify on click: " + ", ".join(sorted(set(unknowns))))

    is_deal = band in {"must-buy", "good"}
    if not is_deal:
        reasons.append("above target price — skip")

    return Verdict(is_deal, round(score, 1), tuple(reasons), band)

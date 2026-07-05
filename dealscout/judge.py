"""The deal judge — decides whether a product is an unmissable bargain.

Encodes the owner's rules (see the dealScout profile):
  - exceptional bargains only: deep discount AND under the "can't-say-no" price
  - quality bar: natural fibre, no big logos, machine-washable
  - quality signals add score but do not gate

Pure and side-effect free, so it is easy to unit-test.
"""

from __future__ import annotations

import logging

from .models import Product, Verdict

logger = logging.getLogger(__name__)

NATURAL_FIBRES = frozenset(
    {"wool", "cotton", "linen", "cashmere", "silk", "merino", "alpaca"}
)


def natural_fibre_ratio(materials: dict[str, float]) -> float:
    """Return the share (0..1) of natural fibre in a composition."""
    total = sum(materials.values())
    if total <= 0:
        return 0.0
    natural = sum(
        v for k, v in materials.items()
        if any(fibre in k.lower() for fibre in NATURAL_FIBRES)
    )
    return natural / total


def discount_pct(price: float, reference_price: float | None) -> float:
    """Return the discount percentage vs the reference price (0 if unknown)."""
    if not reference_price or reference_price <= 0:
        return 0.0
    return max(0.0, (reference_price - price) / reference_price * 100.0)


def brand_tier(brand: str, brands: dict) -> str:
    """Resolve a brand to a tier: 'better' / 'basket' / 'worse' / 'unknown'.

    Case-insensitive; 'local' (Baltic quality finds) counts as basket. Resolve
    diffusion lines to the sub-label before calling (e.g. 'Tommy Jeans' vs 'Tommy Hilfiger').
    """
    needle = brand.strip().lower()
    if not needle:
        return "unknown"

    def has(tier: str) -> bool:
        return any(
            str(name).lower() in needle or needle in str(name).lower()
            for name in brands.get(tier, [])
        )

    if has("better"):
        return "better"
    if has("basket") or has("local"):
        return "basket"
    if has("worse"):
        return "worse"
    return "unknown"


def judge(product: Product, config: dict) -> Verdict:
    """Decide whether a product is an unmissable, on-profile bargain."""
    filters = config.get("filters", {})
    deal = config.get("deal", {})
    reasons: list[str] = []

    # --- hard filters: any failure means "not a deal" ---
    if filters.get("reject_big_wordmarks", True) and product.has_big_logo:
        return Verdict(False, 0.0, ("rejected: big logo/wordmark",))

    # brand tier: never buy below your level
    min_tier = filters.get("min_brand_tier", "any")
    tier = brand_tier(product.brand, config.get("brands", {}))
    if min_tier == "basket" and tier == "worse":
        return Verdict(False, 0.0, (f"rejected: {product.brand or 'brand'} is below your tier",))
    if min_tier == "better" and tier != "better":
        return Verdict(False, 0.0, (f"rejected: {product.brand or 'brand'} not above your tier",))

    min_natural = float(filters.get("natural_fibre_min", 0.0))
    ratio = natural_fibre_ratio(product.materials)
    is_sportswear = product.category.lower() in {"sportswear", "activewear"}
    tolerate_synthetic = is_sportswear and filters.get("sportswear_synthetic_ok", True)
    if min_natural and ratio < min_natural and not tolerate_synthetic:
        return Verdict(False, 0.0, (f"rejected: natural fibre {ratio:.0%} < {min_natural:.0%}",))

    if filters.get("care_no_dry_clean_only", False) and "dry clean only" in product.care.lower():
        return Verdict(False, 0.0, ("rejected: dry-clean-only",))

    never_above = deal.get("never_above", {}).get(product.category)
    if never_above is not None and product.price > float(never_above):
        return Verdict(False, 0.0, (f"rejected: €{product.price:.0f} over never-above €{never_above}",))

    # --- deal test: exceptional only (deep discount AND under the ceiling) ---
    dpct = discount_pct(product.price, product.reference_price)
    min_discount = float(deal.get("min_discount_pct", 0))
    ceiling = deal.get("cant_say_no", {}).get(product.category)

    deep_discount = dpct >= min_discount
    under_ceiling = ceiling is not None and product.price <= float(ceiling)

    if deep_discount:
        reasons.append(f"{dpct:.0f}% off")
    if under_ceiling:
        reasons.append(f"€{product.price:.0f} <= can't-say-no €{ceiling}")

    is_deal = (deep_discount and under_ceiling) if ceiling is not None else deep_discount

    # --- quality + brand bonus: adds score, never gates ---
    wanted = set(filters.get("quality_signals", []))
    matched = wanted & set(product.quality_signals)
    if "natural_fibre" in wanted and ratio > 0 and ratio >= min_natural:
        matched.add("natural_fibre")
    if matched:
        reasons.append("quality: " + ", ".join(sorted(matched)))

    score = dpct + 5.0 * len(matched)
    if tier == "better":
        reasons.append(f"trade-up brand: {product.brand}")
        score += 10.0
    elif tier == "basket" and product.brand:
        reasons.append(f"your tier: {product.brand}")

    if not is_deal:
        reasons.append("not exceptional enough")

    return Verdict(is_deal, round(score, 1), tuple(reasons))

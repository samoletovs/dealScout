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
from .spec import extract_attrs, merge_vocab, normalise_sizes
from . import catalogue

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


def attrs_from_title(
    title: str,
    category: str,
    vocab: dict | None = None,
    brand: str = "",
    reference_price: float | None = None,
) -> dict[str, str]:
    """Read a title's attributes: catalogue first, vocabulary as fallback.

    The single place tier is resolved from a name. It exists because it was once resolved
    in two places by two different rules: the judge learned to consult the catalogue while
    the scout's pre-filter kept reading the vocabulary, so `elite` — a value the catalogue
    no longer assigns — silently contradicted `require: tier: [adult-flagship, ...]` and
    two whole retailers were discarded before a request was ever spent. Anything that
    needs attributes from a title must call this, so the two can never disagree again.

    Order matters. ``extract_attrs`` returns the first match in *declaration order*, so
    ``elite`` shadows ``academy`` and `Diadora Maximus Elite Academy` is unfixable in the
    vocabulary. Where a catalogue exists it therefore **owns** its attributes outright,
    including the right to supply none: a catalogue that declines to name a tier must not
    have the vocabulary's guess quietly restored underneath it.
    """
    derived = extract_attrs(title, category, vocab)
    known = catalogue.load(category)
    if known is not None:
        for name in catalogue.MANAGED_ATTRS:
            derived.pop(name, None)
        derived.update(known.classify(title, brand, reference_price).as_attrs())
    return derived


def resolve_attrs(product: Product, hunt: Hunt, vocab: dict | None = None) -> dict[str, str]:
    """Attributes for a product: catalogue first, then the vocabulary, then the collector."""
    derived = attrs_from_title(
        product.title,
        hunt.category or product.category,
        vocab,
        product.brand,
        product.reference_price,
    )
    return {**derived, **product.attrs}  # collector wins over both


def product_identity(
    product: Product, hunt: Hunt, vocab: dict | None = None
) -> tuple[str, str]:
    """Resolve a product to ``(boot_key, size)`` for the price log.

    The price log must key on the *boot*, not the retailer's URL, or "cheapest seen" is a
    claim about one listing rather than about the boot at every shop. The boot key comes
    from the same catalogue/vocabulary reading the judge uses, so the log can never call a
    boot something the judge would not. ``size`` is the one wanted size this product is
    confirmed in — the price log is per-size, because a junior EU 37 and an adult EU 44 of
    the "same" model are different products at different prices. It is empty when the shop
    never stated sizes (``unknown``, not ``out of stock``) or the boot is confirmed in more
    than one wanted size, since a single line cannot honestly stand for several.

    The brand read by the catalogue is folded back in, because a single-brand shop leaves
    its own brand out of the title and the boot key needs it to be stable across retailers.
    """
    from .pricehistory import boot_key  # local import: pricehistory imports models only

    attrs = resolve_attrs(product, hunt, vocab)
    attrs.setdefault("brand", product.brand)
    if not attrs.get("brand") and hunt.brands:
        low = product.title.lower()
        attrs["brand"] = next((b for b in hunt.brands if b.strip().lower() in low), "")
    key = boot_key(attrs)

    size = ""
    if product.sizes_known:
        wanted = normalise_sizes(hunt.sizes_for(product.brand, product.title))
        matched = sorted(wanted & normalise_sizes(product.sizes))
        if len(matched) == 1:
            size = matched[0]
    return key, size


def known_values(category: str, vocab: dict | None = None) -> dict[str, frozenset[str]]:
    """Every value the engine can produce per attribute, for validating a hunt."""
    table = (vocab if vocab is not None else merge_vocab(None)).get(category, {})
    values = {attr: frozenset(vals) for attr, vals in table.items()}
    known = catalogue.load(category)
    if known is not None:
        values["tier"] = known.tier_values()
        values["generation_status"] = known.status_values()
        # Free-text: the catalogue names lines and generations from data, so there is no
        # closed set to check a config against.
        values.pop("silo", None)
        values.pop("generation", None)
    return values


def validate_hunt(hunt: Hunt, vocab: dict | None = None) -> None:
    """Raise if the hunt requires a value the engine can never assign.

    A stale `require:` is the most dangerous kind of config rot in this tool. The owner's
    real hunt lives in a private `config.local.yaml`; a value that quietly stops matching
    turns it into a hunt that finds nothing and — because silence is the normal, correct
    output most days — says nothing about why. Better to fail on the first run than on the
    day he wonders where the emails went.

    ``prefer`` only scores, so a stale value there is logged rather than raised.
    """
    category = hunt.category
    if not category:
        return
    values = known_values(category, vocab)
    for attr, allowed in hunt.require.items():
        permitted = values.get(attr)
        if permitted is None:
            continue  # supplied by the collector, not by us — nothing to check against
        # Case-insensitive on both sides, matching how `_check` compares at judge time —
        # config writes soleplates as "AG", the vocabulary declares them the same way, and
        # a validator stricter than the judge would reject working config.
        folded = {str(v).strip().lower() for v in permitted}
        unknown = [v for v in allowed if str(v).strip().lower() not in folded]
        if unknown:
            raise ValueError(
                f"hunt {hunt.id!r}: require.{attr} names {', '.join(map(repr, unknown))}, "
                f"which this engine never assigns. Valid: {', '.join(sorted(permitted))}."
            )
    for attr, ranked in hunt.prefer.items():
        permitted = values.get(attr)
        if permitted is None:
            continue
        folded = {str(v).strip().lower() for v in permitted}
        stale = [v for v in ranked if str(v).strip().lower() not in folded]
        if stale:
            logger.warning(
                "hunt %s: prefer.%s names %s, which is never assigned — it will not score",
                hunt.id, attr, ", ".join(map(repr, stale)),
            )


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

    # --- attribute requirements ---
    # Resolved BEFORE the RRP gate, because whether that gate is needed depends on
    # whether the attributes it stands in for are already known.
    attrs = resolve_attrs(product, hunt, vocab)
    established = 0
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
        else:
            established += 1

    # RRP gate. Config calls this "proves it is a flagship, not a lookalike" — it is a
    # *proxy* for tier, and now only a fallback for when the real signal is missing. Every
    # requirement having been positively established means the question it approximates is
    # already answered, and enforcing it anyway rejects genuine finds: the junior Copa Pure
    # Elite lists at RRP €90, which is the top of the junior range and well under any gate
    # written for adult boots.
    tier_is_known = bool(hunt.require) and established == len(hunt.require)
    if hunt.min_reference_price is not None and not tier_is_known:
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

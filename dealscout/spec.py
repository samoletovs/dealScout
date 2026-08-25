"""Spec extraction — read structured attributes out of a product title.

The wardrobe judge asks "what is this made of?". A boot hunt asks "is this the
top-tier model, and is the soleplate AG or FG?". Rather than hardcode either, the
engine reads a **vocabulary**: a declarative map of ``category -> attribute -> value ->
patterns``. Adding running shoes, skis or headphones is a config change, not a code
change.

``DEFAULT_VOCAB`` ships the domain knowledge (which is not user data, so it belongs
in code alongside ``judge.NATURAL_FIBRES``); a config ``vocab:`` block is merged over
it, so a user can extend or correct it without a release.

Everything here is pure and unit-tested.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

# Domain vocabulary. Values are tried in declaration order, so the FIRST match wins:
# list the strongest/most-preferred reading first (a boot sold as "FG/AG" resolves to
# AG, which is correct — it is AG-capable).
DEFAULT_VOCAB: dict[str, dict[str, dict[str, list[str]]]] = {
    "football_boots": {
        # Top tier is the athlete boot (RRP €200+). Everything else is a takedown
        # version: same silo name, cheaper materials, plastic soleplate.
        "tier": {
            "elite": [
                "elite",  # Nike + adidas top tier
                "ultimate",  # Puma top tier (Future / Ultra Ultimate)
                "premium",  # Mizuno Morelia Neo / Alpha "Made in Japan" tier
                "pro edition",
            ],
            "mid": ["pro", "academy", "league", "match", "competition", "advanced"],
            "entry": ["club", "play", "academy jr", "team", "sala", "essential"],
        },
        "soleplate": {
            "AG": ["ag", "ag pro", "artificial ground", "artificial grass"],
            "FG": ["fg", "firm ground"],
            "SG": ["sg", "sg pro", "soft ground"],
            "MG": ["mg", "multi ground", "multi-ground"],
            "TF": ["tf", "turf", "astro turf"],
            "IC": ["ic", "indoor", "futsal", "sala"],
        },
        "silo": {
            # Nike
            "mercurial superfly": ["mercurial superfly", "superfly"],
            "mercurial vapor": ["mercurial vapor", "vapor"],
            "phantom": ["phantom", "phantom gx", "phantom luna", "phantom gt"],
            "tiempo legend": ["tiempo legend", "tiempo", "legend"],
            # adidas
            "predator": ["predator"],
            "f50": ["f50", "x crazyfast", "x speedportal"],
            "copa": ["copa pure", "copa sense", "copa"],
            # Puma
            "future": ["future"],
            "ultra": ["ultra"],
            # Others worth knowing
            "morelia": ["morelia neo", "morelia"],
            "tiempo emerald": ["emerald"],
        },
        "fit": {
            "junior": ["jr", "junior", "kids", "kid", "youth", "children", "boys", "girls"],
            "senior": ["senior", "adult", "mens", "men"],
        },
    },
    "running_shoes": {
        "tier": {
            "elite": [
                "elite",
                "adios pro",
                "endorphin pro",
                "alphafly",
                "vaporfly",
                "metaspeed",
                "cielo x1",
                "endorphin elite",
                "fuelcell supercomp",
            ],
            "mid": ["boston", "tempo", "zoom fly", "endorphin speed", "rebel", "mach"],
            "entry": ["pegasus", "ride", "clifton", "supernova", "winflo", "revolution"],
        },
        "plate": {
            "carbon": ["carbon", "carbon plate", "carbitex"],
            "nylon": ["nylon plate", "pebax plate"],
        },
        "surface": {
            "trail": ["trail", "speedgoat", "terrex", "peregrine"],
            "road": ["road"],
        },
    },
}

_SIZE_PREFIX_RE = re.compile(r"^\s*(?:euro|eur|eu|size|sz|storlek|razmer)[\s.:]*", re.IGNORECASE)
_SIZE_NUM_RE = re.compile(r"^(\d{1,2})(?:[.,](\d))?$")
_APOSTROPHE_RE = re.compile(r"[''`\u2019]")


def _pattern(token: str) -> re.Pattern[str]:
    """Compile a vocabulary token into a tolerant, boundary-safe matcher.

    Spaces in a token match any run of separators, so ``"firm ground"`` also matches
    ``Firm-Ground`` and ``firm/ground``. Lookarounds (rather than ``\\b``) keep short
    tokens like ``ag`` from matching inside ``vantage`` while still matching ``AG-PRO``.
    """
    parts = [re.escape(p) for p in token.strip().lower().split()]
    body = r"[\s\-/_.]+".join(parts)
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])", re.IGNORECASE)


def merge_vocab(overrides: dict[str, Any] | None) -> dict[str, dict[str, dict[str, list[str]]]]:
    """Merge a config ``vocab:`` block over ``DEFAULT_VOCAB`` (per attribute).

    A user-supplied attribute REPLACES the default for that attribute, so a wrong
    default can be corrected outright rather than only added to.
    """
    merged = {cat: {a: dict(v) for a, v in attrs.items()} for cat, attrs in DEFAULT_VOCAB.items()}
    for category, attrs in (overrides or {}).items():
        if not isinstance(attrs, dict):
            continue
        target = merged.setdefault(str(category), {})
        for attr, values in attrs.items():
            if isinstance(values, dict):
                target[str(attr)] = {str(k): list(v or []) for k, v in values.items()}
    return merged


def extract_attrs(title: str, category: str, vocab: dict | None = None) -> dict[str, str]:
    """Read the attributes of ``category`` out of a product title.

    Returns only attributes actually found — a missing key means "the title didn't
    say", which callers must treat as *unknown*, never as *absent*.
    """
    table = (vocab if vocab is not None else merge_vocab(None)).get(category, {})
    if not table:
        return {}
    # "Kids'" and "Kids’" must both match the token "kids".
    haystack = _APOSTROPHE_RE.sub("", title)
    found: dict[str, str] = {}
    for attr, values in table.items():
        for value, tokens in values.items():
            if any(_pattern(t).search(haystack) for t in tokens if str(t).strip()):
                found[attr] = value
                break
    return found


def normalise_size(raw: object) -> str:
    """Normalise a size label to a canonical string ('EU 37,5' -> '37.5', '38.0' -> '38').

    Returns "" when the value isn't a plain numeric size, so callers can skip it
    rather than compare noise.
    """
    text = _SIZE_PREFIX_RE.sub("", str(raw or "")).strip()
    text = text.split("/")[0].strip()  # "37.5 / UK 4.5" -> EU part
    match = _SIZE_NUM_RE.match(text)
    if not match:
        return ""
    whole, frac = match.group(1), match.group(2)
    if frac and frac != "0":
        return f"{whole}.{frac}"
    return whole


def normalise_sizes(values: Iterable[object]) -> frozenset[str]:
    """Normalise a collection of size labels, dropping anything unparseable."""
    return frozenset(s for s in (normalise_size(v) for v in values) if s)


# Below this, a number is not a European shoe size. UK sizing runs 1-13, so a bare "4.5"
# from a British retailer normalises perfectly and means EU 37-ish, not EU 4.5.
EU_SIZE_FLOOR = 20.0


def looks_like_eu(sizes: Iterable[str]) -> bool:
    """True when a size set plausibly uses EU sizing at all.

    Reading a UK size table as EU would reject every boot that actually fits, and a
    confident wrong answer is the one failure mode a co-pilot must not have. Callers
    treat a False here as "the page stated sizes, but not in a system we can read" —
    i.e. unknown, so the human verifies — rather than as "the size is unavailable".
    """
    for size in sizes:
        try:
            if float(size) >= EU_SIZE_FLOOR:
                return True
        except (TypeError, ValueError):
            continue
    return False


def size_matches(wanted: Iterable[str], available: Iterable[str]) -> bool:
    """True when any wanted size is available (both normalised before comparing)."""
    want = normalise_sizes(wanted)
    have = normalise_sizes(available)
    return bool(want & have)

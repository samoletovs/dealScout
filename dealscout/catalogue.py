"""Catalogue — read a product title against a brand's real tier ladder.

The engine used to answer "is this the flagship?" with ``"elite" in title``. That is wrong
in both directions, and expensively so:

* `Diadora Maximus Elite Academy FG`, RRP €70, is an **Academy** boot. "Maximus Elite" is
  the range name; the tier word comes after it.
* `Mizuno Morelia II Elite` is Mizuno's **second** tier (~€150). Their flagship is
  "Made in Japan" — so for Mizuno the word "Elite" means the opposite of what it means
  for Nike.
* Puma's flagship is **Ultimate**. A Puma boot saying "Elite" is not the top line.
* A junior Elite (€120–140) is a **different boot** from an adult Elite (€250–295):
  softer plate, thicker upper, wider last. Not the same boot in small sizes.

Substring matching cannot reach any of this, and it could not be fixed by adding words
to the vocabulary: :func:`dealscout.spec.extract_attrs` returns the first match in
declaration order, so ``elite`` shadows ``academy`` and no token list makes the second
reachable. The catalogue is therefore consulted **before** the vocabulary, and owns the
attributes it supplies.

**It classifies; it does not filter.** The owner is buying for a child, so a junior
flagship is exactly what he might want — deleting it would be the worse error. The four
outcomes are ``adult-flagship``, ``junior-flagship``, ``takedown`` and ``unknown``, and
the last is a real answer: an unrecognised brand, or a title with no tier word, produces
``unknown`` rather than a guess. What the tool owes the owner is that a €130 junior boot
can never be presented as though it were a €280 adult one.

Generation status is carried alongside and is deliberately *not* a quality judgement. A
superseded adult flagship at 60% off is the best find this tool can make; the same
discount on an eight-year-old junior boot usually is not. Both are surfaced, labelled,
and left to the human.

The knowledge lives in ``data/<category>.yaml``, not here, so a new season or a new brand
is a data edit rather than a release. That file also carries ``last_verified``: generation
status is a snapshot of one season and **will** go stale silently, so a status that looks
wrong should send you to the date before the parser.
"""

from __future__ import annotations

import functools
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

ADULT_FLAGSHIP = "adult-flagship"
JUNIOR_FLAGSHIP = "junior-flagship"
TAKEDOWN = "takedown"
UNKNOWN = "unknown"

#: Every tier a catalogue can return. ``unknown`` is a value, not the absence of one.
TIERS: tuple[str, ...] = (ADULT_FLAGSHIP, JUNIOR_FLAGSHIP, TAKEDOWN, UNKNOWN)
#: Generation statuses. ``evergreen`` covers boots outside the tier ladder (Copa Mundial).
STATUSES: tuple[str, ...] = ("current", "superseded", "discontinued", "evergreen")

#: Attributes the catalogue owns outright. Where a catalogue exists for a category these
#: REPLACE the vocabulary's reading rather than merging with it — including the right to
#: say nothing, which is the whole point of consulting it first.
MANAGED_ATTRS: frozenset[str] = frozenset(
    {"tier", "silo", "generation", "generation_status", "generation_year"}
)


@dataclass(frozen=True)
class BootTier:
    """What the catalogue can say about one product title.

    Every field may be empty: a title that names no generation has no generation, and
    saying so is more useful than inventing one.
    """

    tier: str = UNKNOWN
    brand: str = ""
    line: str = ""  # model line / silo, e.g. "mercurial superfly"
    generation: str = ""  # e.g. "10", or a name like "phantom gx"
    status: str = ""  # current | superseded | discontinued | evergreen
    year: int | None = None
    launch_rrp_eur: float | None = None
    rrp_band: tuple[float, float] | None = None  # what this tier normally launches at
    note: str = ""

    @property
    def is_flagship(self) -> bool:
        """True for either flagship. Junior and adult are both top-of-range."""
        return self.tier in {ADULT_FLAGSHIP, JUNIOR_FLAGSHIP}

    @property
    def known(self) -> bool:
        return self.tier != UNKNOWN

    def as_attrs(self) -> dict[str, str]:
        """The catalogue's reading as engine attributes (omitting what it cannot say)."""
        attrs: dict[str, str] = {}
        if self.tier != UNKNOWN:
            attrs["tier"] = self.tier
        if self.line:
            attrs["silo"] = self.line
        if self.generation:
            attrs["generation"] = self.generation
        if self.status:
            attrs["generation_status"] = self.status
        if self.year:
            attrs["generation_year"] = str(self.year)
        return attrs


def _token(text: str) -> re.Pattern[str]:
    """Boundary-safe matcher for a catalogue token.

    Splits on any non-alphanumeric run, so a token written ``b-elite`` still matches a
    title normalised to ``b elite`` — Diadora writes it both ways, and so do retailers
    writing ``SG-Pro`` against ``SG Pro``. Lookarounds rather than ``\\b`` so ``elite``
    matches inside ``B-Elite`` while ``ag`` still refuses to match inside ``vantage``.
    """
    parts = [re.escape(p) for p in re.split(r"[^a-z0-9]+", text.strip().lower()) if p]
    if not parts:
        return re.compile(r"(?!x)x")  # matches nothing, rather than everything
    body = r"[\s\-/_.]+".join(parts)
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])", re.IGNORECASE)


@functools.lru_cache(maxsize=512)
def _compiled_token(text: str) -> re.Pattern[str]:
    return _token(text)


@dataclass(frozen=True)
class Catalogue:
    """A loaded category catalogue. Pure: classification never touches the filesystem."""

    category: str
    last_verified: str
    brands: dict[str, dict]
    junior_markers: tuple[str, ...]
    soleplate_suffixes: tuple[str, ...]
    noise: tuple[str, ...]
    roman_numerals: dict[str, str]

    # -- normalisation ---------------------------------------------------------------

    def normalise(self, title: str) -> str:
        """Lower-case, drop apostrophes, and fold Roman generation numerals to Arabic.

        Folding matters: retailers write the same boot as `Superfly XI` and `Superfly 11`,
        and the Tiempo `Legend X` / `Legend 10` collision has already cost this repo an
        ``exclude_models`` line.
        """
        text = re.sub(r"['\u2019`]", "", title.lower())
        text = re.sub(r"[^a-z0-9+()]+", " ", text)

        def fold(match: re.Match[str]) -> str:
            return self.roman_numerals.get(match.group(0), match.group(0))

        # Only whole words, so the "x" in "X Crazyfast" is not turned into "10" unless it
        # stands alone as a generation — which is why the F50 lineage keeps its own patterns.
        return re.sub(r"(?<![a-z0-9])[ivx]{1,5}(?![a-z0-9])", fold, text).strip()

    def _strip(self, text: str, tokens: tuple[str, ...]) -> str:
        for token in tokens:
            text = _compiled_token(token).sub(" ", text)
        return re.sub(r"\s+", " ", text).strip()

    def is_junior(self, normalised: str) -> bool:
        return any(_compiled_token(m).search(normalised) for m in self.junior_markers)

    # -- lookup ----------------------------------------------------------------------

    def brand_for(self, normalised: str, declared: str = "") -> str:
        """The catalogue brand this product belongs to, or "" if none is recognised.

        The declared brand field wins over the title: a single-brand retailer often omits
        its own name from the product name but does set the field.

        Failing both, the **model line** identifies the brand: teamsport.lv is Nike's
        Latvian distributor and lists "ZM SUPERFLY 10 ELITE SG-PRO" with the word Nike
        nowhere in it, and only one brand in this catalogue makes a Superfly. An ambiguous
        line names no brand rather than guessing between them.
        """
        declared_low = re.sub(r"[^a-z0-9 ]+", "", declared.lower()).strip()
        for name in self.brands:
            if declared_low and _compiled_token(name).search(declared_low):
                return name
        for name in self.brands:
            if _compiled_token(name).search(normalised):
                return name
        by_line = {
            name
            for name, spec in self.brands.items()
            for line in (spec.get("lines") or {}).values()
            if any(_compiled_token(p).search(normalised) for p in line.get("patterns") or [])
        }
        return by_line.pop() if len(by_line) == 1 else ""

    def _first_hit(self, text: str, tokens: list[str]) -> int:
        """Position of the earliest of these tokens, or -1."""
        hits = [m.start() for t in tokens if (m := _compiled_token(str(t)).search(text))]
        return min(hits) if hits else -1

    def _stated_generation(self, normalised: str, patterns: list) -> str:
        """The generation number a title states for this line, or "" if it states none.

        Read from immediately after the line's own name, so `copa pure 4` states 4 while
        `predator elite` states nothing. Roman numerals are already digits by this point.
        """
        for pattern in patterns:
            match = _compiled_token(str(pattern)).search(normalised)
            if not match:
                continue
            stated = re.match(r"\s*(\d{1,2})\b", normalised[match.end() : match.end() + 5])
            if stated:
                return stated.group(1)
        return ""

    def _line_and_generation(self, normalised: str, brand: str) -> tuple[str, dict]:
        """Match the model line, then the most specific generation within it."""
        for line, spec in (self.brands.get(brand, {}).get("lines") or {}).items():
            line_patterns = spec.get("patterns") or []
            if not any(_compiled_token(p).search(normalised) for p in line_patterns):
                continue
            stated = self._stated_generation(normalised, line_patterns)
            for generation in spec.get("generations") or []:
                patterns = [str(p) for p in (generation.get("patterns") or [])]
                if not any(_compiled_token(p).search(normalised) for p in patterns):
                    continue
                # A title naming a generation must not be answered by an entry that names
                # none. `copa pure` is listed as the 2022 original, and being a prefix of
                # every later name it swallowed them: a Copa Pure IV — newer than anything
                # in this file — was reported as "discontinued generation (2022)". That is
                # the worst direction to be wrong in, because it argues the owner out of a
                # current boot, and it arrives silently on the day a brand ships a number
                # we have not seen. Falling through to "generation unknown" is the honest
                # answer, and the one the renderer already knows how to say nothing about.
                if stated and not any(any(c.isdigit() for c in p) for p in patterns):
                    continue
                return line, generation
            return line, {}  # line recognised, generation not stated
        return "", {}

    # -- the decision ----------------------------------------------------------------

    def classify(self, title: str, brand: str = "", rrp: float | None = None) -> BootTier:
        """Classify one product title. Pure, and never raises on odd input.

        ``rrp`` is used for ONE thing only: deciding adult vs junior when the title gives
        no junior marker. That is a fit question, not a tier one, and price is legitimate
        evidence for it — komanda.lv lists a genuine junior boot as "adidas Predator Elite
        LL FG" with nothing in the title to say so. Whether a boot is a flagship or a
        takedown stays title-only, because a discounted flagship is exactly what this tool
        exists to find and letting price answer that question would hide every one of them.
        """
        normalised = self.normalise(title)
        resolved_brand = self.brand_for(normalised, brand)
        if not resolved_brand:
            return BootTier(tier=UNKNOWN)

        spec = self.brands[resolved_brand]
        line, generation = self._line_and_generation(normalised, resolved_brand)

        # Soleplates first: "SG-Pro" and "AG-Pro" would otherwise read as the Pro tier and
        # demote every soft-ground flagship. Then colourways, packs and variant badges.
        cleaned = self._strip(normalised, self.soleplate_suffixes)
        cleaned = self._strip(cleaned, self.noise)

        flagship_at = self._first_hit(cleaned, list(spec.get("flagship") or []))
        if flagship_at < 0:
            flagship_at = self._legacy_flagship(cleaned, spec)
        takedown_at = self._first_hit(cleaned, list(spec.get("takedown") or []))

        bands = spec.get("rrp_bands") or {}
        tier = self._tier_from(flagship_at, takedown_at, self.is_junior(normalised))
        note = str(generation.get("note") or "") if generation else ""
        if tier == ADULT_FLAGSHIP and self._priced_as_junior(rrp, bands):
            tier = JUNIOR_FLAGSHIP
            note = "junior inferred from RRP — the title carries no junior marker"

        band = bands.get(tier)
        return BootTier(
            tier=tier,
            brand=resolved_brand,
            line=line,
            generation=str(generation.get("gen") or "") if generation else "",
            status=str(generation.get("status") or "") if generation else "",
            year=generation.get("year") if generation else None,
            launch_rrp_eur=generation.get("launch_rrp_eur") if generation else None,
            rrp_band=(float(band[0]), float(band[1])) if band and len(band) == 2 else None,
            note=note,
        )

    @staticmethod
    def _priced_as_junior(rrp: float | None, bands: dict) -> bool:
        """True when a stated RRP sits in the junior band and below the adult one.

        Deliberately narrow. It fires only on a *stated* price that is unambiguous — an
        adult flagship discounted to €130 keeps its RRP of €280, so this cannot demote
        one. A missing RRP infers nothing, which is the common case at official dealers
        who publish no "was" price at all.
        """
        junior, adult = bands.get(JUNIOR_FLAGSHIP), bands.get(ADULT_FLAGSHIP)
        if rrp is None or not junior or not adult or len(junior) != 2 or len(adult) != 2:
            return False
        return float(junior[0]) <= rrp <= float(junior[1]) and rrp < float(adult[0])

    @staticmethod
    def _legacy_flagship(cleaned: str, spec: dict) -> int:
        """adidas marked the top tier with "+" before July 2024 — Predator+, Copa Pure+."""
        hits = [
            m.start()
            for raw in (spec.get("flagship_patterns") or [])
            if (m := re.search(str(raw), cleaned, re.IGNORECASE))
        ]
        return min(hits) if hits else -1

    @staticmethod
    def _tier_from(flagship_at: int, takedown_at: int, junior: bool) -> str:
        """Later tier word wins when both appear — which settles Diadora and Mizuno at once.

        `Diadora Maximus Elite Academy`: "Elite" belongs to the range name and "Academy"
        is the tier, so the later word is right. `Mizuno Morelia II Elite Made in Japan`:
        "Elite" is Mizuno's second tier and "Made in Japan" is the qualifier above it —
        again the later word. One rule, no per-brand branch.
        """
        if flagship_at >= 0 and takedown_at > flagship_at:
            return TAKEDOWN
        if flagship_at >= 0:
            return JUNIOR_FLAGSHIP if junior else ADULT_FLAGSHIP
        if takedown_at >= 0:
            return TAKEDOWN
        return UNKNOWN

    def tier_values(self) -> frozenset[str]:
        """Every tier this catalogue can assign — used to validate a hunt's `require`."""
        return frozenset(TIERS)

    def status_values(self) -> frozenset[str]:
        return frozenset(STATUSES)


def _as_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(v).strip().lower() for v in value if str(v).strip())


def build(data: dict[str, Any]) -> Catalogue:
    """Build a Catalogue from a parsed data file (pure — no I/O)."""
    return Catalogue(
        category=str(data.get("category") or ""),
        last_verified=str(data.get("last_verified") or ""),
        brands={str(k).lower(): (v or {}) for k, v in (data.get("brands") or {}).items()},
        junior_markers=_as_tuple(data.get("junior_markers")),
        soleplate_suffixes=_as_tuple(data.get("soleplate_suffixes")),
        noise=_as_tuple(data.get("noise")),
        roman_numerals={
            str(k).lower(): str(v) for k, v in (data.get("roman_numerals") or {}).items()
        },
    )


@functools.lru_cache(maxsize=8)
def load(category: str, data_dir: Path | None = None) -> Catalogue | None:
    """The catalogue for a category, or None when there isn't one.

    None is the ordinary case, not an error: most categories (knitwear, running shoes)
    have no tier ladder worth tabulating, and the vocabulary continues to serve them.
    A malformed file is logged and treated as absent — a broken catalogue must degrade
    to "no opinion", never take a hunt down with it.
    """
    directory = data_dir or DATA_DIR
    path = directory / f"{category}.yaml"
    if not category or not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("catalogue %s unreadable (%s) — falling back to the vocabulary", path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("catalogue %s is not a mapping — ignoring", path)
        return None
    return build(data)


def classify(title: str, category: str, brand: str = "", rrp: float | None = None) -> BootTier:
    """Classify a title in a category, or return an all-unknown reading if there's no data."""
    catalogue = load(category)
    return catalogue.classify(title, brand, rrp) if catalogue else BootTier(tier=UNKNOWN)

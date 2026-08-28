"""Tests for the bRoom reference dataset in ``data/broom/``.

These tests enforce the one rule that keeps ``data/broom/`` and ``data/football_boots.yaml``
from drifting apart: **one boot fact, one home**. The classifier owns silo / generation /
year / launch RRP / status; the bRoom dataset joins to it on ``(brand, silo, generation)``
and adds only the site-facing fields. If a bRoom row names a boot the classifier has never
heard of, that is drift, and this file fails the build.

They also pin the sourcing discipline the whole project rests on: every factual field in the
dataset carries a source URL or the literal string ``UNVERIFIED``. A confident number with
no source is exactly what bRoom exists to be more trustworthy than.

Every test here fails without ``data/broom/`` present — the dataset is the thing under test.
"""


from __future__ import annotations

import re

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"
BROOM = DATA / "broom"


def _load(path: Path):
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def catalogue() -> dict:
    return _load(DATA / "football_boots.yaml")


@pytest.fixture(scope="module")
def boots() -> dict:
    return _load(BROOM / "boots.yaml")


# ------------------------------------------------------------------ the files exist & parse


def test_the_broom_dataset_files_exist():
    # The whole deliverable. If these are gone, everything downstream is vacuous.
    for name in ("boots.yaml", "glossary.yaml", "tiers.yaml", "heritage.yaml", "README.md"):
        assert (BROOM / name).exists(), f"missing data/broom/{name}"


@pytest.mark.parametrize("name", ["boots.yaml", "glossary.yaml", "tiers.yaml", "heritage.yaml"])
def test_each_dataset_file_is_valid_yaml_with_a_last_verified(name):
    doc = _load(BROOM / name)
    assert isinstance(doc, dict), f"{name} did not parse to a mapping"
    # Every file carries its own staleness clock, in the spirit of football_boots.yaml.
    assert doc.get("last_verified"), f"{name} is missing last_verified"


# ------------------------------------------------------------------ the join (no drift)


def _catalogue_generation_keys(catalogue: dict) -> set[tuple[str, str, str]]:
    """Every (brand, line, generation-token) the classifier can answer.

    A generation is addressable by its ``gen`` number, by any of its ``patterns``, or by the
    last word of a pattern — because a bRoom row keys on the natural name a shop uses
    ("maestro"), while the catalogue may store the fuller pattern ("tiempo maestro").
    """
    keys: set[tuple[str, str, str]] = set()
    for brand, bspec in catalogue["brands"].items():
        for line, lspec in (bspec.get("lines") or {}).items():
            for gen in (lspec.get("generations") or []):
                tokens: set[str] = set()
                if gen.get("gen") is not None:
                    tokens.add(str(gen["gen"]))
                for pat in (gen.get("patterns") or []):
                    pat = str(pat)
                    tokens.add(pat)
                    tokens.add(pat.split()[-1])  # last word, e.g. "maestro" from "tiempo maestro"
                for tok in tokens:
                    keys.add((brand, line, tok))
    return keys


def test_every_boot_row_joins_to_a_generation_in_the_classifier(boots, catalogue):
    """No bRoom row may name a (brand, silo, generation) the classifier does not know.

    This is the anti-drift guard. If adidas renames a silo or the catalogue moves a
    generation on, the key here stops resolving and the build fails loudly — which is the
    entire reason the two files are allowed to be separate.
    """
    known = _catalogue_generation_keys(catalogue)
    missing = []
    for row in boots["boots"]:
        key = (row["brand"], row["silo"], str(row["generation"]))
        if key not in known:
            missing.append(key)
    assert not missing, (
        "bRoom rows reference generations absent from data/football_boots.yaml "
        f"(drift): {sorted(set(missing))}"
    )


def test_the_boot_dataset_does_not_restate_rrp_year_or_status(boots):
    """RRP, model year and generation status are the classifier's to own, not this file's.

    Copying them here would create a second last_verified clock that drifts within a season.
    A ``rrp_note`` (prose corroboration) is allowed; a structured ``launch_rrp_eur`` /
    ``year`` / ``status`` field is not.
    """
    forbidden = {"launch_rrp_eur", "year", "status", "generation_status"}
    offenders = [
        (row["brand"], row["silo"], row["generation"], field)
        for row in boots["boots"]
        for field in forbidden
        if field in row
    ]
    assert not offenders, f"boots.yaml restates classifier-owned fields: {offenders}"


# ------------------------------------------------------------------ sourcing discipline


def _is_sourced(value) -> bool:
    """A source field is satisfied by a non-empty URL or the literal UNVERIFIED."""
    if value == "UNVERIFIED":
        return True
    return isinstance(value, str) and value.startswith("http")


def test_every_factual_boot_field_carries_a_source_or_is_unverified(boots):
    """The core trust rule: no factual claim without a source or an honest UNVERIFIED.

    For each row, the fact-bearing blocks (upper, plate, weight, soleplates, players,
    street price) must each carry a source that is a URL or UNVERIFIED. A gap is allowed;
    a confident unsourced number is not.
    """
    problems: list[str] = []
    for row in boots["boots"]:
        rid = f"{row['brand']}/{row['silo']}/{row['generation']}/{row.get('audience')}"

        # street price: if a price is given, it needs a source
        if row.get("street_price_eur") not in (None, "UNVERIFIED"):
            if not _is_sourced(row.get("street_price_source")):
                problems.append(f"{rid}: street_price_eur without a source")

        # nested blocks each own a `source`
        for block in ("upper", "plate"):
            b = row.get(block) or {}
            if not _is_sourced(b.get("source")):
                problems.append(f"{rid}: {block}.source missing/unsourced")

        # flat fact fields with a paired *_source
        for value_key, source_key in (
            ("weight_g", "weight_source"),
            ("soleplates", "soleplate_source"),
            ("signature_players", "signature_source"),
        ):
            if not _is_sourced(row.get(source_key)):
                problems.append(f"{rid}: {source_key} missing/unsourced")

    assert not problems, "unsourced factual fields:\n" + "\n".join(problems)


def test_street_price_is_never_labelled_an_rrp(boots):
    """PR #30's lesson, pinned: street_price_eur is a measured selling price, not an RRP.

    A row may carry a ``rrp_note`` (prose) but must not repurpose street_price as RRP by
    naming an ``rrp`` field on the row.
    """
    offenders = [
        (row["brand"], row["silo"], row["generation"])
        for row in boots["boots"]
        if "rrp" in row or "rrp_eur" in row
    ]
    assert not offenders, f"rows label a street price as RRP: {offenders}"


# ------------------------------------------------------------------ content files are usable


def test_glossary_covers_the_named_technologies():
    doc = _load(BROOM / "glossary.yaml")
    terms = {t["term"] for t in doc["terms"]}
    required = {
        "Flyknit", "Vaporposite", "Zoom Air", "Gripknit", "Cyclone 360", "ACC",
        "Primeknit", "Hybridtouch", "Demonskin", "Sprintframe", "Carbitex", "Fibertouch",
    }
    assert required <= terms, f"glossary missing: {sorted(required - terms)}"


def test_every_glossary_term_has_a_plain_explanation_and_a_source():
    doc = _load(BROOM / "glossary.yaml")
    for t in doc["terms"]:
        assert t.get("plain"), f"{t['term']} has no plain-language explanation"
        assert _is_sourced(t.get("source")), f"{t['term']} has no source"


def test_tier_explainer_states_all_three_bands_with_sources():
    doc = _load(BROOM / "tiers.yaml")
    tiers = {t["tier"] for t in doc["tiers"]}
    assert {"adult-flagship", "junior-flagship", "takedown"} <= tiers
    for t in doc["tiers"]:
        assert _is_sourced(t.get("source")), f"tier {t['tier']} has no source"


def test_every_drawable_tier_difference_declares_a_confidence_and_last_width_is_never_drawn():
    """The tier stickers are OWNED ARTWORK drawn from this data, so a physical claim here
    becomes a shape on the page. This guards the contract the design session relies on:

      * every tier carries a `draw` block,
      * every `draw.show` item declares `confidence` (measured|directional) so the
        illustrator never renders an unmeasured magnitude as a hard fact, and
      * last width is NEVER in `draw.show` for any tier — no per-silo Elite last-width
        figure was sourced, and a confidently-wrong width becoming a wide-boot silhouette
        is the exact failure the brief calls out.

    Without the `draw` blocks this test fails, which is why the precision upgrade ships with it.
    """
    doc = _load(BROOM / "tiers.yaml")
    allowed = {"measured", "directional"}
    for t in doc["tiers"]:
        draw = t.get("draw")
        assert isinstance(draw, dict), f"tier {t['tier']} has no draw block"
        show = draw.get("show", [])
        assert show, f"tier {t['tier']} draw.show is empty"
        for item in show:
            assert item.get("confidence") in allowed, (
                f"tier {t['tier']} draws '{item.get('feature')}' without a valid confidence"
            )
            # A 'measured' drawable is a hard claim on the page — it must name its evidence.
            if item.get("confidence") == "measured":
                assert "source" in str(item.get("note", "")).lower(), (
                    f"tier {t['tier']} draws '{item.get('feature')}' as MEASURED but its note "
                    "names no source — a measured drawable must cite evidence"
                )
            assert "last width" not in str(item.get("feature", "")).lower(), (
                f"tier {t['tier']} tries to DRAW last width — no measured figure exists; "
                "it must stay in do_not_draw"
            )
        # last width must be explicitly declared un-drawable
        do_not = " ".join(str(d.get("feature", "")) for d in draw.get("do_not_draw", [])).lower()
        assert "last width" in do_not, (
            f"tier {t['tier']} must list 'last width' in draw.do_not_draw"
        )


def test_heritage_notes_are_dated_and_sourced_per_silo():
    doc = _load(BROOM / "heritage.yaml")
    silos = {n["silo"] for n in doc["notes"]}
    # every current-silo family should have a heritage note
    for silo in ("predator", "copa", "mercurial superfly", "phantom", "tiempo", "f50"):
        assert silo in silos, f"no heritage note for {silo}"
    for note in doc["notes"]:
        assert note.get("dated"), f"heritage note '{note.get('title')}' is undated"
        assert note.get("sources"), f"heritage note '{note.get('title')}' has no sources"


def test_no_drawable_claims_an_absolute_it_cannot_support():
    """An absolute is destroyed by one counter-example, so `measured` must never be
    spent on words like "never", "only" or "always".

    This exists because it happened. `adidas junior closure` shipped as
    `LACED (never laceless)` with `confidence: measured`, instructing the illustrator
    to draw laces on the junior card as a *taught tier difference*. It is false:
    `data/football_boots.yaml` records a real listing measured on sportsdirect.lv on
    2026-08-28 —

        "Predator Elite Laceless Juniors Firm Ground Football Boots"   EUR 63.00

    — and the same file treats `laceless` as NOISE, a closure variant WITHIN a tier
    rather than a marker of one, noting that Predator+ (laceless) and Predator.1
    (laced) were the SAME tier. So closure cannot separate adult from junior at all.

    The sources behind the original claim were describing the COMMON configuration.
    That is a tendency, and a tendency phrased as an absolute is a wrong fact. A
    tendency may still be drawn, as `directional`; an absolute may not, unless a
    source genuinely establishes exclusivity.
    """
    absolutes = ("never", "only", "always", "exclusively")
    offenders = []
    for tier in _load(BROOM / "tiers.yaml")["tiers"]:
        for item in tier.get("draw", {}).get("show", []):
            if item.get("confidence") != "measured":
                continue
            value = str(item.get("value", "")).lower()
            # The note is checked as well as the value. The first version of this guard
            # read `value` only, and a second absolute walked straight past it: an item
            # whose value was a mild "LACED" carried "adidas League is never laceless"
            # in its note. The note is what a human reads before drawing, so it makes
            # exactly the same promise to the illustrator that the value does.
            text = value + " " + str(item.get("note", "")).lower()
            hits = sorted({w for w in absolutes if re.search(r"\b" + w + r"\b", text)})
            if hits:
                offenders.append(f"{tier['tier']}/{item.get('feature')}: uses {hits}")

    assert not offenders, (
        "A `measured` drawable states an absolute. One counter-example destroys an "
        "absolute, and this artwork is taught to readers as fact. Either source the "
        "exclusivity properly or restate it as `directional`:\n  " + "\n  ".join(offenders)
    )

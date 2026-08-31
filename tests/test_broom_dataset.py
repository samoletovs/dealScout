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


def test_closure_is_never_drawn_as_a_tier_property():
    """Laced vs laceless is a SKU variant within a tier, so it can never be a tier drawable.

    This is a second failure mode, distinct from the absolutes guard above and invisible to
    it. `LACELESS on Predator Elite` contains no "never"/"only"/"always", so it passed that
    check — while still asserting a within-tier SKU variant as a property of the tier, marked
    `measured`, aimed at an illustrator.

    It is false in both directions:

      * adidas sells `Predator Elite FG` (laced) AND `Predator Elite Laceless FG` at the
        same tier, so a card labelled only "Predator Elite FG" must not be drawn laceless;
      * a laceless JUNIOR Elite exists -- `Predator Elite Laceless Juniors FG`, EUR 63.00,
        sportsdirect.lv 2026-08-28 -- so closure cannot separate adult from junior either.

    `data/football_boots.yaml` already encodes the rule: `laceless` sits in `noise`, stripped
    before tier is read, and Predator+ (laceless) and Predator.1 (laced) are recorded as the
    same Elite tier.

    Closure may still be described per silo or per named SKU. It may never be a `draw.show`
    item, because that is where the tier explainer reads its differences from.
    """
    closure_words = ("laceless", "laced", "closure", "lace")
    offenders = []
    for tier in _load(BROOM / "tiers.yaml")["tiers"]:
        for item in tier.get("draw", {}).get("show", []):
            text = " ".join(str(item.get(k, "")) for k in ("feature", "value")).lower()
            if any(re.search(r"\b" + w + r"\b", text) for w in closure_words):
                offenders.append(f"{tier['tier']}/{item.get('feature')}: {item.get('value')!r}")

    assert not offenders, (
        "Closure appears as a tier drawable. Laced and laceless coexist at every tier this "
        "dataset covers, so drawing either as a tier difference teaches something the "
        "catalogue disproves. Move it to `drawable_by_silo`, or name the specific SKU:\n  "
        + "\n  ".join(offenders)
    )


def test_every_glossary_term_is_translated_into_every_language():
    """A term without lv/ru renders as English under a translated heading.

    The site marks untranslated prose honestly rather than hiding it, so a gap is
    visible rather than silent -- but visible-and-wrong is still wrong, and the marker
    exists to be temporary. Once a language is claimed as equal, a new term arriving in
    English only is a regression, and nothing else would catch it: the page still
    renders, it just quietly speaks the wrong language to a Latvian reader.
    """
    doc = _load(BROOM / "glossary.yaml")
    missing = [
        f"{term['term']}.{lang}.{field}"
        for term in doc["terms"]
        for lang in ("lv", "ru")
        for field in ("what", "plain")
        if not (term.get("i18n") or {}).get(lang, {}).get(field)
    ]

    assert not missing, (
        "glossary terms are missing translations. bRoom ships Latvian and Russian as "
        "equals, so an English-only term is a gap a reader sees:\n  " + "\n  ".join(missing)
    )


def test_a_translation_never_restates_a_figure_the_english_does_not_have():
    """A translation is another rendering of one fact, not a second copy of it.

    Numbers are where that breaks: a translator adding "около 280 евро" to a paragraph
    the English states without a price has invented a claim, in a field no English
    reader will ever check. So any digit run in a translation must appear in the
    English it renders.
    """
    import re

    doc = _load(BROOM / "glossary.yaml")
    offenders = []
    for term in doc["terms"]:
        for lang in ("lv", "ru"):
            for field in ("what", "plain"):
                src = str(term.get(field, ""))
                dst = str((term.get("i18n") or {}).get(lang, {}).get(field, ""))
                src_nums = set(re.findall(r"\d+", src))
                for num in set(re.findall(r"\d+", dst)):
                    if num not in src_nums:
                        offenders.append(f"{term['term']}.{lang}.{field}: '{num}' not in the English")

    assert not offenders, (
        "a translation contains a number its English source does not:\n  " + "\n  ".join(offenders)
    )


def test_every_heritage_note_is_translated_into_every_language():
    """A heritage note without lv/ru renders as English under a translated heading.

    The glossary already ships three equal languages; the heritage narratives are the
    last English-only prose on the site, shown under a ``vēl nav tulkots`` /
    ``не переведено`` marker. Once a language is claimed as equal, a note arriving (or
    left) in English only is a regression the rendered page cannot catch — it still
    renders, it just speaks the wrong language to a Latvian or Russian reader. This is
    the sibling of ``test_every_glossary_term_is_translated_into_every_language``.
    """
    doc = _load(BROOM / "heritage.yaml")
    missing = [
        f"{note.get('silo')}.{lang}.{field}"
        for note in doc["notes"]
        for lang in ("lv", "ru")
        for field in ("title", "body")
        if not (note.get("i18n") or {}).get(lang, {}).get(field)
    ]

    assert not missing, (
        "heritage notes are missing translations. bRoom ships Latvian and Russian as "
        "equals, so an English-only note is a gap a reader sees:\n  " + "\n  ".join(missing)
    )


def test_a_heritage_translation_never_restates_a_figure_the_english_does_not_have():
    """A heritage translation is another rendering of one fact, not a second copy.

    These narratives are dense with dates (1994, 1979, 1998, 2026) and one weight
    (~150 g). A translator adding a price the English never states — "no €61" — would
    invent a claim in a field no English reader audits, in exactly the place the tier
    explainer's honesty rules are hardest to police. So any digit run in a translated
    ``title``/``body`` must already appear in the English it renders. Sibling of
    ``test_a_translation_never_restates_a_figure_the_english_does_not_have``.
    """
    doc = _load(BROOM / "heritage.yaml")
    offenders = []
    for note in doc["notes"]:
        for lang in ("lv", "ru"):
            for field in ("title", "body"):
                src = str(note.get(field, ""))
                dst = str((note.get("i18n") or {}).get(lang, {}).get(field, ""))
                src_nums = set(re.findall(r"\d+", src))
                for num in set(re.findall(r"\d+", dst)):
                    if num not in src_nums:
                        offenders.append(
                            f"{note.get('silo')}.{lang}.{field}: '{num}' not in the English"
                        )

    assert not offenders, (
        "a heritage translation contains a number its English source does not:\n  "
        + "\n  ".join(offenders)
    )


def test_every_boot_upper_is_translated_into_every_language(boots):
    """A boot's ``upper.plain`` without lv/ru renders as English under a translated heading.

    The glossary and heritage narratives already ship three equal languages; the one-line
    "what the upper actually is" description is the last English-only prose on a boot page,
    shown under a ``vēl nav tulkots`` / ``не переведено`` marker. Once a language is claimed
    as equal, a boot arriving (or left) in English only is a regression the rendered page
    cannot catch — it still renders, it just speaks the wrong language to a Latvian or
    Russian reader.

    Note the shape: ``upper.plain`` is *nested*, and the site's ``localised(row, field, lang)``
    reads ``row[field]`` and ``row.i18n[lang][field]`` flat, so the ``i18n`` block lives
    inside ``upper`` (``upper.i18n.lv.plain``), not at the boot's top level. Sibling of
    ``test_every_heritage_note_is_translated_into_every_language``.
    """
    missing = [
        f"{row['brand']}/{row['silo']}/{row['generation']}.{lang}.plain"
        for row in boots["boots"]
        for lang in ("lv", "ru")
        if not ((row.get("upper") or {}).get("i18n") or {}).get(lang, {}).get("plain")
    ]

    assert not missing, (
        "boot upper descriptions are missing translations. bRoom ships Latvian and Russian "
        "as equals, so an English-only upper is a gap a reader sees:\n  " + "\n  ".join(missing)
    )


def test_a_boot_upper_translation_never_restates_a_figure_the_english_does_not_have(boots):
    """A boot upper translation is another rendering of one fact, not a second copy.

    These lines carry weights and percentages (``~29%``, ``30–60 g``, ``40–60 g``) and
    technology names with numbers (``Cyclone 360``, ``Copa Pure 4``). A translator adding a
    price the English never states — ``no €61`` — would invent a claim in a field no English
    reader audits, exactly where the tier explainer's honesty rules are hardest to police. So
    any digit run in a translated ``upper.plain`` must already appear in the English it
    renders. Sibling of ``test_a_heritage_translation_never_restates_a_figure_the_english_does_not_have``.
    """
    offenders = []
    for row in boots["boots"]:
        upper = row.get("upper") or {}
        src = str(upper.get("plain", ""))
        src_nums = set(re.findall(r"\d+", src))
        for lang in ("lv", "ru"):
            dst = str((upper.get("i18n") or {}).get(lang, {}).get("plain", ""))
            for num in set(re.findall(r"\d+", dst)):
                if num not in src_nums:
                    offenders.append(
                        f"{row['brand']}/{row['silo']}/{row['generation']}.{lang}.plain: "
                        f"'{num}' not in the English"
                    )

    assert not offenders, (
        "a boot upper translation contains a number its English source does not:\n  "
        + "\n  ".join(offenders)
    )


def test_a_translation_never_claims_a_material_the_english_does_not():
    """The same rule as the digit guard, for the word that carries the most meaning here.

    Russian «кожа» and Latvian "āda" both mean leather in a footwear context. The English
    uppers say "skin" — a thin synthetic coating over the knit — and four Mercurial rows
    came back translated as leather. A Mercurial is defined by *not* being leather; that
    is the Tiempo's identity, and telling the two apart is the reason this site exists.

    The digit guard cannot see this, because no figure changed. Nothing looks wrong in
    either language on its own: the Russian reads as fluent, accurate prose about a boot
    that does not exist. Only the pair reveals it, which is why it needs a test and not a
    proofread.
    """
    import re

    leather_en = re.compile(r"leather", re.I)
    leather_tr = {"ru": re.compile(r"кож", re.I), "lv": re.compile(r"\bād", re.I)}

    doc = _load(BROOM / "boots.yaml")
    offenders = []
    for boot in doc["boots"]:
        upper = boot.get("upper") or {}
        english = str(upper.get("plain", ""))
        if leather_en.search(english):
            continue  # the English says leather, so a translation may too
        for lang, pattern in leather_tr.items():
            rendered = str((upper.get("i18n") or {}).get(lang, {}).get("plain", ""))
            if pattern.search(rendered):
                key = f"{boot.get('silo')} {boot.get('generation')} {boot.get('tier')}"
                offenders.append(f"{key}.{lang}: says leather where the English says skin")

    assert not offenders, (
        "a translation names a material its English source does not:\n  " + "\n  ".join(offenders)
    )


def test_collar_height_is_never_drawn_as_a_tier_property():
    """High vs low collar is a SKU variant, like closure — the *material* is the tier thing.

    The same shape of error as the closure one, and it hid in the sentence that was supposed
    to prevent it. `drawable_by_silo` asserted "Collar is a SILO trait, not a tier trait",
    which is true of the Mercurial — the silo name carries it, Superfly collared and Vapor
    low — and false in general. Nike sells `Phantom 6 High Elite FG` and `Phantom 6 Low
    Elite FG`: one silo, one generation, one tier, two collars, observed together on
    teamsport.lv. `cut` is part of the price-log identity for exactly this reason.

    So a card labelled only "Phantom 6 Elite" cannot be drawn collared or low, and the
    height must not appear as a tier difference.

    The distinction this guard has to keep is between HEIGHT and MATERIAL. "lower-profile
    mesh, not premium knit" is a real, sourced tier difference and must survive — the
    takedown's collar is made of cheaper stuff whatever its height. Only words that assert a
    height are banned, so the guard cannot be satisfied by deleting the true claim.
    """
    height_words = ("high-cut", "low-cut", "high cut", "low cut", "collar height", "ankle height")
    offenders = []
    for tier in _load(BROOM / "tiers.yaml")["tiers"]:
        for item in tier.get("draw", {}).get("show", []):
            text = " ".join(str(item.get(k, "")) for k in ("feature", "value", "note")).lower()
            for word in height_words:
                if word in text:
                    offenders.append(f"{tier['tier']}/{item.get('feature')}: {word!r}")

    assert not offenders, (
        "collar HEIGHT is drawn as a tier difference, but Nike sells Phantom 6 High and Low "
        "at one tier:\n  " + "\n  ".join(offenders)
    )


def test_the_collar_note_should_not_call_the_collar_a_silo_trait():
    """The claim that was wrong, pinned so it cannot be restored by a well-meaning edit.

    "Collar is a SILO trait" reads as a rule and is a generalisation from one line. Phantom 6
    High and Low disprove it. The note may still say collar is not a TIER trait — that part
    is true and load-bearing — but it must not promote the Mercurial's naming convention into
    a law that the Phantom breaks.
    """
    notes = []
    for tier in _load(BROOM / "tiers.yaml")["tiers"]:
        for item in tier.get("draw", {}).get("drawable_by_silo", []):
            if "collar" in str(item.get("feature", "")).lower():
                notes.append(str(item.get("note", "")))

    assert notes, "expected a collar entry in drawable_by_silo"
    for note in notes:
        low = " ".join(note.lower().split())
        assert "collar is a silo trait" not in low, (
            "the collar note calls collar a silo trait; Phantom 6 High and Low are one silo, "
            "one generation and one tier with two collars"
        )
        assert "phantom" in low, (
            "the collar note must name the counter-example it is qualified by, or the "
            "qualification is a claim without evidence"
        )


# --------------------------------------------------- cross-generation source guard


def _url_generation_matches_row(url: str, silo: str, generation: str) -> str | None:
    """Return an error string if *url* names *silo* with a **different** generation.

    Returns ``None`` (no problem) when:
    - the URL does not mention the silo at all (generic source)
    - the silo is mentioned but no generation appears after it

    A generation is detected as:
    - a bare integer following the silo slug  (``vapor-17``, ``predator-26``)
    - a known named-generation suffix         (``phantom-gx-2``, ``phantom-gt``)
    """
    silo_slug = silo.replace(" ", "-")
    gen_slug = generation.replace(" ", "-")
    low = url.lower()

    # Does the URL mention this silo at all?
    idx = low.find(silo_slug)
    if idx < 0:
        return None  # generic source, no flag

    after = low[idx + len(silo_slug):]

    # Named-generation suffixes: only test those that belong to THIS silo.
    # Maps suffix → canonical generation slug (with silo prefix).
    # E.g. "phantom" silo: suffix "-gt-2" or "-gt2" both → "phantom-gt-2".
    _NAMED_SUFFIXES: dict[str, list[tuple[str, str]]] = {
        "phantom": [
            ("-gx-2", "phantom-gx-2"), ("-gx2", "phantom-gx-2"),
            ("-gx", "phantom-gx"),
            ("-gt-2", "phantom-gt-2"), ("-gt2", "phantom-gt-2"),
            ("-gt", "phantom-gt"),
        ],  # longest first per family
    }
    suffixes = _NAMED_SUFFIXES.get(silo_slug, [])
    for suffix, canonical in suffixes:
        if after.startswith(suffix):
            # Ensure the suffix is a complete token — not a prefix of a year like
            # "-gx-2023" matching the "-gx-2" suffix meant for "phantom gx 2".
            rest = after[len(suffix):]
            if rest and rest[0].isdigit():
                continue  # e.g. "-gx-2023" is a year, not "-gx-2"
            if gen_slug == canonical:
                return None  # match
            canon_name = canonical.replace("-", " ")
            return f"URL names '{canon_name}' but row generation is '{generation}'"

    # Try numeric: expect a hyphen then digits
    m = re.match(r"-(\d+)", after)
    if not m:
        return None  # no generation in URL → no flag

    url_gen_num = m.group(1)

    # The row's generation might be numeric ("17") or named with a number.
    # Also accept a four-digit year matching the two-digit gen (e.g. "2024" for "24").
    if generation == url_gen_num:
        return None  # match
    if len(url_gen_num) == 4 and generation == url_gen_num[2:]:
        return None  # "2024" in URL matches gen "24"

    return f"URL names generation {url_gen_num} but row generation is '{generation}'"


def test_a_source_url_must_not_cite_a_different_generation(boots):
    """Where a source URL names this silo AND a generation, the generation must match.

    Citing a Phantom 6 review for a Phantom GX claim reproduces, in the citations,
    exactly the confusion the site exists to remove. A reader who checks the source
    finds the wrong boot.

    A source with no generation in the URL (a tier explainer) does not flag.
    """
    problems: list[str] = []
    for row in boots["boots"]:
        rid = f"{row['brand']}/{row['silo']}/{row['generation']}/{row.get('audience')}"
        silo = row["silo"]
        gen = str(row["generation"])

        # Collect all source URLs on this row
        sources: list[tuple[str, str]] = []
        for block in ("upper", "plate"):
            b = row.get(block) or {}
            src = b.get("source", "")
            if isinstance(src, str) and src.startswith("http"):
                sources.append((f"{block}.source", src))
        for field in ("weight_source", "soleplate_source", "signature_source",
                       "street_price_source"):
            src = row.get(field, "")
            if isinstance(src, str) and src.startswith("http"):
                sources.append((field, src))

        for field_name, url in sources:
            err = _url_generation_matches_row(url, silo, gen)
            if err:
                problems.append(f"{rid}: {field_name} — {err} ({url})")

    assert not problems, (
        "source URLs cite a different generation than the row they belong to:\n  "
        + "\n  ".join(problems)
    )

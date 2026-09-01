"""Guards for the bRoom **lineage** dataset in ``data/broom/lineage/``.

A lineage record is a *generation in a silo's history* — the 1994 Predator, the 2002
Mania, the 1998 R9 Mercurial. It is deliberately NOT a row in ``boots.yaml``.

That separation is the whole design. ``boots.yaml`` describes a boot you can buy: it
carries soleplates, a retailer handle, a street price, and it feeds the price identity
that the Scout's "cheapest seen in 45 days" claim rests on. A boot discontinued in 1996
has none of those things. Forcing it into ``boots.yaml`` would hand it a soleplate list
and a price identity it never had, and would pollute the very keys the Scout depends on.

One file per silo (``lineage/predator.yaml``) so that researchers working on different
silos never touch the same file.

The sourcing discipline is inherited from ``test_broom_dataset.py`` and tightened in one
place, because a thirty-year lineage is where the mistake it guards against is easiest
to make: **a source about a neighbouring generation is not a source.** Eight rows in the
main dataset once cited a review of a different boot in the same silo; across twenty-plus
Predators that error is nearly invisible to a reader and completely invisible to a type
check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"
LINEAGE = DATA / "broom" / "lineage"

UNVERIFIED = "UNVERIFIED"

# Fields every entry must carry. `innovation`, `players` and `photo` may be the literal
# UNVERIFIED — an honest gap in a timeline is fine and is itself informative. `sources`
# may not: an entry with no source is an assertion, and this project does not publish
# assertions.
REQUIRED = ("brand", "silo", "sequence", "name", "year", "innovation", "sources")

# Football boots as a product category start at the Copa Mundial. Anything outside this
# is a typo, and a typo in a year silently reorders a timeline.
YEAR_MIN, YEAR_MAX = 1979, 2027


def _files() -> list[Path]:
    return sorted(LINEAGE.glob("*.yaml")) if LINEAGE.is_dir() else []


def _entries() -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    for path in _files():
        with path.open(encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        for entry in doc.get("generations") or []:
            out.append((path, entry))
    return out


@pytest.fixture(scope="module")
def catalogue() -> dict:
    with (DATA / "football_boots.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_the_lineage_directory_exists_and_is_not_empty() -> None:
    # Without this the whole file would vacuously pass, which is the failure mode where
    # a guard reports success because it cannot see anything.
    assert _files(), f"no lineage files found in {LINEAGE}"


def test_every_entry_carries_the_required_fields() -> None:
    missing: list[str] = []
    for path, e in _entries():
        for field in REQUIRED:
            if e.get(field) in (None, "", []):
                missing.append(f"{path.name}: {e.get('name', '?')} has no {field}")
    assert not missing, "entries missing required fields:\n" + "\n".join(missing)


def test_every_entry_has_at_least_one_source() -> None:
    bad = [
        f"{p.name}: {e.get('name', '?')}"
        for p, e in _entries()
        if not [s for s in (e.get("sources") or []) if str(s).startswith("http")]
    ]
    assert not bad, "entries with no usable source URL:\n" + "\n".join(bad)


def test_years_are_plausible() -> None:
    bad = [
        f"{p.name}: {e.get('name', '?')} year={e.get('year')}"
        for p, e in _entries()
        if not (isinstance(e.get("year"), int) and YEAR_MIN <= e["year"] <= YEAR_MAX)
    ]
    assert not bad, f"years outside {YEAR_MIN}-{YEAR_MAX}:\n" + "\n".join(bad)


def test_sequence_orders_the_lineage_by_year() -> None:
    """`sequence` is the display order; it must agree with the years it displays.

    Gaps are allowed and expected — a silo half-researched is a normal intermediate
    state. What is not allowed is a sequence that contradicts the years, because then
    the timeline renders out of order while every individual fact remains correct.
    """
    problems: list[str] = []
    for path in _files():
        with path.open(encoding="utf-8") as fh:
            entries = (yaml.safe_load(fh) or {}).get("generations") or []
        seqs = [e.get("sequence") for e in entries]
        if len(seqs) != len(set(seqs)):
            problems.append(f"{path.name}: duplicate sequence numbers {seqs}")
        ordered = sorted(entries, key=lambda e: e.get("sequence") or 0)
        years = [e.get("year") for e in ordered if isinstance(e.get("year"), int)]
        if years != sorted(years):
            problems.append(f"{path.name}: sequence disagrees with years {years}")
    assert not problems, "\n".join(problems)


def test_lineage_silos_are_known_to_the_classifier(catalogue: dict) -> None:
    """One boot fact, one home — a lineage cannot invent a silo the engine never heard of."""
    known = {
        (brand, silo)
        for brand, b in catalogue["brands"].items()
        for silo in (b.get("lines") or {})
    }
    unknown = {
        (e.get("brand"), e.get("silo"))
        for _, e in _entries()
        if (e.get("brand"), e.get("silo")) not in known
    }
    assert not unknown, f"lineage names silos the classifier does not know: {sorted(unknown)}"


def test_a_photo_reference_resolves_to_a_licensed_photo() -> None:
    """A lineage entry names a photo by id; it does not restate the licence.

    Attribution is a licence condition for CC BY / BY-SA, so the licence, author and
    source have exactly one home — ``photos.yaml`` — and copying them here would create
    a second copy that can drift out of agreement with the first. Drift in an
    attribution is a licence breach, not a formatting problem.

    So the only thing checked here is that the reference resolves. If it does, the
    attribution rendered on the page is by construction the verified one.
    """
    photos_path = DATA / "broom" / "photos.yaml"
    with photos_path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    known = {p.get("id") for p in (doc.get("photos") or doc or [])}

    dangling = [
        f"{path.name}: {e.get('name')} -> photo '{e['photo']}'"
        for path, e in _entries()
        if e.get("photo") and e["photo"] != UNVERIFIED and e["photo"] not in known
    ]
    assert not dangling, (
        "lineage entries reference photos that do not exist in photos.yaml:\n"
        + "\n".join(dangling)
    )


def test_a_source_url_must_not_cite_a_different_generation() -> None:
    """The mistake this lineage is most likely to make, ported from the dataset guards.

    A URL naming a *different* generation of the same silo is citing a different boot.
    Only fires when the URL names some generation of this silo AND that generation is
    not this one — a URL that names no generation at all is left alone, because a brand
    history page legitimately covers the whole lineage.
    """
    problems: list[str] = []
    for path, e in _entries():
        silo_words = [w for w in str(e.get("silo", "")).split() if len(w) > 3]
        if not silo_words:
            continue
        mine = str(e.get("generation_token") or e.get("year") or "").lower()
        for src in e.get("sources") or []:
            low = str(src).lower()
            if not any(w in low for w in silo_words):
                continue
            # Years are the least ambiguous generation token in a URL.
            cited = re.findall(r"(?:19|20)\d{2}", low)
            if cited and mine and mine not in cited:
                problems.append(
                    f"{path.name}: {e.get('name')} (year {e.get('year')}) cites {src}"
                )
    assert not problems, (
        "sources appear to cite a different generation:\n" + "\n".join(problems)
    )


# Reader-facing prose fields. `photo`/`sources` are not here: `photo` is an id or the bare
# sentinel and never reaches the reader as prose, and `sources` are URLs.
PROSE_FIELDS = ("name", "innovation", "why", "players")


def test_reader_facing_prose_never_embeds_the_raw_sentinel() -> None:
    """UNVERIFIED is a whole-field token, never a word in a sentence.

    A field may be *exactly* ``UNVERIFIED`` — that renders as an honest gap. What it may
    never do is carry the token inside a longer string ("match-day wearers are
    UNVERIFIED"), because that reaches the reader as the raw word, which is the one thing
    the sentinel exists to keep off the page. bRoom's own "never leaks the sentinel"
    render guard fired on exactly this after a merge; caught upstream here it never ships.
    """
    leaks = [
        f"{path.name}: {e.get('name', '?')} -> {field}: {e[field]!r}"
        for path, e in _entries()
        for field in PROSE_FIELDS
        if isinstance(e.get(field), str) and e[field] != UNVERIFIED and UNVERIFIED in e[field]
    ]
    assert not leaks, (
        "prose fields embed the raw UNVERIFIED sentinel — use the field value exactly "
        "'UNVERIFIED' for an honest gap, or reword the sentence:\n" + "\n".join(leaks)
    )

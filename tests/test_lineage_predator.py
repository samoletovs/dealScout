"""Predator-specific guards for ``data/broom/lineage/predator.yaml``.

The generic contract lives in ``tests/test_broom_lineage.py`` and applies to every
silo. This file pins the two things the *Predator* research job was asked to deliver,
so that a regression (a re-seeded UNVERIFIED, a lineage silently truncated back to the
nine seeded rows) fails loudly rather than quietly rendering "Not documented yet."

Each assertion here was checked to FAIL against the seeded file: before the research
was written, eight of the nine seeded entries carried ``innovation: UNVERIFIED`` and
the lineage stopped at 2018.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PREDATOR = REPO_ROOT / "data" / "broom" / "lineage" / "predator.yaml"

UNVERIFIED = "UNVERIFIED"

# The eight seeded generations that shipped with ``innovation: UNVERIFIED``. Each is
# identified by a substring of its name plus its year, because 2012 carries two
# distinct generations (adiPower and Lethal Zones) and a year alone is ambiguous. The
# adiPower is dated 2011 here: it launched in May 2011 and the seed's 2012 was corrected
# from the cited Wikipedia list ("AdiPower Predator (2011)").
FORMERLY_UNVERIFIED = [
    ("touch", 1996),
    ("accelerator", 1998),
    ("mania", 2002),
    ("pulse", 2004),
    ("absolute", 2006),
    ("adipower", 2011),
    ("lz", 2012),
    ("18", 2018),
]


def _generations() -> list[dict]:
    with PREDATOR.open(encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("generations") or []


def _find(name_part: str, year: int) -> dict | None:
    for e in _generations():
        if e.get("year") == year and name_part in str(e.get("name", "")).lower():
            return e
    return None


def test_the_eight_seeded_innovations_are_now_documented() -> None:
    """The priority job: the eight seeded gaps must carry real, substantial prose.

    A short or missing ``innovation`` is what the owner complained about ("not enough
    details... is it interesting to read?"). ``UNVERIFIED`` here is a regression.
    """
    problems: list[str] = []
    for name_part, year in FORMERLY_UNVERIFIED:
        e = _find(name_part, year)
        if e is None:
            problems.append(f"missing generation: {name_part!r} ({year})")
            continue
        innovation = str(e.get("innovation") or "").strip()
        if innovation == UNVERIFIED or len(innovation) < 60:
            problems.append(
                f"{e.get('name')} ({year}): innovation not documented "
                f"({innovation[:30]!r}...)"
            )
    assert not problems, "seeded Predator innovations still undocumented:\n" + "\n".join(
        problems
    )


def test_the_lineage_grew_beyond_the_nine_seeded_generations() -> None:
    """Part two: the modern era and the pre-2002 gaps were added.

    The seeded file had exactly nine generations (1994–2018). Completing the spine
    means materially more than that.
    """
    count = len(_generations())
    assert count > 9, f"lineage still only has {count} generations; expected the spine to grow"


def test_the_modern_numbered_era_reaches_predator_24_or_later() -> None:
    """The owner asked for the spine to reach the modern numbered era (24/25/26)."""
    years = [e.get("year") for e in _generations() if isinstance(e.get("year"), int)]
    assert max(years) >= 2024, f"newest generation is {max(years)}; expected 2024+"


def test_sequence_still_agrees_with_year_order_after_renumbering() -> None:
    """Renumbering 23 generations by hand is exactly where an ordering bug creeps in.

    This duplicates the generic guard on purpose, scoped to this file, so a Predator
    re-sequence that contradicts the years fails in the Predator suite too.
    """
    gens = _generations()
    seqs = [e.get("sequence") for e in gens]
    assert len(seqs) == len(set(seqs)), f"duplicate sequence numbers: {seqs}"
    ordered = sorted(gens, key=lambda e: e.get("sequence") or 0)
    years = [e.get("year") for e in ordered if isinstance(e.get("year"), int)]
    assert years == sorted(years), f"sequence disagrees with year order: {years}"


def test_predator_rapier_1995_was_added() -> None:
    """The 1995 Rapier — the first adidas boot sold in a colour other than black — sits
    between the 1994 original and the 1996 Touch and was missing from the spine.

    Fails against any file that jumps straight from 1994 to 1996 (the merged seed did).
    """
    e = _find("rapier", 1995)
    assert e is not None, "Predator Rapier (1995) generation is missing"
    innovation = str(e.get("innovation") or "").strip()
    assert innovation and innovation != UNVERIFIED and len(innovation) >= 60, (
        f"Rapier innovation not documented ({innovation[:30]!r}...)"
    )


def test_adipower_year_resolved_to_2011() -> None:
    """adiPower launched in May 2011; the seed mislabelled it 2012.

    Fails against the pre-fix file, which carried ``year: 2012`` for the adiPower.
    """
    assert _find("adipower", 2011) is not None, "adiPower is not dated 2011"
    assert _find("adipower", 2012) is None, "a stale adiPower dated 2012 still exists"


def test_lineage_reached_twenty_four_generations() -> None:
    """Adding the Rapier takes the spine to 24 generations; the prior merged file had 23."""
    count = len(_generations())
    assert count >= 24, (
        f"lineage has {count} generations; expected 24 after adding the Rapier"
    )

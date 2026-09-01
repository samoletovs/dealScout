"""Phantom-specific guards for ``data/broom/lineage/phantom.yaml``.

The generic ``test_broom_lineage.py`` guards every lineage file at once, so on its own it
passes vacuously when the Phantom file is simply absent — it never asserts that *this*
silo was researched. These tests pin the Phantom lineage in particular, and every one of
them FAILS if ``phantom.yaml`` is missing or malformed:

- the file exists and loads as a Phantom lineage;
- it has at least five generations (a lineage, not a stub);
- every entry is tagged ``silo: phantom`` — the Magista/Hypervenom predecessors the
  Phantom replaced are deliberately NOT smuggled in under other silo tags;
- the lowest ``sequence`` is the oldest boot, so the timeline renders oldest-first.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PHANTOM = REPO_ROOT / "data" / "broom" / "lineage" / "phantom.yaml"


@pytest.fixture(scope="module")
def doc() -> dict:
    assert PHANTOM.is_file(), f"phantom lineage file is missing: {PHANTOM}"
    with PHANTOM.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@pytest.fixture(scope="module")
def generations(doc: dict) -> list[dict]:
    gens = doc.get("generations") or []
    assert gens, "phantom.yaml has no generations"
    return gens


def test_phantom_file_declares_the_phantom_silo(doc: dict) -> None:
    assert doc.get("silo") == "phantom", "phantom.yaml must declare silo: phantom"
    assert doc.get("brand") == "nike", "phantom.yaml must declare brand: nike"


def test_phantom_has_at_least_five_generations(generations: list[dict]) -> None:
    assert len(generations) >= 5, (
        f"a Phantom lineage should carry the line's generations, not a stub; "
        f"found only {len(generations)}"
    )


def test_every_phantom_entry_is_the_phantom_silo(generations: list[dict]) -> None:
    # The Magista and Hypervenom lines the Phantom replaced are separate silos and must not
    # be smuggled in here under another tag; every row is the phantom silo the classifier knows.
    wrong = [g.get("name", "?") for g in generations if g.get("silo") != "phantom"]
    assert not wrong, f"entries not tagged silo: phantom: {wrong}"


def test_every_phantom_entry_is_nike(generations: list[dict]) -> None:
    wrong = [g.get("name", "?") for g in generations if g.get("brand") != "nike"]
    assert not wrong, f"entries not tagged brand: nike: {wrong}"


def test_sequence_one_is_the_oldest_phantom(generations: list[dict]) -> None:
    """Sequence 1 must be the oldest boot, so the timeline reads oldest-first."""
    ordered = sorted(generations, key=lambda g: g.get("sequence") or 0)
    first = ordered[0]
    assert first.get("sequence") == 1, "the lineage must start at sequence 1"
    oldest_year = min(
        g["year"] for g in generations if isinstance(g.get("year"), int)
    )
    assert first.get("year") == oldest_year, (
        f"sequence 1 ({first.get('name')}, {first.get('year')}) is not the oldest "
        f"generation ({oldest_year})"
    )

"""Guards specific to the **adidas F50** lineage (``data/broom/lineage/f50.yaml``).

The shared contract lives in ``tests/test_broom_lineage.py`` and covers every silo. This
file adds the checks that are specific to the F50 — the ones that would let a reader trust
that *this* silo, in particular, was researched rather than stubbed.

The F50 is the awkward silo: it is one continuous speed line that changed its name to "X"
for nine years and then changed back, so the single most important thing to assert is that
the file tells that whole arc under one ``silo: f50`` tag, oldest generation first. Without
this file none of these assertions can run — which is exactly the point: the test fails
loudly until the F50 lineage exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
F50 = REPO_ROOT / "data" / "broom" / "lineage" / "f50.yaml"


@pytest.fixture(scope="module")
def doc() -> dict:
    assert F50.is_file(), f"the F50 lineage file does not exist: {F50}"
    with F50.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@pytest.fixture(scope="module")
def generations(doc: dict) -> list[dict]:
    gens = doc.get("generations") or []
    assert gens, "f50.yaml declares no generations"
    return gens


def test_the_file_is_tagged_as_the_f50_silo(doc: dict) -> None:
    assert doc.get("silo") == "f50", f"top-level silo must be 'f50', got {doc.get('silo')!r}"
    assert doc.get("brand") == "adidas"


def test_the_lineage_covers_at_least_six_generations(generations: list[dict]) -> None:
    # The F50 arc (origin -> TUNIT -> adizero -> miCoach -> X era -> 2024 revival) is far
    # longer than six; six is a floor that a half-built stub cannot clear.
    assert len(generations) >= 6, f"only {len(generations)} F50 generations, expected >= 6"


def test_every_entry_is_the_f50_silo(generations: list[dict]) -> None:
    """The whole point of the file: X-era boots included, every entry is tagged f50.

    If any entry drifted to ``silo: x`` the classifier check in the shared suite would not
    catch it here, and the reader would lose the thread that X and F50 are one line.
    """
    stray = [g.get("name", "?") for g in generations if g.get("silo") != "f50"]
    assert not stray, f"entries not tagged silo: f50: {stray}"


def test_sequence_one_is_the_oldest_generation(generations: list[dict]) -> None:
    """Sequence 1 must be the earliest boot — the timeline reads oldest-first."""
    by_seq = sorted(generations, key=lambda g: g.get("sequence") or 0)
    years = [g.get("year") for g in generations if isinstance(g.get("year"), int)]
    first = by_seq[0]
    assert first.get("sequence") == 1, "the lowest sequence number must be 1"
    assert first.get("year") == min(years), (
        f"sequence 1 is {first.get('name')!r} ({first.get('year')}), "
        f"but the oldest year in the file is {min(years)}"
    )

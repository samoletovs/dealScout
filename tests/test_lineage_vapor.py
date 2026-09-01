"""Guard for the **Mercurial Vapor** lineage (``data/broom/lineage/mercurial-vapor.yaml``).

``tests/test_broom_lineage.py`` enforces the shared shape across every silo. This file
adds the checks that are specific to the Vapor record and, deliberately, FAILS if the
file is missing — so it also proves the file was actually delivered rather than the
generic guards passing vacuously over the other silos.

What it pins:
- the file exists and parses as a lineage document;
- it is a substantive record, not a stub — at least six generations;
- every entry belongs to the Vapor silo (``mercurial vapor``), the low-cut line the
  classifier knows, so a Superfly row can never leak into this file;
- ``sequence`` 1 is the oldest generation — the 1998 original the whole line grew from —
  because the timeline is meant to read from the beginning.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
VAPOR = REPO_ROOT / "data" / "broom" / "lineage" / "mercurial-vapor.yaml"

SILO = "mercurial vapor"


def _doc() -> dict:
    assert VAPOR.is_file(), f"missing Mercurial Vapor lineage file: {VAPOR}"
    with VAPOR.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _generations() -> list[dict]:
    return _doc().get("generations") or []


def test_vapor_lineage_loads() -> None:
    doc = _doc()
    assert doc.get("silo") == SILO, f"file-level silo should be '{SILO}', got {doc.get('silo')!r}"
    assert _generations(), "Mercurial Vapor lineage has no generations"


def test_vapor_lineage_is_substantive() -> None:
    gens = _generations()
    assert len(gens) >= 6, f"expected at least 6 generations, found {len(gens)}"


def test_every_generation_is_in_the_vapor_silo() -> None:
    wrong = [
        f"{g.get('name', '?')} -> silo={g.get('silo')!r}"
        for g in _generations()
        if g.get("silo") != SILO
    ]
    assert not wrong, "entries not tagged to the Vapor silo:\n" + "\n".join(wrong)


def test_sequence_one_is_the_oldest_generation() -> None:
    gens = _generations()
    years = [g["year"] for g in gens if isinstance(g.get("year"), int)]
    assert years, "no integer years present"
    first = min(gens, key=lambda g: g.get("sequence") or 0)
    assert first.get("sequence") == 1, "no generation carries sequence 1"
    assert first.get("year") == min(years), (
        f"sequence 1 ({first.get('name')}, year {first.get('year')}) is not the oldest "
        f"generation (oldest year is {min(years)})"
    )

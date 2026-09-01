"""Guards specific to the Nike Mercurial Superfly lineage file.

The shared guards in ``test_broom_lineage.py`` cover every lineage file generically.
This module pins the things that are true *only* of the Superfly silo, so the file
cannot quietly lose them: it is the high-cut Mercurial branch, it starts at the 2009
Superfly I, every entry carries the classifier's ``mercurial superfly`` tag, and the
one generation that has a licence-verified photograph actually resolves it.

Each assertion here fails if ``data/broom/lineage/mercurial-superfly.yaml`` is missing
or regresses — which is what makes this a test rather than a description.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"
SUPERFLY = DATA / "broom" / "lineage" / "mercurial-superfly.yaml"
PHOTOS = DATA / "broom" / "photos.yaml"

SILO = "mercurial superfly"


def _doc() -> dict:
    with SUPERFLY.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _generations() -> list[dict]:
    return _doc().get("generations") or []


def test_the_superfly_lineage_file_exists() -> None:
    assert SUPERFLY.is_file(), f"missing lineage file: {SUPERFLY}"


def test_header_names_the_superfly_silo() -> None:
    doc = _doc()
    assert doc.get("brand") == "nike"
    assert doc.get("silo") == SILO
    assert doc.get("category") == "broom_lineage"


def test_it_has_a_researched_lineage() -> None:
    # Not a stub: a Superfly worth reading covers most of the line.
    gens = _generations()
    assert len(gens) >= 6, f"only {len(gens)} generations; expected a fuller lineage"


def test_every_entry_is_tagged_as_a_superfly() -> None:
    wrong = [
        f"{e.get('name', '?')}: brand={e.get('brand')} silo={e.get('silo')!r}"
        for e in _generations()
        if e.get("brand") != "nike" or e.get("silo") != SILO
    ]
    assert not wrong, "entries not tagged nike/'mercurial superfly':\n" + "\n".join(wrong)


def test_the_line_starts_at_superfly_i_in_2009() -> None:
    # The Superfly is the high-cut Mercurial branch; the 1998 R9 and 2002 Vapor originals
    # belong to the vapor lineage, so this file must not reach back before 2009.
    years = [e.get("year") for e in _generations() if isinstance(e.get("year"), int)]
    assert years, "no plausible years found"
    assert min(years) == 2009, f"Superfly lineage should start at 2009, got {min(years)}"


def test_the_2009_entry_resolves_its_verified_photo() -> None:
    with PHOTOS.open(encoding="utf-8") as fh:
        photos_doc = yaml.safe_load(fh) or {}
    known = {p.get("id") for p in (photos_doc.get("photos") or photos_doc or [])}

    entry_2009 = next((e for e in _generations() if e.get("year") == 2009), None)
    assert entry_2009 is not None, "no 2009 Superfly I entry"
    photo = entry_2009.get("photo")
    assert photo == "mercurial-superfly-i-2009", f"unexpected 2009 photo id: {photo!r}"
    assert photo in known, f"photo id {photo!r} not present in photos.yaml"

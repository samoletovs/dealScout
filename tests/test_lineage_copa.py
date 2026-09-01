"""Copa-specific guards for ``data/broom/lineage/copa.yaml``.

The generic lineage suite in ``test_broom_lineage.py`` validates *whatever* lineage
files exist; it passes vacuously for the Copa silo when ``copa.yaml`` is absent. This
file asserts the Copa lineage is actually present and anchored on the boot the whole
silo descends from — the 1979 Copa Mundial — so removing or gutting ``copa.yaml`` fails
a test rather than silently shrinking the Archive.

Every check here fails if ``copa.yaml`` is deleted, which is the point: it is the test
that fails without the change that added the file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"
COPA = DATA / "broom" / "lineage" / "copa.yaml"
PHOTOS = DATA / "broom" / "photos.yaml"


@pytest.fixture(scope="module")
def doc() -> dict:
    assert COPA.is_file(), f"missing Copa lineage file: {COPA}"
    with COPA.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@pytest.fixture(scope="module")
def generations(doc: dict) -> list[dict]:
    gens = doc.get("generations") or []
    assert gens, "copa.yaml has no generations"
    return gens


def test_file_header_declares_the_copa_silo(doc: dict) -> None:
    assert doc.get("category") == "broom_lineage"
    assert doc.get("brand") == "adidas"
    assert doc.get("silo") == "copa"


def test_every_generation_is_tagged_copa(generations: list[dict]) -> None:
    # heritage.yaml and photos.yaml both tag the Copa Mundial as silo `copa`; the Archive
    # tells one continuous story, so the file must not mix in a second silo tag.
    off = [g.get("name", "?") for g in generations if g.get("silo") != "copa"]
    assert not off, f"generations not tagged silo 'copa': {off}"


def test_the_lineage_is_anchored_on_the_1979_copa_mundial(generations: list[dict]) -> None:
    first = min(generations, key=lambda g: g.get("sequence") or 0)
    assert first.get("sequence") == 1
    assert first.get("year") == 1979
    assert "mundial" in str(first.get("name", "")).lower()


def test_the_modern_reboot_arc_is_seeded(generations: list[dict]) -> None:
    # Origin + the six modern reboot generations = at least seven. A thinner file has
    # dropped part of the arc the seeding committed to.
    assert len(generations) >= 7, f"expected >=7 seeded generations, got {len(generations)}"
    years = [g.get("year") for g in generations]
    assert max(y for y in years if isinstance(y, int)) >= 2021, "modern Copa generations missing"


def test_the_copa_mundial_photo_reference_resolves(generations: list[dict]) -> None:
    first = min(generations, key=lambda g: g.get("sequence") or 0)
    photo = first.get("photo")
    assert photo and photo != "UNVERIFIED", "the 1979 anchor should carry its verified photo"
    with PHOTOS.open(encoding="utf-8") as fh:
        pdoc = yaml.safe_load(fh) or {}
    known = {p.get("id") for p in (pdoc.get("photos") or pdoc or [])}
    assert photo in known, f"copa photo '{photo}' not found in photos.yaml"

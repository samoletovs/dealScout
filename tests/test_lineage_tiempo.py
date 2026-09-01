"""Guards specific to the Nike Tiempo lineage (``data/broom/lineage/tiempo.yaml``).

The generic contract for every lineage silo lives in ``test_broom_lineage.py``. This
file adds the checks that are specifically true of the Tiempo: that the file exists at
all, that it carries a real timeline rather than a stub, that every row is tagged with
the ``tiempo`` silo the classifier knows, and that the one licence-verified Tiempo photo
is attached to the generation it actually shows.

Each test here fails if ``tiempo.yaml`` is absent — which is the point: it is the test
that would have caught an empty or missing file before review.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"
TIEMPO = DATA / "broom" / "lineage" / "tiempo.yaml"

# The photo that already exists in photos.yaml, and the generation it depicts.
LEGEND_IV_PHOTO = "tiempo-legend-iv-2011"
LEGEND_IV_YEAR = 2011


@pytest.fixture(scope="module")
def generations() -> list[dict]:
    assert TIEMPO.is_file(), f"missing lineage file {TIEMPO}"
    with TIEMPO.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    gens = doc.get("generations") or []
    assert gens, "tiempo.yaml has no generations"
    return gens


def test_tiempo_lineage_is_a_timeline_not_a_stub(generations: list[dict]) -> None:
    # A one-row "lineage" is a stub. The Tiempo is one of football's oldest lines; a
    # timeline that teaches anything needs several rungs. Six is a deliberate floor.
    assert len(generations) >= 6, f"only {len(generations)} generations — too thin to be a history"


def test_every_entry_is_tagged_with_the_tiempo_silo(generations: list[dict]) -> None:
    # The classifier knows this line as (nike, tiempo). A row tagged anything else would
    # pass the generic silo guard only by luck and would file the boot under the wrong line.
    wrong = [
        f"{e.get('name', '?')}: brand={e.get('brand')!r} silo={e.get('silo')!r}"
        for e in generations
        if e.get("brand") != "nike" or e.get("silo") != "tiempo"
    ]
    assert not wrong, "entries not tagged (nike, tiempo):\n" + "\n".join(wrong)


def test_the_legend_iv_photo_sits_on_the_legend_iv(generations: list[dict]) -> None:
    """The one Tiempo photo must be on the 2011 Legend IV and resolve into photos.yaml.

    Two failures are guarded at once: the photo drifting onto the wrong generation (which
    would teach the reader a false thing), and the id not existing in photos.yaml (a
    dangling reference the page could not render).
    """
    with (DATA / "broom" / "photos.yaml").open(encoding="utf-8") as fh:
        photos_doc = yaml.safe_load(fh) or {}
    known = {p.get("id") for p in (photos_doc.get("photos") or [])}
    assert LEGEND_IV_PHOTO in known, f"{LEGEND_IV_PHOTO} is not in photos.yaml"

    holders = [e for e in generations if e.get("photo") == LEGEND_IV_PHOTO]
    assert len(holders) == 1, f"expected exactly one entry to use {LEGEND_IV_PHOTO}, got {len(holders)}"
    assert holders[0].get("year") == LEGEND_IV_YEAR, (
        f"{LEGEND_IV_PHOTO} is a {LEGEND_IV_YEAR} boot but sits on year {holders[0].get('year')}"
    )

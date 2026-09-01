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


def test_named_players_are_not_blanket_unverified(generations: list[dict]) -> None:
    """At least the generations whose cited source names a wearer must fill ``players``.

    The whole line has documented signature editions — Ronaldinho's R10 (2005), the Totti
    Legend V, the Pirlo Legend VI, the Premier's 1994 Brazil squad — and the first source on
    those entries names them. A lineage that left every ``players`` blank would be hiding the
    single most engaging fact about the boot behind an "UNVERIFIED" that isn't true. This
    guards against silently regressing back to that.
    """
    filled = [e for e in generations if str(e.get("players", "")).strip() not in ("", "UNVERIFIED")]
    assert len(filled) >= 4, (
        f"only {len(filled)} generations name a player; the cited sources name at least four "
        "(Premier, Legend 2005, Legend V, Legend VI) — a blank players field there is not honest"
    )


def test_no_player_is_carried_verbatim_across_generations(generations: list[dict]) -> None:
    """The cross-generation trap in a different costume.

    A signature name belongs to the generation its source ties it to. If the identical
    ``players`` string appeared on two entries it would almost certainly mean a name was
    copied forward without a source that puts it there.
    """
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for e in generations:
        val = str(e.get("players", "")).strip()
        if val in ("", "UNVERIFIED"):
            continue
        if val in seen:
            clashes.append(f"{e.get('name')} repeats the players line of {seen[val]}")
        seen[val] = str(e.get("name"))
    assert not clashes, "\n".join(clashes)


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

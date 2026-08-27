"""Convert a foot size from a page's stated system into the EU dialect the engine speaks.

Nearly every source states EU sizes. teamsport.lv does not: it is Nike's Latvian
distributor and prints its ladder in **US** numbers, under a selector its own markup
labels ``US izmēri``. Read as EU those numbers are impossible (EU starts at 35); read as
US they are exactly a Nike men's ladder. Converting them at the collector boundary lets
teamsport contribute exact per-size stock in EU terms, so nothing downstream has to know
it speaks another dialect.

The conversion is **never inferred**. US and UK differ by roughly a full size, so guessing
the wrong one would tell the owner a boot exists in his son's size when it does not — the
one mistake this co-pilot must not make. So a caller converts only when the page names its
system (``US izmēri``) *and* the brand's ladder is recorded in ``data/size_conversions.yaml``.
A size this table cannot place returns "" — the same "we don't know" a caller already
treats as unknown — rather than a nearest guess.

Pure and side-effect free; the table is read once and cached.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path

import yaml

from .spec import normalise_size

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_CONVERSIONS_FILE = DATA_DIR / "size_conversions.yaml"

# The size systems this engine can read a label *in*. "eu" needs no conversion; the others
# are only trusted when the page states them explicitly (see module docstring).
EU = "eu"
US = "us"


@functools.lru_cache(maxsize=1)
def _tables() -> dict[str, dict[str, str]]:
    """``{brand: {us_label: eu_label}}``, normalised on both sides (cached)."""
    try:
        raw = yaml.safe_load(_CONVERSIONS_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):  # pragma: no cover - a missing data file is fatal config
        logger.warning("could not read %s; US sizes will stay unknown", _CONVERSIONS_FILE)
        return {}
    tables: dict[str, dict[str, str]] = {}
    for brand, spec in (raw.get("brands") or {}).items():
        mapping = (spec or {}).get("us_to_eu") or {}
        table: dict[str, str] = {}
        for us_label, eu_label in mapping.items():
            us_key = normalise_size(us_label)
            eu_value = normalise_size(eu_label)
            if us_key and eu_value:
                table[us_key] = eu_value
        if table:
            tables[str(brand).strip().lower()] = table
    return tables


def known_brands() -> frozenset[str]:
    """Brands with a recorded US->EU ladder."""
    return frozenset(_tables())


def us_to_eu(size: object, brand: str = "nike") -> str:
    """Convert one US size label to its normalised EU label ("" when not placeable).

    ``size`` may carry the European decimal comma teamsport prints (``10,5``); it is folded
    to a point by :func:`dealscout.spec.normalise_size` before lookup. A label the brand's
    ladder does not contain returns "" — deliberately, so a caller drops it rather than
    inventing a nearest EU size.
    """
    table = _tables().get(str(brand or "").strip().lower())
    if not table:
        return ""
    return table.get(normalise_size(size), "")

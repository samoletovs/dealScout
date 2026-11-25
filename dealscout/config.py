"""Load and lightly validate dealScout config (YAML)."""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _mapping(value: Any, path: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a mapping")
    return value


def _list(value: Any, path: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{path}: expected a list")
    return value


def _strings(value: Any, path: str) -> None:
    for index, item in enumerate(_list(value, path)):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{path}.{index}: expected a nonempty string")


def _number(value: Any, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path}: expected a finite nonnegative number")
    try:
        valid = math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{path}: expected a finite nonnegative number")


def _booleans(section: dict, path: str, names: tuple[str, ...]) -> None:
    for name in names:
        if name in section and not isinstance(section[name], bool):
            raise ValueError(f"{path}.{name}: expected a boolean")


def validate_config(config: Any) -> None:
    """Validate the sections consumed by scheduled runs before any I/O."""
    root = _mapping(config, "config")
    for index, item in enumerate(_list(root.get("watch", []), "watch")):
        path = f"watch.{index}"
        item = _mapping(item, path)
        for field in ("url", "category"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f"{path}.{field}: expected a nonempty string")
        for field in item:
            if field not in {"url", "category", "note"}:
                raise ValueError(f"{path}.{field}: unexpected field")
        if "note" in item and not isinstance(item["note"], str):
            raise ValueError(f"{path}.note: expected a string")

    filters = _mapping(root.get("filters", {}), "filters")
    _booleans(filters, "filters", (
        "reject_big_wordmarks", "sportswear_synthetic_ok", "care_no_dry_clean_only",
    ))
    for field in ("quality_signals", "reject_materials"):
        if field in filters:
            _strings(filters[field], f"filters.{field}")
    for field, choices in (
        ("logos", {"none", "tonal_only", "small_ok", "any"}),
        ("min_brand_tier", {"any", "basket", "better"}),
    ):
        if field in filters and (
            not isinstance(filters[field], str) or filters[field] not in choices
        ):
            raise ValueError(f"filters.{field}: unexpected value")
    if "natural_fibre_min" in filters:
        _number(filters["natural_fibre_min"], "filters.natural_fibre_min")
        if filters["natural_fibre_min"] > 1:
            raise ValueError("filters.natural_fibre_min: expected a ratio between 0 and 1")

    deal = _mapping(root.get("deal", {}), "deal")
    if "min_discount_pct" in deal:
        _number(deal["min_discount_pct"], "deal.min_discount_pct")
    for field in ("must_buy", "good_offer", "never_above"):
        for category, value in _mapping(deal.get(field, {}), f"deal.{field}").items():
            _number(value, f"deal.{field}.{category}")

    brands = _mapping(root.get("brands", {}), "brands")
    for tier, names in brands.items():
        _strings(names, f"brands.{tier}")

    hunts = _list(root.get("hunts", []), "hunts")
    for index, hunt in enumerate(hunts):
        path = f"hunts.{index}"
        hunt = _mapping(hunt, path)
        _booleans(hunt, path, (
            "enabled", "require_size_in_stock", "require_new", "brands_only", "observe_only",
        ))
        for field in ("require", "prefer", "sizes_by_brand", "exclude", "price"):
            if field in hunt:
                _mapping(hunt[field], f"{path}.{field}")
        for field in ("must_buy", "good_offer", "never_above", "min_reference_price",
                      "min_discount_pct"):
            if field in hunt.get("price", {}):
                _number(hunt["price"][field], f"{path}.price.{field}")
        if "queries" in hunt:
            _strings(hunt["queries"], f"{path}.queries")
        if "catalogs" in hunt:
            for i, catalog in enumerate(_list(hunt["catalogs"], f"{path}.catalogs")):
                _mapping(catalog, f"{path}.catalogs.{i}")

    serpapi = _mapping(root.get("serpapi", {}), "serpapi")
    _booleans(serpapi, "serpapi", ("enabled", "require_known_brand", "exclude_used"))
    for field, minimum in (("cycle_budget", 0), ("account_reserve", 0),
                           ("max_searches_per_refresh", 1), ("cache_days", 1),
                           ("max_results", 1)):
        if field in serpapi and (type(serpapi[field]) is not int or serpapi[field] < minimum):
            raise ValueError(f"serpapi.{field}: expected an integer >= {minimum}")
    for field in ("preferred_stores", "exclude_sources"):
        if field in serpapi:
            _strings(serpapi[field], f"serpapi.{field}")
    if "queries" in serpapi:
        for i, query in enumerate(_list(serpapi["queries"], "serpapi.queries")):
            path = f"serpapi.queries.{i}"
            query = _mapping(query, path)
            if not isinstance(query.get("q"), str) or not query["q"].strip():
                raise ValueError(f"{path}.q: expected a nonempty string")
            if "category" in query and not isinstance(query["category"], str):
                raise ValueError(f"{path}.category: expected a string")


def load_config(path: Path) -> dict[str, Any]:
    """Load a YAML config file into a dict.

    Raises FileNotFoundError if the path does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    validate_config(config)
    logger.info(
        "loaded config: user=%s, %d watch item(s)",
        config.get("user"),
        len(config.get("watch", [])),
    )
    return config

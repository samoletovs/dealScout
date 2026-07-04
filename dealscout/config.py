"""Load and lightly validate dealScout config (YAML)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def load_config(path: Path) -> dict[str, Any]:
    """Load a YAML config file into a dict.

    Raises FileNotFoundError if the path does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        config: dict[str, Any] = yaml.safe_load(fh) or {}
    logger.info(
        "loaded config: user=%s, %d watch item(s)",
        config.get("user"),
        len(config.get("watch", [])),
    )
    return config

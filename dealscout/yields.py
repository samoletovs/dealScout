"""Notice when a source quietly stops working, before it reaches zero.

The coverage note already tells the reader when a source contributed nothing. That is the
*last* symptom, not the first, and relying on it produced a wrong answer once already: a
tier-label change made the scout's pre-filter discard every candidate, two healthy
retailers reported zero, and the email accused their readers of being broken. The reader
was fine. What had actually happened was a yield collapsing from 35 to 0 — visible a run
earlier, to anyone who had kept the 35.

So this keeps it. One number per source per run, compared against that source's own
recent history rather than against the other sources, because retailers differ by an order
of magnitude in catalogue size and comparing them to each other says nothing.

Two deliberate refusals:

* **It will not judge on one observation.** A first run has no baseline, and "0 where we
  have never seen anything else" is not evidence of a fall.
* **It reports a drop, never a rise.** A source that doubles is not a fault, and treating
  every change as noteworthy is how a signal becomes noise.

Pure except for reading and writing its own file.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("state") / "yields.json"

# Below this share of the recent baseline, a source is treated as having fallen. 0.5 is a
# halving: large enough that ordinary week-to-week stock movement does not trip it, small
# enough to catch a reader that has started returning a fraction of what it used to.
DEFAULT_DROP_RATIO = 0.5

# How many past runs form the baseline. A median over a few runs ignores the one quiet
# week a mean would be dragged down by.
DEFAULT_BASELINE_RUNS = 5

# Never complain about a source that was always tiny; a fall from 2 to 0 is not evidence.
DEFAULT_MIN_BASELINE = 5


@dataclass(frozen=True)
class Drop:
    """A source yielding materially less than it recently did."""

    source: str
    label: str
    now: int
    baseline: int

    @property
    def share(self) -> float:
        return self.now / self.baseline if self.baseline else 1.0

    def describe(self) -> str:
        if self.now == 0:
            return f"{self.label} returned nothing (usually about {self.baseline})"
        return f"{self.label} returned {self.now}, usually about {self.baseline}"


def load(path: Path = DEFAULT_PATH) -> dict[str, list[int]]:
    """Past yields per source. An unreadable file is no history, never an error."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    history: dict[str, list[int]] = {}
    for source, counts in raw.items():
        if isinstance(counts, list):
            history[str(source)] = [int(c) for c in counts if isinstance(c, (int, float))]
    return history


def record(
    history: dict[str, list[int]],
    yields: dict[str, int],
    keep: int = DEFAULT_BASELINE_RUNS,
) -> dict[str, list[int]]:
    """Append this run's yields, keeping only the recent window (pure)."""
    updated = {source: list(counts) for source, counts in history.items()}
    for source, count in yields.items():
        updated.setdefault(source, []).append(int(count))
        updated[source] = updated[source][-(keep + 1) :]
    return updated


def save(history: dict[str, list[int]], path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated": datetime.now(timezone.utc).isoformat(), **history}
    path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    logger.info("source yields: %d source(s) -> %s", len(history), path)


def _median(values: list[int]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def drops(
    history: dict[str, list[int]],
    yields: dict[str, int],
    labels: dict[str, str] | None = None,
    ratio: float = DEFAULT_DROP_RATIO,
    min_baseline: int = DEFAULT_MIN_BASELINE,
) -> list[Drop]:
    """Sources yielding materially less than their own recent median (pure).

    ``history`` must be the state *before* this run's yields are recorded, or every source
    is compared against a baseline that already contains today's number and a genuine
    collapse is halved into invisibility.
    """
    labels = labels or {}
    found: list[Drop] = []
    for source, now in yields.items():
        past = history.get(source) or []
        if len(past) < 2:
            continue  # one observation is not a baseline
        baseline = _median(past)
        if baseline < min_baseline:
            continue  # a source that was always tiny cannot fall far enough to mean anything
        if now <= baseline * ratio:
            found.append(
                Drop(
                    source=source,
                    label=labels.get(source, source),
                    now=int(now),
                    baseline=int(round(baseline)),
                )
            )
    return sorted(found, key=lambda d: d.share)

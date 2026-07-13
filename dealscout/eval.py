"""Golden-set evaluation for the deal judge — a drift scorecard.

`tests/test_judge.py` proves individual rules with synthetic inputs. This module runs
the (pure) judge over a curated set of *realistic* cases in ``evals/golden.yaml`` and
reports how well its verdicts match the expected bands: band accuracy, plus precision
and recall on the "would we surface this?" decision (the money metric). It is a
regression/drift monitor, not pass/fail unit testing, so tuning changes are measurable
over time.

Run it::

    python -m dealscout.eval                 # print + write the scorecard
    python -m dealscout.eval --min-accuracy 0.90 --min-deal-precision 0.90   # gate CI
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .config import load_config
from .judge import judge
from .models import Product

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("dealscout.eval")

#: Bands the judge assigns; a deal (surfaced) is must-buy or good.
BANDS: tuple[str, ...] = ("must-buy", "good", "regular", "reject")
DEAL_BANDS: frozenset[str] = frozenset({"must-buy", "good"})

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLDEN = _REPO_ROOT / "evals" / "golden.yaml"
DEFAULT_CONFIG = _REPO_ROOT / "config.example.yaml"
_PRODUCT_FIELDS = {f.name for f in fields(Product)}


@dataclass(frozen=True)
class GoldenCase:
    """One curated case: a product and the band the judge should assign it."""

    id: str
    product: Product
    expected_band: str
    note: str = ""

    @property
    def expected_is_deal(self) -> bool:
        return self.expected_band in DEAL_BANDS


@dataclass(frozen=True)
class CaseResult:
    """The judge's verdict on a golden case, compared to the expectation."""

    case: GoldenCase
    predicted_band: str
    predicted_is_deal: bool
    reasons: tuple[str, ...]

    @property
    def band_ok(self) -> bool:
        return self.predicted_band == self.case.expected_band

    @property
    def deal_ok(self) -> bool:
        return self.predicted_is_deal == self.case.expected_is_deal


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    """Return numerator/denominator, or None when the denominator is 0 (undefined)."""
    return numerator / denominator if denominator else None


@dataclass
class EvalResult:
    """Aggregate metrics over a run of the judge against the golden set."""

    results: list[CaseResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def accuracy(self) -> float:
        """Share of cases whose predicted band equals the expected band."""
        if not self.results:
            return 0.0
        return sum(r.band_ok for r in self.results) / self.total

    def _deal_counts(self) -> tuple[int, int, int, int]:
        """Return (true_pos, false_pos, false_neg, true_neg) for the is_deal decision."""
        tp = sum(r.predicted_is_deal and r.case.expected_is_deal for r in self.results)
        fp = sum(r.predicted_is_deal and not r.case.expected_is_deal for r in self.results)
        fn = sum(not r.predicted_is_deal and r.case.expected_is_deal for r in self.results)
        tn = sum(not r.predicted_is_deal and not r.case.expected_is_deal for r in self.results)
        return tp, fp, fn, tn

    @property
    def deal_precision(self) -> float | None:
        """Of the items we'd surface, the share that should be surfaced."""
        tp, fp, _, _ = self._deal_counts()
        return _safe_ratio(tp, tp + fp)

    @property
    def deal_recall(self) -> float | None:
        """Of the items that should be surfaced, the share we catch."""
        tp, _, fn, _ = self._deal_counts()
        return _safe_ratio(tp, tp + fn)

    @property
    def deal_f1(self) -> float | None:
        precision, recall = self.deal_precision, self.deal_recall
        if precision is None or recall is None:
            return None
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    def band_stats(self) -> dict[str, dict[str, Any]]:
        """Per-band expected/predicted counts with precision and recall."""
        stats: dict[str, dict[str, Any]] = {}
        for band in BANDS:
            actual = sum(r.case.expected_band == band for r in self.results)
            predicted = sum(r.predicted_band == band for r in self.results)
            correct = sum(
                r.predicted_band == band and r.case.expected_band == band
                for r in self.results
            )
            stats[band] = {
                "actual": actual,
                "predicted": predicted,
                "correct": correct,
                "precision": _safe_ratio(correct, predicted),
                "recall": _safe_ratio(correct, actual),
            }
        return stats

    def misses(self) -> list[CaseResult]:
        """Cases where the predicted band differs from the expected band."""
        return [r for r in self.results if not r.band_ok]


def _build_product(raw: dict[str, Any], case_id: str) -> Product:
    """Construct a Product from a golden-case mapping, coercing container types."""
    data = {k: v for k, v in raw.items() if k in _PRODUCT_FIELDS}
    if data.get("quality_signals") is not None and "quality_signals" in data:
        data["quality_signals"] = frozenset(data["quality_signals"])
    if "materials" in data and data["materials"] is None:
        data["materials"] = {}
    try:
        return Product(**data)
    except TypeError as exc:
        raise ValueError(f"golden case {case_id!r}: invalid product ({exc})") from exc


def load_golden(path: Path = DEFAULT_GOLDEN) -> list[GoldenCase]:
    """Load and validate the golden set. Raises ValueError on a malformed file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases: list[GoldenCase] = []
    for entry in raw.get("cases", []):
        case_id = str(entry.get("id") or "<unnamed>")
        expected = entry.get("expected") or {}
        band = expected.get("band")
        if band not in BANDS:
            raise ValueError(
                f"golden case {case_id!r}: expected.band must be one of {BANDS}, got {band!r}"
            )
        product = _build_product(entry.get("product") or {}, case_id)
        cases.append(
            GoldenCase(
                id=case_id,
                product=product,
                expected_band=band,
                note=str(entry.get("note", "")),
            )
        )
    if not cases:
        raise ValueError(f"no cases found in {path}")
    return cases


def evaluate(cases: list[GoldenCase], config: dict[str, Any]) -> EvalResult:
    """Run the judge over every case and collect the results."""
    results = [
        CaseResult(
            case=case,
            predicted_band=(verdict := judge(case.product, config)).band,
            predicted_is_deal=verdict.is_deal,
            reasons=verdict.reasons,
        )
        for case in cases
    ]
    return EvalResult(results)


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def format_scorecard(result: EvalResult) -> str:
    """Render the eval result as a Markdown scorecard."""
    lines = [
        f"# dealScout judge — eval scorecard ({date.today().isoformat()})",
        "",
        f"**Cases:** {result.total}  ·  "
        f"**Band accuracy:** {_pct(result.accuracy)}  ·  "
        f"**Deal precision:** {_pct(result.deal_precision)}  ·  "
        f"**Deal recall:** {_pct(result.deal_recall)}  ·  "
        f"**Deal F1:** {_pct(result.deal_f1)}",
        "",
        "## Per band",
        "| band | expected | predicted | correct | precision | recall |",
        "|------|---------:|----------:|--------:|----------:|-------:|",
    ]
    stats = result.band_stats()
    for band in BANDS:
        s = stats[band]
        lines.append(
            f"| {band} | {s['actual']} | {s['predicted']} | {s['correct']} | "
            f"{_pct(s['precision'])} | {_pct(s['recall'])} |"
        )

    misses = result.misses()
    lines += ["", f"## Misses ({len(misses)})"]
    if misses:
        lines += [
            "| id | expected | predicted | reasons |",
            "|----|----------|-----------|---------|",
        ]
        lines += [
            f"| {m.case.id} | {m.case.expected_band} | {m.predicted_band} | "
            f"{'; '.join(m.reasons)} |"
            for m in misses
        ]
    else:
        lines.append("None — every case matched its expected band.")

    lines += [
        "",
        "_Scored against config.example.yaml. Grow evals/golden.yaml from real sale "
        "emails — especially cases the judge gets wrong._",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI: score the judge against the golden set; optionally gate on thresholds."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN, help="golden-set YAML")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="config used to score")
    parser.add_argument("--out", type=Path, default=Path("out/eval-scorecard.md"), help="scorecard output path")
    parser.add_argument("--min-accuracy", type=float, default=None, help="fail if band accuracy is below this")
    parser.add_argument("--min-deal-precision", type=float, default=None, help="fail if deal precision is below this")
    parser.add_argument("--min-deal-recall", type=float, default=None, help="fail if deal recall is below this")
    args = parser.parse_args(argv)

    cases = load_golden(args.golden)
    config = load_config(args.config)
    result = evaluate(cases, config)
    scorecard = format_scorecard(result)

    sys.stdout.write(scorecard)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(scorecard, encoding="utf-8")
    logger.info(
        "eval: %d cases, accuracy=%s, deal precision=%s, recall=%s, misses=%d",
        result.total, _pct(result.accuracy), _pct(result.deal_precision),
        _pct(result.deal_recall), len(result.misses()),
    )

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(scorecard + "\n")

    gates: list[tuple[str, float | None, float | None]] = [
        ("band accuracy", result.accuracy, args.min_accuracy),
        ("deal precision", result.deal_precision, args.min_deal_precision),
        ("deal recall", result.deal_recall, args.min_deal_recall),
    ]
    failed = False
    for label, actual, minimum in gates:
        if minimum is not None and (actual is None or actual < minimum):
            logger.error("eval gate failed: %s %s < %.0f%%", label, _pct(actual), minimum * 100)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

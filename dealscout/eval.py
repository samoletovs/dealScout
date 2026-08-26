"""Golden-set evaluation for the deal judge — a drift scorecard.

`tests/test_judge.py` proves individual rules with synthetic inputs. This module runs
the (pure) judge over a curated set of *realistic* cases in ``evals/golden.yaml`` and
reports how well its verdicts match the expected bands: band accuracy, plus precision
and recall on the "would we surface this?" decision (the money metric). It is a
regression/drift monitor, not pass/fail unit testing, so tuning changes are measurable
over time.

**Two judges, scored separately.** A case with no ``hunt:`` is scored by the wardrobe
judge (:func:`dealscout.judge.judge`). A case naming a hunt is scored by the *hunt* judge
(:func:`dealscout.hunt.judge_hunt`) against that hunt as it is actually configured, so a
config regression — someone loosening ``min_reference_price``, say — shows up here too.
Blending the two into one number would hide which of them a change touched, so the
scorecard reports each.

**Band alone is not enough.** A case can reach the right band for entirely the wrong
reason: `Diadora Maximus Elite Academy FG` is correctly rejected today, but only because
Diadora is not in the brand list — nothing has looked at its tier. A case may therefore
also assert ``expected.attrs``, pinning the *resolved attributes* the verdict was built
from. That is what stops a golden case certifying an accident.

Run it::

    python -m dealscout.eval                 # print + write the scorecard
    python -m dealscout.eval --min-accuracy 0.90 --min-deal-precision 0.90   # gate CI
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field, fields, replace
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .config import load_config
from .hunt import judge_hunt, resolve_attrs
from .judge import judge
from .models import Hunt, Product
from .spec import extract_attrs, merge_vocab

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
    """One curated case: a product and the band the judge should assign it.

    ``hunt_id`` selects which judge scores it. ``expected_attrs`` optionally pins the
    resolved attributes behind the verdict, so a case cannot pass on a coincidence.
    """

    id: str
    product: Product
    expected_band: str
    note: str = ""
    hunt_id: str = ""  # "" = score with the wardrobe judge
    expected_attrs: dict[str, str] = field(default_factory=dict)

    @property
    def expected_is_deal(self) -> bool:
        return self.expected_band in DEAL_BANDS

    @property
    def kind(self) -> str:
        return "hunt" if self.hunt_id else "wardrobe"


@dataclass(frozen=True)
class CaseResult:
    """The judge's verdict on a golden case, compared to the expectation."""

    case: GoldenCase
    predicted_band: str
    predicted_is_deal: bool
    reasons: tuple[str, ...]
    predicted_attrs: dict[str, str] = field(default_factory=dict)

    @property
    def band_ok(self) -> bool:
        return self.predicted_band == self.case.expected_band

    @property
    def deal_ok(self) -> bool:
        return self.predicted_is_deal == self.case.expected_is_deal

    @property
    def attr_misses(self) -> tuple[str, ...]:
        """Attributes the case pinned that the engine read differently."""
        return tuple(
            f"{name}={self.predicted_attrs.get(name) or '(unstated)'}, wanted {wanted}"
            for name, wanted in sorted(self.case.expected_attrs.items())
            if self.predicted_attrs.get(name) != wanted
        )

    @property
    def attr_ok(self) -> bool | None:
        """True/False when the case pinned attributes, None when it pinned none.

        None rather than True: a case that asserts nothing has not been checked, and
        counting it as a pass would inflate the metric with cases that measure nothing.
        """
        if not self.case.expected_attrs:
            return None
        return not self.attr_misses


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

    @property
    def attr_checked(self) -> int:
        """How many cases pinned attributes at all."""
        return sum(r.attr_ok is not None for r in self.results)

    @property
    def attr_accuracy(self) -> float | None:
        """Share of attribute-pinning cases whose attributes all matched.

        None when no case pins any attribute — undefined, not zero, and not 100%.
        """
        return _safe_ratio(sum(r.attr_ok is True for r in self.results), self.attr_checked)

    def attr_misses(self) -> list[CaseResult]:
        """Cases whose pinned attributes did not match what the engine read."""
        return [r for r in self.results if r.attr_ok is False]

    def by_kind(self) -> dict[str, dict[str, Any]]:
        """Per-judge counts and band accuracy, so a blend can't hide which judge moved."""
        stats: dict[str, dict[str, Any]] = {}
        for kind in ("wardrobe", "hunt"):
            group = [r for r in self.results if r.case.kind == kind]
            stats[kind] = {
                "cases": len(group),
                "correct": sum(r.band_ok for r in group),
                "accuracy": _safe_ratio(sum(r.band_ok for r in group), len(group)),
            }
        return stats


WANTED_SIZE = "@wanted"  # "a size this hunt is looking for, whatever it currently is"
UNWANTED_SIZE = "@unwanted"  # "a size it is not"


def resolve_sizes(product: Product, hunt: Hunt) -> Product:
    """Replace size sentinels with sizes derived from the hunt (pure).

    A golden case that restates a value tests a symptom; one that relates two things tests
    an invariant. Most of these cases exist to pin *tier* behaviour and need a stocked size
    only so the size gate does not reject them first — but they pinned the literal ``37.5``,
    so the owner's son growing out of that size would have broken CI on a config edit that
    was entirely correct. ``@wanted`` says what the case actually means.

    ``@unwanted`` is its opposite, for the one case that exists to prove a stated size we
    do not want is an answer rather than an uncertainty.
    """
    wanted = hunt.sizes_for(product.brand, product.title)
    if not wanted:
        return product
    resolved: set[str] = set()
    for size in product.sizes:
        if size == WANTED_SIZE:
            resolved.update(wanted)
        elif size == UNWANTED_SIZE:
            # Two whole sizes clear of anything wanted, so it cannot collide by accident.
            resolved.update(str(float(s) + 2) for s in wanted if _numeric(s))
        else:
            resolved.add(size)
    return replace(product, sizes=frozenset(resolved))


def _numeric(size: str) -> bool:
    try:
        float(size)
    except (TypeError, ValueError):
        return False
    return True


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


def _expected_attrs(raw: object, case_id: str) -> dict[str, str]:
    """Coerce an ``expected.attrs`` mapping into plain strings."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"golden case {case_id!r}: expected.attrs must be a mapping")
    return {str(k): str(v) for k, v in raw.items()}


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
                hunt_id=str(entry.get("hunt") or "").strip(),
                expected_attrs=_expected_attrs(expected.get("attrs"), case_id),
            )
        )
    if not cases:
        raise ValueError(f"no cases found in {path}")
    return cases


def load_hunts(config: dict[str, Any]) -> dict[str, Hunt]:
    """Every hunt in the scoring config, by id — including disabled ones.

    ``enabled`` governs whether a cron runs a hunt, not whether it can be scored: a
    disabled hunt still has rules worth protecting from regression.
    """
    hunts = (Hunt.from_dict(h) for h in (config.get("hunts") or []) if h.get("id"))
    return {hunt.id: hunt for hunt in hunts}


def evaluate(cases: list[GoldenCase], config: dict[str, Any]) -> EvalResult:
    """Run the right judge over every case and collect the results."""
    hunts = load_hunts(config)
    vocab = merge_vocab(config.get("vocab"))
    results: list[CaseResult] = []
    for case in cases:
        if case.hunt_id:
            hunt = hunts.get(case.hunt_id)
            if hunt is None:
                raise ValueError(
                    f"golden case {case.id!r}: no hunt {case.hunt_id!r} in the scoring config "
                    f"(have: {', '.join(sorted(hunts)) or 'none'})"
                )
            product = resolve_sizes(case.product, hunt)
            verdict = judge_hunt(product, hunt, vocab)
            attrs = resolve_attrs(product, hunt, vocab)
        else:
            verdict = judge(case.product, config)
            attrs = extract_attrs(case.product.title, case.product.category, vocab)
        results.append(
            CaseResult(
                case=case,
                predicted_band=verdict.band,
                predicted_is_deal=verdict.is_deal,
                reasons=verdict.reasons,
                predicted_attrs=attrs,
            )
        )
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
        f"**Attributes pinned:** {result.attr_checked}/{result.total} case(s)  ·  "
        f"**Attribute accuracy:** {_pct(result.attr_accuracy)}",
        "",
        "## By judge",
        "| judge | cases | correct | band accuracy |",
        "|-------|------:|--------:|--------------:|",
    ]
    kinds = result.by_kind()
    for kind, label in (("wardrobe", "wardrobe (`judge`)"), ("hunt", "hunt (`judge_hunt`)")):
        s = kinds[kind]
        lines.append(
            f"| {label} | {s['cases']} | {s['correct']} | {_pct(s['accuracy'])} |"
        )

    lines += [
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
    lines += ["", f"## Band misses ({len(misses)})"]
    if misses:
        lines += [
            "| id | judge | expected | predicted | reasons |",
            "|----|-------|----------|-----------|---------|",
        ]
        lines += [
            f"| {m.case.id} | {m.case.kind} | {m.case.expected_band} | {m.predicted_band} | "
            f"{'; '.join(m.reasons)} |"
            for m in misses
        ]
    else:
        lines.append("None — every case matched its expected band.")

    attr_misses = result.attr_misses()
    lines += ["", f"## Attribute misses ({len(attr_misses)})"]
    if attr_misses:
        lines += [
            "| id | judge | read |",
            "|----|-------|------|",
        ]
        lines += [
            f"| {m.case.id} | {m.case.kind} | {'; '.join(m.attr_misses)} |" for m in attr_misses
        ]
    else:
        lines.append("None — every pinned attribute matched.")

    lines += [
        "",
        "_Scored against config.example.yaml. Grow evals/golden.yaml from real sale "
        "emails — especially cases the judge gets wrong. Pin `expected.attrs` on any case "
        "that could reach the right band for the wrong reason._",
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
    parser.add_argument(
        "--min-attr-accuracy",
        type=float,
        default=None,
        help="fail if pinned-attribute accuracy is below this (also fails when no case pins any)",
    )
    parser.add_argument(
        "--min-kind-accuracy",
        type=float,
        default=None,
        help="fail if EITHER judge's band accuracy is below this, so the smaller set of "
        "cases cannot hide a regression behind the larger one",
    )
    args = parser.parse_args(argv)

    cases = load_golden(args.golden)
    config = load_config(args.config)
    result = evaluate(cases, config)
    scorecard = format_scorecard(result)

    sys.stdout.write(scorecard)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(scorecard, encoding="utf-8")
    logger.info(
        "eval: %d cases (%d hunt), accuracy=%s, deal precision=%s, recall=%s, "
        "attrs=%s over %d, misses=%d",
        result.total, result.by_kind()["hunt"]["cases"], _pct(result.accuracy),
        _pct(result.deal_precision), _pct(result.deal_recall),
        _pct(result.attr_accuracy), result.attr_checked, len(result.misses()),
    )

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(scorecard + "\n")

    gates: list[tuple[str, float | None, float | None]] = [
        ("band accuracy", result.accuracy, args.min_accuracy),
        ("deal precision", result.deal_precision, args.min_deal_precision),
        ("deal recall", result.deal_recall, args.min_deal_recall),
        ("attribute accuracy", result.attr_accuracy, args.min_attr_accuracy),
    ]
    # Overall accuracy is a weighted average, so a judge with few cases can regress badly
    # while the blend stays above the floor: with 15 wardrobe and 7 hunt cases, losing two
    # hunt cases outright still scores 91%. Each judge therefore gets the same floor
    # applied to it alone.
    for kind, stats in result.by_kind().items():
        if stats["cases"]:
            gates.append((f"{kind} band accuracy", stats["accuracy"], args.min_kind_accuracy))
    failed = False
    for label, actual, minimum in gates:
        if minimum is not None and (actual is None or actual < minimum):
            logger.error("eval gate failed: %s %s < %.0f%%", label, _pct(actual), minimum * 100)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

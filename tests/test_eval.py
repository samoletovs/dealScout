"""Unit tests for the golden-set evaluator (dealscout.eval)."""

from __future__ import annotations

import textwrap

import pytest

from dealscout.eval import (
    DEFAULT_CONFIG,
    DEFAULT_GOLDEN,
    GoldenCase,
    evaluate,
    format_scorecard,
    load_golden,
)
from dealscout.config import load_config
from dealscout.models import Product

# No brand gate, no fibre gate -> band is decided purely by price for these tests.
CONFIG = {
    "filters": {"natural_fibre_min": 0.0},
    "deal": {"must_buy": {"knitwear": 30}, "good_offer": {"knitwear": 60}},
}


def _case(case_id: str, price: float, expected_band: str) -> GoldenCase:
    product = Product(
        title="Test knit",
        category="knitwear",
        price=price,
        reference_price=100.0,
        currency="EUR",
        url="https://example.com/p",
        materials={"wool": 1.0},
    )
    return GoldenCase(id=case_id, product=product, expected_band=expected_band)


def test_evaluate_scores_band_accuracy_with_a_deliberate_miss():
    cases = [
        _case("a", 25.0, "must-buy"),  # correct
        _case("b", 45.0, "good"),      # correct
        _case("c", 80.0, "regular"),   # correct
        _case("d", 25.0, "regular"),   # judge says must-buy -> a miss
    ]

    result = evaluate(cases, CONFIG)

    assert result.total == 4
    assert result.accuracy == pytest.approx(0.75)
    assert [m.case.id for m in result.misses()] == ["d"]


def test_evaluate_computes_deal_precision_and_recall():
    # 'd' is predicted a deal (must-buy) but labelled regular -> a false positive.
    cases = [
        _case("a", 25.0, "must-buy"),
        _case("b", 45.0, "good"),
        _case("c", 80.0, "regular"),
        _case("d", 25.0, "regular"),
    ]

    result = evaluate(cases, CONFIG)

    assert result.deal_precision == pytest.approx(2 / 3)  # TP=2, FP=1
    assert result.deal_recall == pytest.approx(1.0)       # TP=2, FN=0


def test_load_golden_coerces_containers(tmp_path):
    path = tmp_path / "golden.yaml"
    path.write_text(
        textwrap.dedent(
            """
            cases:
              - id: t1
                product:
                  title: T
                  category: knitwear
                  price: 25
                  reference_price: 100
                  currency: EUR
                  url: https://example.com/p
                  materials: { wool: 0.8, nylon: 0.2 }
                  quality_signals: [natural_fibre]
                expected: { band: must-buy }
            """
        ),
        encoding="utf-8",
    )

    cases = load_golden(path)

    assert len(cases) == 1
    assert cases[0].product.materials == {"wool": 0.8, "nylon": 0.2}
    assert cases[0].product.quality_signals == frozenset({"natural_fibre"})
    assert cases[0].expected_is_deal is True


def test_load_golden_rejects_an_unknown_band(tmp_path):
    path = tmp_path / "golden.yaml"
    path.write_text(
        "cases:\n  - id: bad\n    product: { title: T, category: tee, price: 5, "
        "reference_price: 10, currency: EUR, url: https://x }\n    expected: { band: amazing }\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected.band"):
        load_golden(path)


def test_seed_golden_set_matches_current_judge():
    # The shipped golden set is clear-cut, so the current judge should score it high.
    result = evaluate(load_golden(DEFAULT_GOLDEN), load_config(DEFAULT_CONFIG))

    assert result.accuracy >= 0.9
    assert result.deal_precision is not None and result.deal_precision >= 0.9


def test_format_scorecard_is_markdown():
    result = evaluate([_case("a", 25.0, "must-buy")], CONFIG)

    scorecard = format_scorecard(result)

    assert "# dealScout judge — eval scorecard" in scorecard
    assert "Band accuracy:" in scorecard
    assert "## Per band" in scorecard

"""Validation of user configuration before scheduled work starts."""

from pathlib import Path

import pytest
import yaml

from dealscout.config import load_config


def _load(tmp_path: Path, value: object) -> dict:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return load_config(path)


@pytest.mark.parametrize("value", [None, [], "text", 0, False])
def test_root_must_be_a_mapping(tmp_path, value):
    with pytest.raises(ValueError, match="config"):
        _load(tmp_path, value)


@pytest.mark.parametrize(
    ("value", "path"),
    [
        ({"watch": {}}, "watch"),
        ({"watch": [None]}, "watch.0"),
        ({"watch": [{"category": "tee"}]}, "watch.0.url"),
        ({"watch": [{"url": "https://example.com", "category": "  "}]}, "watch.0.category"),
        ({"watch": [{"url": "https://example.com", "category": "tee", "extra": 1}]}, "watch.0.extra"),
        ({"filters": []}, "filters"),
        ({"deal": []}, "deal"),
        ({"brands": []}, "brands"),
        ({"hunts": {}}, "hunts"),
        ({"serpapi": []}, "serpapi"),
        ({"brands": {"better": "brand"}}, "brands.better"),
        ({"deal": {"must_buy": []}}, "deal.must_buy"),
        ({"hunts": [None]}, "hunts.0"),
        ({"hunts": [{"price": []}]}, "hunts.0.price"),
        ({"serpapi": {"queries": ["bad"]}}, "serpapi.queries.0"),
        ({"filters": {"min_brand_tier": []}}, "filters.min_brand_tier"),
        ({"filters": {"reject_big_wordmarks": "false"}}, "filters.reject_big_wordmarks"),
        ({"filters": {"min_brand_teir": "basket"}}, "filters.min_brand_teir"),
        ({"deal": {"never_abv": {"tee": 10}}}, "deal.never_abv"),
        ({"hunts": [{"exclude": {"models": {"model": "Legend"}}}]}, "hunts.0.exclude.models"),
        ({"hunts": [{"price": {"never_abv": 10}}]}, "hunts.0.price.never_abv"),
        ({"hunts": [{"require": {"tier": [["elite"]]}}]}, "hunts.0.require.tier.0"),
    ],
)
def test_invalid_shapes_report_the_field(tmp_path, value, path):
    with pytest.raises(ValueError) as error:
        _load(tmp_path, value)
    assert str(error.value).startswith(f"{path}:")


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "bad"])
@pytest.mark.parametrize(
    ("field", "path"),
    [
        (lambda value: {"filters": {"natural_fibre_min": value}}, "filters.natural_fibre_min"),
        (lambda value: {"deal": {"must_buy": {"tee": value}}}, "deal.must_buy.tee"),
        (lambda value: {"deal": {"min_discount_pct": value}}, "deal.min_discount_pct"),
        (lambda value: {"hunts": [{"price": {"good_offer": value}}]}, "hunts.0.price.good_offer"),
        (lambda value: {"serpapi": {"cycle_budget": value}}, "serpapi.cycle_budget"),
    ],
)
def test_invalid_thresholds_report_the_field(tmp_path, value, field, path):
    with pytest.raises(ValueError) as error:
        _load(tmp_path, field(value))
    assert str(error.value).startswith(f"{path}:")


def test_optional_sections_and_zero_thresholds_are_valid(tmp_path):
    config = {"watch": [], "filters": {"natural_fibre_min": 0}, "deal": {"must_buy": {"tee": 0}}}
    assert _load(tmp_path, config) == config


def test_example_config_is_valid():
    assert load_config(Path("config.example.yaml"))["watch"]


def test_numeric_strings_and_scalar_hunt_query_remain_valid(tmp_path):
    from dealscout.discovery import query_terms

    config = _load(tmp_path, {
        "filters": {"natural_fibre_min": "0.7"},
        "hunts": [{"price": {"good_offer": "100"}, "queries": "boots sale"}],
    })
    assert query_terms(config) == ["boots sale"]

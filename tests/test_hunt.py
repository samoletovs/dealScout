"""Unit tests for the hunt judge — the domain-neutral Hunt spec evaluator."""

from __future__ import annotations

from dealscout.hunt import judge_hunt, resolve_attrs
from dealscout.models import Hunt, Product

OWNED_URL = (
    "https://www.sportsdirect.lv/"
    "nike-kids-legend-10-elite-firm-ground-football-boots-084181"
)

# Mirrors the boots-junior hunt in config.example.yaml.
BOOTS = {
    "id": "boots-junior",
    "category": "football_boots",
    "for": "junior",
    "sizes": ["37", "37.5"],
    "brands": ["Nike", "adidas", "Puma"],
    "require": {"tier": ["elite"], "soleplate": ["AG", "FG"]},
    "prefer": {"soleplate": ["AG", "FG"], "fit": ["junior"]},
    "exclude": {
        "models": ["Legend 10 Elite"],
        "urls": [OWNED_URL],
        "sources": ["dhgate"],
    },
    "price": {
        "must_buy": 70,
        "good_offer": 100,
        "never_above": 100,
        "min_reference_price": 200,
    },
}


def _hunt(**overrides) -> Hunt:
    return Hunt.from_dict({**BOOTS, **overrides})


def _product(**overrides) -> Product:
    base = dict(
        title="Nike Jr. Mercurial Superfly 10 Elite AG",
        category="football_boots",
        price=65.0,
        reference_price=280.0,
        currency="EUR",
        url="https://www.sportsdirect.lv/nike-jr-superfly-10-elite-ag-123456",
        brand="Nike",
        source="sportsdirect.lv",
        sizes=frozenset({"37", "37.5"}),
        sizes_known=True,
    )
    base.update(overrides)
    return Product(**base)


def test_hunt_from_dict_should_map_the_yaml_shape():
    hunt = _hunt()
    assert hunt.for_whom == "junior"
    assert hunt.sizes == ("37", "37.5")
    assert hunt.brands == ("Nike", "adidas", "Puma")
    assert hunt.require["tier"] == ("elite",)
    assert (hunt.must_buy, hunt.good_offer, hunt.never_above) == (70, 100, 100)
    assert hunt.min_reference_price == 200
    assert hunt.exclude_models == ("Legend 10 Elite",)


def test_should_call_a_cheap_fully_verified_elite_boot_a_must_buy():
    verdict = judge_hunt(_product(price=65.0), _hunt())
    assert verdict.is_deal is True
    assert verdict.band == "must-buy"
    assert not any("verify on click" in r for r in verdict.reasons)


def test_should_call_a_boot_under_the_ceiling_but_over_must_buy_a_good_offer():
    verdict = judge_hunt(_product(price=95.0), _hunt())
    assert verdict.is_deal is True
    assert verdict.band == "good"


def test_should_cap_an_unverified_boot_below_must_buy_and_say_what_to_check():
    # The title never states a soleplate: that is unknown, not failed.
    verdict = judge_hunt(_product(title="Nike Jr. Mercurial Superfly 10 Elite"), _hunt())
    assert verdict.is_deal is True
    assert verdict.band == "good"
    assert any("verify on click: soleplate" in r for r in verdict.reasons)


def test_should_penalise_each_unknown_so_a_verified_boot_ranks_higher():
    verified = judge_hunt(_product(), _hunt())
    unverified = judge_hunt(_product(title="Nike Jr. Mercurial Superfly 10 Elite"), _hunt())
    assert verified.score > unverified.score


def test_should_reject_a_takedown_model_that_is_not_the_elite_tier():
    verdict = judge_hunt(_product(title="Nike Jr. Mercurial Superfly 10 Academy AG"), _hunt())
    assert verdict.is_deal is False
    assert "tier=mid" in verdict.reasons[0]


def test_should_reject_a_soleplate_we_do_not_want():
    verdict = judge_hunt(_product(title="Nike Jr. Mercurial Superfly 10 Elite TF"), _hunt())
    assert verdict.is_deal is False
    assert "soleplate=TF" in verdict.reasons[0]


def test_should_never_re_surface_a_boot_already_owned():
    verdict = judge_hunt(_product(title="Nike Kids' Legend 10 Elite FG"), _hunt())
    assert verdict.is_deal is False
    assert "already owned" in verdict.reasons[0]


def test_should_exclude_by_url_ignoring_any_colourway_fragment():
    verdict = judge_hunt(_product(url=f"{OWNED_URL}#colcode=08418103"), _hunt())
    assert verdict.is_deal is False
    assert "already owned" in verdict.reasons[0]


def test_should_reject_a_seller_on_the_exclude_list():
    verdict = judge_hunt(_product(source="dhgate.com"), _hunt())
    assert verdict.is_deal is False
    assert "seller" in verdict.reasons[0]


def test_should_reject_a_used_boot_when_the_hunt_requires_new():
    verdict = judge_hunt(_product(condition="used"), _hunt())
    assert verdict.is_deal is False
    assert "condition is used" in verdict.reasons[0]


def test_should_reject_a_boot_over_the_hard_ceiling():
    verdict = judge_hunt(_product(price=140.0), _hunt())
    assert verdict.is_deal is False
    assert "ceiling" in verdict.reasons[0]


def test_should_reject_a_lookalike_whose_rrp_proves_it_is_not_a_flagship():
    verdict = judge_hunt(_product(reference_price=120.0), _hunt())
    assert verdict.is_deal is False
    assert "not a flagship" in verdict.reasons[0]


def test_should_flag_a_missing_rrp_instead_of_rejecting_the_boot():
    verdict = judge_hunt(_product(reference_price=None), _hunt())
    assert verdict.is_deal is True
    assert verdict.band == "good"
    assert any("verify on click: RRP" in r for r in verdict.reasons)


def test_should_reject_a_boot_that_is_not_discounted_enough():
    hunt = _hunt(price={**BOOTS["price"], "min_discount_pct": 60})
    verdict = judge_hunt(_product(price=95.0, reference_price=200.0), hunt)
    assert verdict.is_deal is False
    assert "% off" in verdict.reasons[0]


def test_should_reject_when_the_wanted_size_is_not_in_stock():
    verdict = judge_hunt(_product(sizes=frozenset({"40", "41"})), _hunt())
    assert verdict.is_deal is False
    assert "not in stock" in verdict.reasons[0]


def test_should_match_a_size_written_in_a_different_notation():
    verdict = judge_hunt(_product(sizes=frozenset({"EU 37,5", "40"})), _hunt())
    assert verdict.is_deal is True
    assert any("size 37.5 in stock" in r for r in verdict.reasons)


def test_should_flag_rather_than_drop_a_listing_that_never_stated_its_sizes():
    verdict = judge_hunt(_product(sizes=frozenset(), sizes_known=False), _hunt())
    assert verdict.is_deal is True
    assert any("verify on click: size" in r for r in verdict.reasons)


def test_should_never_claim_stock_it_did_not_see_when_the_size_gate_is_off():
    hunt = _hunt(require_size_in_stock=False)
    verdict = judge_hunt(_product(sizes=frozenset({"40"})), hunt)
    assert verdict.is_deal is True
    assert any("verify on click: size" in r for r in verdict.reasons)
    assert not any("in stock" in r for r in verdict.reasons)


def test_should_rank_the_first_choice_brand_above_the_last_at_the_same_price():
    nike = judge_hunt(_product(brand="Nike"), _hunt())
    puma = judge_hunt(
        _product(title="Puma Future 8 Ultimate Jr FG/AG", brand="Puma"), _hunt()
    )
    assert nike.is_deal and puma.is_deal
    assert nike.score > puma.score


def test_should_rank_a_cheap_second_choice_brand_above_a_pricey_first_choice():
    # Price must dominate brand preference, or the ranking stops being about deals.
    puma = judge_hunt(
        _product(title="Puma Future 8 Ultimate Jr FG/AG", brand="Puma", price=60.0), _hunt()
    )
    nike = judge_hunt(_product(brand="Nike", price=99.0), _hunt())
    assert puma.score > nike.score


def test_should_score_a_preferred_soleplate_above_an_accepted_one():
    ag = judge_hunt(_product(title="Nike Jr. Mercurial Superfly 10 Elite AG"), _hunt())
    fg = judge_hunt(_product(title="Nike Jr. Mercurial Superfly 10 Elite FG"), _hunt())
    assert ag.score > fg.score
    assert any("preferred soleplate: AG" in r for r in ag.reasons)


def test_should_not_report_a_boot_priced_above_the_good_offer_band():
    hunt = _hunt(price={**BOOTS["price"], "never_above": 200})
    verdict = judge_hunt(_product(price=150.0), hunt)
    assert verdict.is_deal is False
    assert verdict.band == "regular"
    assert "above target price" in verdict.reasons[-1]


def test_collector_supplied_attributes_should_beat_a_guess_from_the_title():
    # The page stated the soleplate even though the title did not — trust the page.
    product = _product(title="Nike Jr. Mercurial Superfly 10 Elite", attrs={"soleplate": "AG"})
    assert resolve_attrs(product, _hunt())["soleplate"] == "AG"
    assert judge_hunt(product, _hunt()).band == "must-buy"


def test_an_empty_requirement_should_gate_nothing():
    hunt = _hunt(require={})
    verdict = judge_hunt(_product(title="Nike Jr. Mercurial Superfly 10 Academy TF"), hunt)
    assert verdict.is_deal is True


def test_a_brand_list_should_only_rank_by_default():
    # Skechers is not on the list, but brands_only is off — it still scores as a deal.
    verdict = judge_hunt(_product(title="Skechers SKX 01 Elite FG", brand="Skechers"), _hunt())
    assert verdict.is_deal is True


def test_brands_only_should_reject_a_brand_off_the_list():
    hunt = _hunt(brands_only=True)
    verdict = judge_hunt(_product(title="Skechers SKX 01 Elite FG", brand="Skechers"), hunt)
    assert verdict.is_deal is False
    assert "brand not in" in verdict.reasons[0]


def test_brands_only_should_keep_a_brand_named_in_the_title_alone():
    # sportsdirect splits brand and name, so a collector may supply an empty brand.
    hunt = _hunt(brands_only=True)
    verdict = judge_hunt(
        _product(title="Puma Ultra 5 Ultimate Firm Ground Football Boots Juniors", brand=""), hunt
    )
    assert verdict.is_deal is True

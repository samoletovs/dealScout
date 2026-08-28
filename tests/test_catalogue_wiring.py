"""Tests for wiring the catalogue into the judge and the email.

Three things had to be true for the catalogue to be worth having: it must be consulted
*before* the vocabulary (which cannot be fixed by adding words), a config that names a
tier the engine no longer assigns must fail loudly rather than quietly match nothing, and
the email must say which flagship a boot is.
"""

from __future__ import annotations

import pytest

from dealscout.hunt import judge_hunt, known_values, resolve_attrs, validate_hunt
from dealscout.models import Change, Hunt, Product, Verdict
from dealscout.notify import render_hunt_report, render_shortlist, tier_phrase
from dealscout.scout import title_confirms, title_plausible

BOOTS = {
    "id": "boots-junior",
    "category": "football_boots",
    "sizes": ["37.5"],
    "brands": ["Nike", "adidas"],
    "require": {"tier": ["adult-flagship", "junior-flagship"], "soleplate": ["AG", "FG"]},
    "price": {"must_buy": 70, "good_offer": 100, "never_above": 100},
}


def _hunt(**overrides) -> Hunt:
    return Hunt.from_dict({**BOOTS, **overrides})


def _product(title: str = "Nike Kids Tiempo Legend VII Elite FG", **overrides) -> Product:
    base = dict(
        title=title,
        category="football_boots",
        price=55.0,
        reference_price=None,
        currency="EUR",
        url="https://example.com/boot",
        brand="Nike",
        source="komanda.lv",
        sizes=frozenset({"37.5"}),
        sizes_known=True,
    )
    base.update(overrides)
    return Product(**base)


# ------------------------------------------------------------------------- precedence


def test_the_catalogue_should_be_consulted_before_the_vocabulary():
    # The vocabulary reads "elite" out of this title by substring and cannot be taught
    # otherwise: `extract_attrs` returns the first match in declaration order, so `elite`
    # shadows `academy` permanently. The catalogue has to win, or the trap is unfixable.
    attrs = resolve_attrs(_product("Diadora Maximus Elite Academy FG"), _hunt())

    assert attrs["tier"] == "takedown"


def test_a_catalogue_that_declines_to_name_a_tier_should_not_have_the_guess_restored():
    # Puma's flagship is "Ultimate", so "Elite" tells us nothing — and the vocabulary's
    # reading must not quietly reappear underneath that silence.
    attrs = resolve_attrs(_product("Puma Future 8 Elite FG", brand="Puma"), _hunt())

    assert "tier" not in attrs


def test_the_collector_should_still_win_over_the_catalogue():
    # A value read off the product page beats anything inferred from a title.
    product = _product(attrs={"tier": "adult-flagship"})

    assert resolve_attrs(product, _hunt())["tier"] == "adult-flagship"


def test_a_category_without_a_catalogue_should_keep_using_the_vocabulary():
    # Proof the catalogue is category-scoped: running shoes still read elite/mid/entry.
    hunt = Hunt.from_dict({"id": "run", "category": "running_shoes", "require": {}})
    product = _product("Nike Vaporfly 3", category="running_shoes")

    assert resolve_attrs(product, hunt)["tier"] == "elite"


# ------------------------------------------------------------------------- validation


def test_a_hunt_requiring_a_tier_the_engine_never_assigns_should_raise():
    # The loud break. The owner's real hunt lives in a private config.local.yaml; a value
    # that silently stops matching turns it into a hunt that finds nothing and, because
    # silence is the normal output, says nothing about why.
    with pytest.raises(ValueError, match="require.tier names 'elite'"):
        validate_hunt(_hunt(require={"tier": ["elite"]}))


def test_the_error_should_name_the_values_that_would_have_worked():
    with pytest.raises(ValueError, match="adult-flagship"):
        validate_hunt(_hunt(require={"tier": ["elite"]}))


def test_the_shipped_require_values_should_validate():
    validate_hunt(_hunt())  # must not raise


def test_validation_should_be_case_insensitive_like_the_judge():
    # Config writes soleplates as "AG"; a validator stricter than `_check` would reject
    # config that works perfectly.
    validate_hunt(_hunt(require={"soleplate": ["ag", "FG"]}))


def test_a_stale_prefer_value_should_warn_rather_than_raise(caplog):
    # `prefer` only scores, so a stale value costs ranking, not results.
    validate_hunt(_hunt(prefer={"tier": ["elite"]}))

    assert "will not score" in caplog.text


def test_an_attribute_the_engine_does_not_supply_should_not_be_validated():
    # Collectors can attach anything; we have no closed set to check it against.
    validate_hunt(_hunt(require={"colourway": ["blueprint"]}))


def test_known_values_should_take_tier_from_the_catalogue_not_the_vocabulary():
    assert "adult-flagship" in known_values("football_boots")["tier"]
    assert "elite" not in known_values("football_boots")["tier"]


def test_known_values_should_fall_back_to_the_vocabulary_without_a_catalogue():
    assert known_values("running_shoes")["tier"] == frozenset({"elite", "mid", "entry"})


# -------------------------------------------------------------- the rrp proxy, demoted


def test_the_rrp_gate_should_not_reject_a_junior_flagship_priced_like_one():
    # RRP €90 is the top of the junior range. The old gate called it "not a flagship".
    hunt = _hunt(price={**BOOTS["price"], "min_reference_price": 110})
    product = _product("adidas Kids Copa Pure III Elite FG", brand="adidas", reference_price=90.0)

    assert judge_hunt(product, hunt).is_deal is True


def test_the_rrp_gate_should_still_reject_when_the_tier_is_unknown():
    hunt = _hunt(price={**BOOTS["price"], "min_reference_price": 110})
    product = _product("Puma Future 8 Elite FG", brand="Puma", reference_price=60.0)

    assert judge_hunt(product, hunt).is_deal is False


# ------------------------------------------------------------------------- the email


def test_a_junior_flagship_should_say_so_and_say_what_it_is_not():
    said = tier_phrase({"tier": "junior-flagship"})

    assert "junior flagship" in said
    assert "not adult construction" in said


def test_an_adult_flagship_should_be_labelled_as_one():
    assert "adult flagship" in tier_phrase({"tier": "adult-flagship"})


def test_a_takedown_should_be_labelled_as_not_the_flagship():
    assert "not the flagship" in tier_phrase({"tier": "takedown"})


def test_an_unknown_tier_should_produce_no_label_at_all():
    # The row already carries a "verify on click" caveat; inventing a label would be the
    # confident-wrong answer the catalogue exists to prevent.
    assert tier_phrase({}) == ""


def test_a_vocabulary_tier_should_not_be_dressed_up_as_a_flagship_label():
    # running_shoes still reads elite/mid/entry, which these labels do not describe.
    assert tier_phrase({"tier": "elite"}) == ""


def test_an_old_generation_should_be_named_with_its_year():
    said = tier_phrase(
        {"tier": "junior-flagship", "generation_status": "discontinued", "generation_year": "2018"}
    )

    assert "discontinued generation (2018)" in said


def test_the_shortlist_should_not_let_a_junior_boot_read_as_an_adult_flagship():
    body = render_shortlist(_hunt(), [_product(price=130.0)], [], {})

    assert "junior flagship" in body
    assert "comfort-tuned plate" in body


def test_the_shortlist_should_disclose_that_a_cheap_boot_is_eight_years_old():
    # The misrepresentation this catalogue exists to end: a 2018 boot presented as though
    # it were current-season stock, with no RRP published to hint otherwise.
    body = render_shortlist(_hunt(), [_product()], [], {})

    assert "discontinued generation (2018)" in body


def test_the_hunt_report_should_carry_the_same_label():
    results = [(_product(), Verdict(True, 9.0, (), "must-buy"), Change(_product(), "new"))]

    body = render_hunt_report(_hunt(), results)

    assert "junior flagship" in body


def test_the_raw_tier_value_should_not_be_printed_beside_its_own_label():
    # Otherwise a row reads "adult flagship · adult-flagship".
    body = render_shortlist(_hunt(), [_product()], [], {})

    assert "junior-flagship" not in body


# ------------------------------------------------------- the scout's cheap pre-filter
#
# `title_plausible` runs BEFORE a request is spent, on nothing but a name. When it read
# tier by an older rule than the judge, every candidate from a source that pre-filters on
# a name — link-following and sitemap slugs — was discarded with no exception, no empty
# parse and nothing in the log. Two whole retailers silently contributed zero, and the
# coverage note then blamed them for being broken.


@pytest.mark.parametrize(
    "title",
    [
        "adidas f50 hyperfast elite fg chaos vs control kids weiss",
        "adidas predator elite fg chaos vs control kids weiss",
        "adidas_f50_messi_elite_firm_ground_football_boots_jq0930",
        "adidas Kids F50 Elite FG",
        "Nike Jr. Mercurial Superfly 10 Elite AG",
    ],
)
def test_a_flagship_title_should_survive_the_pre_filter_under_the_new_tier_labels(title):
    assert title_plausible(title, _hunt()) is True


def test_the_pre_filter_should_still_discard_a_title_that_contradicts_the_tier():
    # It must stay a real filter — this is what saves the request budget.
    assert title_plausible("Nike Jr. Mercurial Vapor 16 Academy FG", _hunt()) is False


def test_the_pre_filter_should_keep_a_title_the_catalogue_cannot_read():
    # Unknown is not a contradiction. This is why the bug only bit titles the vocabulary
    # was confident about — silence always survived.
    #
    # The example used to be "adidas Kids Copa 19.4 FG", which stopped being unreadable
    # once the catalogue learned adidas's retired .1/.2/.3/.4 tiers — ".4" is Club, so
    # that title is now correctly *discarded* rather than passed through (see below).
    # Copa Mundial is the durable example: kangaroo leather, outside the tier ladder
    # altogether, so it genuinely states no tier and never will.
    assert title_plausible("adidas Kids Copa Mundial FG", _hunt()) is True


def test_a_legacy_numbered_takedown_should_now_be_discarded_rather_than_passed_through():
    """The other half of the change: `.4` is Club, and the pre-filter can now say so.

    Before the catalogue read adidas's retired numbering, every clearance-era boot came
    back `unknown` and was waved through to spend a request. Reading the number saves the
    budget on the takedowns and — far more importantly — stops `.1` flagships being
    dropped by `require_stated: [tier]`.
    """
    assert title_plausible("adidas Kids Copa 19.4 FG", _hunt()) is False
    assert title_plausible("adidas Kids Copa Sense.1 FG", _hunt()) is True


def test_the_pre_filter_and_the_judge_should_agree_about_tier():
    # The root cause was two resolvers, not one wrong one. Pin the agreement.
    title = "adidas Kids F50 Elite FG"
    product = _product(title, brand="adidas", reference_price=130.0)

    assert title_plausible(title, _hunt()) is True
    assert resolve_attrs(product, _hunt())["tier"] in _hunt().require["tier"]


def test_budget_ordering_should_still_recognise_a_title_that_states_everything():
    # `title_confirms` orders a limited request budget; reading tier by the old rule made
    # it answer False for every boot, quietly degrading which pages got fetched first.
    assert title_confirms("adidas Kids F50 Elite FG", _hunt()) is True
    assert title_confirms("adidas Kids Copa 19.4 FG", _hunt()) is False

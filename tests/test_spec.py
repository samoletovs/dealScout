"""Unit tests for spec extraction — reading attributes out of a product title."""

from __future__ import annotations

from dealscout.spec import (
    DEFAULT_VOCAB,
    extract_attrs,
    looks_like_eu,
    merge_vocab,
    normalise_size,
    normalise_sizes,
    size_matches,
)

BOOTS = "football_boots"


def test_should_read_tier_soleplate_fit_and_silo_from_a_title():
    attrs = extract_attrs("Nike Jr. Mercurial Superfly 10 Elite FG", BOOTS)
    assert attrs["tier"] == "elite"
    assert attrs["soleplate"] == "FG"
    assert attrs["fit"] == "junior"
    assert attrs["silo"] == "mercurial superfly"


def test_should_read_puma_ultimate_as_the_elite_tier():
    assert extract_attrs("Puma Future 8 Ultimate FG/AG", BOOTS)["tier"] == "elite"


def test_should_classify_a_takedown_model_as_a_lower_tier():
    assert extract_attrs("Nike Mercurial Superfly 10 Academy FG", BOOTS)["tier"] == "mid"
    assert extract_attrs("adidas Predator Club FxG", BOOTS)["tier"] == "entry"


def test_should_not_read_pro_as_the_elite_tier():
    # "Pro" is a takedown; only the literal "Pro Edition" is a flagship.
    assert extract_attrs("Nike Phantom GX Pro FG", BOOTS)["tier"] == "mid"


def test_should_prefer_ag_when_a_boot_is_sold_as_fg_ag():
    # Vocabulary order is the contract: AG is declared first because FG/AG is AG-capable.
    assert extract_attrs("Puma Future 8 Ultimate FG/AG", BOOTS)["soleplate"] == "AG"


def test_should_not_match_a_token_inside_a_longer_word():
    # "ag" must not fire inside "Vantage", nor "ic" inside "Classic".
    assert "soleplate" not in extract_attrs("Nike Vantage Classic boots", BOOTS)


def test_should_still_match_a_token_glued_to_a_suffix_by_a_hyphen():
    assert extract_attrs("Nike Mercurial Superfly 10 Elite AG-PRO", BOOTS)["soleplate"] == "AG"


def test_should_match_a_multi_word_token_across_a_hyphen():
    assert extract_attrs("adidas F50 Elite Firm-Ground boots", BOOTS)["soleplate"] == "FG"


def test_should_omit_an_attribute_the_title_never_stated():
    # A missing key means "unknown" — callers must never read it as "absent".
    attrs = extract_attrs("Nike Tiempo Legend 10 Elite", BOOTS)
    assert attrs["tier"] == "elite"
    assert "soleplate" not in attrs


def test_should_match_junior_despite_a_curly_apostrophe():
    assert extract_attrs("Nike Kids\u2019 Legend 10 Elite FG", BOOTS)["fit"] == "junior"
    assert extract_attrs("Nike Kids' Legend 10 Elite FG", BOOTS)["fit"] == "junior"


def test_should_return_nothing_for_a_category_the_vocabulary_does_not_know():
    assert extract_attrs("Nike Mercurial Superfly Elite FG", "skis") == {}


def test_should_read_a_second_category_with_its_own_attributes():
    attrs = extract_attrs("Nike Vaporfly 3 road racing shoe", "running_shoes")
    assert attrs["tier"] == "elite"
    assert attrs["surface"] == "road"


def test_merge_vocab_should_replace_a_default_attribute_outright():
    merged = merge_vocab({BOOTS: {"tier": {"elite": ["signature"]}}})
    assert extract_attrs("Nike Phantom Signature FG", BOOTS, merged)["tier"] == "elite"
    # Replacing (not extending) is what lets a user correct a wrong default.
    assert "tier" not in extract_attrs("Nike Phantom Elite FG", BOOTS, merged)


def test_merge_vocab_should_leave_other_attributes_and_categories_intact():
    merged = merge_vocab({BOOTS: {"tier": {"elite": ["signature"]}}})
    assert merged[BOOTS]["soleplate"] == DEFAULT_VOCAB[BOOTS]["soleplate"]
    assert "running_shoes" in merged


def test_merge_vocab_should_add_a_brand_new_category():
    merged = merge_vocab({"skis": {"tier": {"race": ["world cup", "wc"]}}})
    assert extract_attrs("Atomic Redster S9 World Cup", "skis", merged)["tier"] == "race"


def test_merge_vocab_should_ignore_malformed_blocks():
    merged = merge_vocab({BOOTS: "not-a-mapping", "skis": {"tier": "also-not-a-mapping"}})
    assert merged[BOOTS]["tier"] == DEFAULT_VOCAB[BOOTS]["tier"]
    assert merged["skis"] == {}


def test_merge_vocab_should_not_expose_the_default_to_mutation():
    merged = merge_vocab(None)
    merged[BOOTS]["tier"]["elite"] = ["nonsense"]
    assert "elite" in DEFAULT_VOCAB[BOOTS]["tier"]["elite"]


def test_normalise_size_should_canonicalise_common_labels():
    assert normalise_size("EU 37,5") == "37.5"
    assert normalise_size("38.0") == "38"
    assert normalise_size("37.5 / UK 4.5") == "37.5"
    assert normalise_size(" size 37 ") == "37"


def test_normalise_size_should_return_empty_for_an_unparseable_value():
    assert normalise_size("One Size") == ""
    assert normalise_size(None) == ""
    assert normalise_size("") == ""


def test_normalise_sizes_should_drop_anything_unparseable():
    assert normalise_sizes(["EU 37", "one size", "37,5", None]) == frozenset({"37", "37.5"})


def test_size_matches_should_compare_after_normalising_both_sides():
    assert size_matches(["37", "37.5"], ["EU 37,5", "40"]) is True


def test_size_matches_should_be_false_when_no_wanted_size_is_available():
    assert size_matches(["37", "37.5"], ["38", "38.5"]) is False


def test_size_matches_should_be_false_when_nothing_is_wanted():
    assert size_matches([], ["37"]) is False


def test_looks_like_eu_should_accept_a_european_size_table():
    assert looks_like_eu({"36", "37.5", "38"}) is True


def test_looks_like_eu_should_reject_a_uk_size_table():
    # UK 4/4.5/5 normalise perfectly but mean EU 36-38, not EU 4-5.
    assert looks_like_eu({"4", "4.5", "5"}) is False


def test_looks_like_eu_should_accept_a_mixed_table_containing_eu_sizes():
    assert looks_like_eu({"4", "37"}) is True


def test_looks_like_eu_should_reject_an_empty_or_unparseable_table():
    assert looks_like_eu([]) is False
    assert looks_like_eu(["one size"]) is False

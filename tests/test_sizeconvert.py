"""Unit tests for the US -> EU size conversion ladder.

The one invariant that actually matters to the owner: his son's Nike size, stated in EU as
37.5, is US 5 in the ladder teamsport prints. These tests relate the two systems rather
than pinning a boot to a literal size, so they keep holding as the child grows.
"""

from __future__ import annotations

from dealscout.sizeconvert import known_brands, us_to_eu


def test_the_owners_nike_eu_size_is_the_us_size_teamsport_would_print():
    # The whole feature rests on this correspondence: the size the owner asks for in EU
    # (his son's Nike size is EU 37.5) must be reachable from the US number teamsport
    # actually shows for it. If Nike ever re-tabled its ladder, this is the assertion that
    # should break — not a boot fixture. Stated as an invariant between the two systems,
    # so it keeps holding whatever size the child grows into next.
    assert us_to_eu("5", "nike") == "37.5"


def test_a_recorded_us_size_converts_to_its_nike_eu_equivalent():
    # Nike's own men's ladder: US 5 == EU 37.5, US 9 == EU 42.5, US 12 == EU 46.
    assert us_to_eu("5", "nike") == "37.5"
    assert us_to_eu("9", "nike") == "42.5"
    assert us_to_eu("12", "nike") == "46"


def test_it_folds_the_european_decimal_comma_teamsport_prints():
    # teamsport labels a half size "10,5", not "10.5"; the ladder must still place it.
    assert us_to_eu("10,5", "nike") == "44.5"


def test_a_size_outside_the_ladder_is_unknown_not_a_nearest_guess():
    # A confident wrong answer about whether a boot fits is the one failure mode the
    # engine forbids, so an unrecorded label returns "" rather than the closest EU size.
    assert us_to_eu("99", "nike") == ""
    assert us_to_eu("not-a-size", "nike") == ""


def test_an_unknown_brand_has_no_ladder_and_converts_nothing():
    assert us_to_eu("9", "reebok") == ""


def test_nike_is_a_known_brand():
    assert "nike" in known_brands()

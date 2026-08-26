"""Unit tests for source qualification (no network — the pure decision logic)."""

from __future__ import annotations

from dealscout.qualify import (
    Verdict,
    detect_guard,
    elite_evidence,
    is_top_tier,
    rate_stock,
)
from dealscout.models import Product


def _boot(title: str, price: float, rrp: float | None = None, sizes_known: bool = False) -> Product:
    return Product(
        title=title,
        category="football_boots",
        price=price,
        reference_price=rrp,
        currency="EUR",
        url=f"https://shop.example/{title.replace(' ', '-').lower()}",
        sizes=frozenset({"37"}) if sizes_known else frozenset(),
        sizes_known=sizes_known,
    )


def test_should_recognise_the_flagship_tier_by_the_words_retailers_print():
    assert is_top_tier("adidas Predator Elite FT FG") is True
    assert is_top_tier("Puma Future 8 Ultimate FG") is True
    assert is_top_tier("Mizuno Morelia Neo IV Made in Japan") is True
    assert is_top_tier("Nike Mercurial Vapor 16 Academy FG") is False


def test_should_treat_sg_pro_as_a_soleplate_not_a_tier():
    # "SG-Pro" and "AG-Pro" name the soleplate. A Phantom Elite SG-Pro is a genuine Elite
    # boot, and a shop stocking it has proven it carries the flagship line.
    assert is_top_tier("Nike Phantom GX II Elite SG-Pro Anti-Clog") is True


def test_elite_evidence_should_accept_a_discounted_flagship():
    proof = elite_evidence([_boot("Nike Phantom 6 Elite FG", 120.0, 280.0)])
    assert len(proof) == 1
    assert "RRP €280" in proof[0]


def test_elite_evidence_should_accept_a_shop_that_sells_at_rrp():
    # An official dealer (komanda.lv) publishes no `compare_at_price` because it never
    # discounts. Requiring a "was" price rejects exactly the authorised retailers most
    # worth trusting, so absolute price qualifies too.
    proof = elite_evidence([_boot("adidas Predator Elite FT FG", 280.0, None)])
    assert len(proof) == 1
    assert "at RRP" in proof[0]


def test_elite_evidence_should_ignore_a_cheap_takedown():
    assert elite_evidence([_boot("Nike Mercurial Vapor 16 Academy FG", 65.0, 90.0)]) == []


def test_elite_evidence_should_ignore_a_flagship_name_priced_like_a_takedown():
    # A junior Elite lists at €120-130. It is a fine boot to buy but it does not prove the
    # shop carries the adult flagship line, which is what qualifies a source.
    assert elite_evidence([_boot("adidas Kids Predator Elite FG", 90.0, 120.0)]) == []


def test_rate_stock_should_distinguish_exact_from_price_only():
    assert rate_stock([_boot("x Elite", 200.0, sizes_known=True)]) == "exact"
    assert rate_stock([_boot("x Elite", 200.0, sizes_known=False)]) == "price-only"
    assert rate_stock([]) == "none"


def test_detect_guard_should_name_known_bot_protection():
    assert detect_guard('<script src="/cdn-cgi/challenge-platform/x.js">') == "cloudflare"
    assert detect_guard("<html><body>boots</body></html>") == ""


def test_a_source_is_not_qualified_without_proof_of_the_flagship_tier():
    verdict = Verdict(host="shop.example", reachable=True, stock_quality="exact")
    assert verdict.qualified is False
    assert verdict.tier == "TIER 3 — not monitorable as-is"


def test_a_source_is_not_qualified_when_robots_forbids_it():
    verdict = Verdict(
        host="shop.example",
        reachable=True,
        robots_ok=False,
        stock_quality="exact",
        elite=["adidas Predator Elite — €280"],
    )
    assert verdict.qualified is False


def test_a_reachable_priced_elite_source_with_sizes_is_tier_one():
    verdict = Verdict(
        host="shop.example",
        reachable=True,
        robots_ok=True,
        stock_quality="exact",
        elite=["adidas Predator Elite — €280"],
    )
    assert verdict.qualified is True
    assert verdict.tier == "TIER 1 — exact stock"


def test_a_source_without_readable_sizes_is_tier_two():
    verdict = Verdict(
        host="shop.example",
        reachable=True,
        robots_ok=True,
        stock_quality="price-only",
        elite=["adidas Predator Elite — €280"],
    )
    assert verdict.qualified is True
    assert verdict.tier == "TIER 2 — partial"

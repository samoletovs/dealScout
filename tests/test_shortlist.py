"""Unit tests for shortlist ranking (pure — no network)."""

from __future__ import annotations

from dealscout.models import Hunt, Product
from dealscout.shortlist import (
    Delivery,
    delivery_for,
    expected_sources,
    landed_cost,
    matched_sizes,
    pick_diverse,
    source_coverage,
    split_by_size_confidence,
    stamp_house_brands,
)

HUNT = Hunt(id="boots", sizes=("37", "37.5", "37.33"))

TABLE = {
    "prodirectsport.ie": Delivery(label="Pro:Direct", shipping=7.0),
    "teamsport.lv": Delivery(label="teamsport", shipping=5.0, free_over=50.0,
                             pickup=True, house_brand="Nike"),
    "komanda.lv": Delivery(label="komanda", shipping=0.0, pickup=True),
}


def _boot(
    title: str,
    price: float,
    source: str,
    sizes: tuple[str, ...] = (),
    sizes_known: bool = False,
    brand: str = "",
    rrp: float | None = None,
) -> Product:
    return Product(
        title=title,
        category="football_boots",
        price=price,
        reference_price=rrp,
        currency="EUR",
        url=f"https://{source}/{title.replace(' ', '-').lower()}",
        source=source,
        brand=brand,
        sizes=frozenset(sizes),
        sizes_known=sizes_known,
    )


def test_landed_cost_should_add_delivery_to_the_shelf_price():
    boot = _boot("A Elite", 45.0, "prodirectsport.ie")
    assert landed_cost(boot, delivery_for("prodirectsport.ie", TABLE)) == 52.0


def test_landed_cost_should_honour_a_free_shipping_threshold():
    cheap = _boot("A Elite", 40.0, "teamsport.lv")
    dear = _boot("B Elite", 120.0, "teamsport.lv")
    assert landed_cost(cheap, delivery_for("teamsport.lv", TABLE)) == 45.0
    assert landed_cost(dear, delivery_for("teamsport.lv", TABLE)) == 120.0


def test_landed_cost_should_not_invent_postage_for_an_unknown_source():
    # A guessed shipping figure would silently reorder the list.
    boot = _boot("A Elite", 99.0, "unknown-shop.com")
    assert landed_cost(boot, delivery_for("unknown-shop.com", TABLE)) == 99.0


def test_delivery_lookup_should_ignore_a_www_prefix():
    assert delivery_for("www.prodirectsport.ie", TABLE).label == "Pro:Direct"


def test_a_cheaper_boot_can_cost_more_to_receive_than_a_dearer_one():
    # The reason this module exists: shelf price and landed cost disagree.
    shipped = _boot("A Elite", 45.0, "prodirectsport.ie")
    local = _boot("B Elite", 50.0, "komanda.lv")
    assert shipped.price < local.price
    assert landed_cost(shipped, delivery_for("prodirectsport.ie", TABLE)) > landed_cost(
        local, delivery_for("komanda.lv", TABLE)
    )


def test_matched_sizes_should_report_only_sizes_the_hunt_wants():
    boot = _boot("A Elite", 60.0, "x", sizes=("36.67", "37.33", "44"), sizes_known=True)
    assert matched_sizes(boot, HUNT) == ["37.33"]


def test_matched_sizes_should_be_empty_when_the_shop_stated_no_sizes():
    boot = _boot("A Elite", 60.0, "x", sizes=("37",), sizes_known=False)
    assert matched_sizes(boot, HUNT) == []


def test_split_should_exclude_a_boot_the_shop_says_is_not_in_your_size():
    # Stated and absent is an answer, not an uncertainty: it belongs in neither list.
    yes = _boot("Yes Elite", 60.0, "x", sizes=("37",), sizes_known=True)
    no = _boot("No Elite", 60.0, "x", sizes=("44",), sizes_known=True)
    maybe = _boot("Maybe Elite", 60.0, "x", sizes_known=False)
    confirmed, unknown = split_by_size_confidence([yes, no, maybe], HUNT)
    assert [p.title for p in confirmed] == ["Yes Elite"]
    assert [p.title for p in unknown] == ["Maybe Elite"]


def test_pick_diverse_should_cap_how_many_rows_one_shop_can_take():
    products = [_boot(f"Elite {i}", 40.0 + i, "prodirectsport.ie") for i in range(5)]
    products.append(_boot("Local Elite", 100.0, "komanda.lv"))
    picked = pick_diverse(products, TABLE, limit=3, per_source=2)
    assert sum(1 for p in picked if p.source == "prodirectsport.ie") == 2
    assert any(p.source == "komanda.lv" for p in picked)


def test_pick_diverse_should_fill_the_list_rather_than_return_a_short_one():
    # A short list is worse than a repetitive one: being handed two rows because the
    # diversity rule could not be satisfied helps nobody.
    products = [_boot(f"Elite {i}", 40.0 + i, "prodirectsport.ie") for i in range(6)]
    assert len(pick_diverse(products, TABLE, limit=5, per_source=2)) == 5


def test_pick_diverse_should_return_the_list_in_landed_cost_order():
    # The fallback appends in catalogue order, so without a final sort a "cheapest first"
    # list can show a dearer boot above a cheaper one.
    products = [_boot(f"Elite {i}", 100.0 - i * 10, "prodirectsport.ie") for i in range(5)]
    products.append(_boot("Local", 55.0, "komanda.lv"))
    picked = pick_diverse(products, TABLE, limit=6, per_source=2)
    costs = [landed_cost(p, delivery_for(p.source, TABLE)) for p in picked]
    assert costs == sorted(costs)


def test_pick_diverse_should_give_every_shop_a_row_before_any_shop_gets_a_second():
    # Cheapest-first hands the whole cap to the deepest sale before a shop with one
    # bargain is reached at all, so a shop can be squeezed off the list entirely.
    deep = [_boot(f"Deep {i}", 40.0 + i, "deep.example") for i in range(5)]
    others = [_boot(f"Only {s}", 200.0 + s, f"shop{s}.example") for s in range(3)]

    picked = pick_diverse([*deep, *others], TABLE, limit=4, per_source=3)

    assert {p.source for p in picked} == {
        "deep.example",
        "shop0.example",
        "shop1.example",
        "shop2.example",
    }


def test_pick_diverse_should_split_the_list_between_two_shops_that_can_both_fill_it():
    # Relaxing the cap cheapest-first gave the whole remainder to the shop with the
    # deepest sale; relaxing it round-robin keeps the spread the cap was there to protect.
    deep = [_boot(f"Deep {i}", 40.0 + i, "deep.example") for i in range(10)]
    local = [_boot(f"Local {i}", 60.0 + i, "komanda.lv") for i in range(5)]

    picked = pick_diverse([*deep, *local], TABLE, limit=10, per_source=3)

    assert sum(1 for p in picked if p.source == "deep.example") == 5
    assert sum(1 for p in picked if p.source == "komanda.lv") == 5


def test_pick_diverse_should_still_take_the_cheapest_row_of_each_shop_first():
    # Round-robin spreads the rows; within one shop it must still be its cheapest.
    deep = [_boot(f"Deep {i}", 40.0 + i * 10, "deep.example") for i in range(4)]
    local = [_boot("Local", 55.0, "komanda.lv")]

    picked = pick_diverse([*deep, *local], TABLE, limit=3, per_source=3)

    assert [p.title for p in picked if p.source == "deep.example"] == ["Deep 0", "Deep 1"]


def test_source_coverage_should_count_the_rows_each_shop_contributed():
    products = [
        _boot("A Elite", 40.0, "prodirectsport.ie"),
        _boot("B Elite", 50.0, "prodirectsport.ie"),
        _boot("C Elite", 90.0, "komanda.lv"),
    ]

    coverage = source_coverage(products, TABLE)

    assert [(c.source, c.label, c.count, c.cheapest) for c in coverage] == [
        ("prodirectsport.ie", "Pro:Direct", 2, 47.0),
        ("komanda.lv", "komanda", 1, 90.0),
    ]


def test_source_coverage_should_report_how_many_candidates_a_shop_offered():
    # Six rows from one shop reads as a ranking bug until the table shows that shop had
    # fifteen candidates and the next one had two.
    picked = [_boot("A Elite", 40.0, "prodirectsport.ie")]
    pool = [_boot(f"Elite {i}", 40.0 + i, "prodirectsport.ie") for i in range(15)]

    [row] = source_coverage(picked, TABLE, pool=pool)

    assert row.count == 1
    assert row.found == 15


def test_source_coverage_should_report_a_configured_shop_that_contributed_nothing():
    # A shop goes quiet because its parser broke far more often than because it sold out,
    # and a list that merely lacks the row cannot say which happened.
    products = [_boot("A Elite", 40.0, "prodirectsport.ie")]

    coverage = source_coverage(products, TABLE, expected=["prodirectsport.ie", "komanda.lv"])

    assert [(c.source, c.count) for c in coverage] == [("prodirectsport.ie", 1), ("komanda.lv", 0)]


def test_source_coverage_should_label_a_shop_config_does_not_know():
    [row] = source_coverage([_boot("A Elite", 40.0, "unknown-shop.com")], TABLE)

    assert row.label == "unknown-shop.com"


def test_expected_sources_should_read_the_hosts_the_hunt_is_configured_to_poll():
    hunt = Hunt(
        id="boots",
        watch=(
            "https://www.prodirectsport.ie/collections/a/products.json",
            "https://www.prodirectsport.ie/collections/b/products.json",
            "https://komanda.lv/collections/c/products.json",
        ),
        catalogs=({"sitemap": "https://teamsport.lv/s.xml", "origin": "https://teamsport.lv"},),
    )

    assert expected_sources(hunt, TABLE) == ["prodirectsport.ie", "komanda.lv", "teamsport.lv"]


def test_expected_sources_should_skip_a_shop_config_states_no_delivery_terms_for():
    # A watch list keeps URLs for shops that have since been blocked, and reporting those
    # as "gone quiet" every run would train the reader to ignore the line that matters.
    hunt = Hunt(id="boots", watch=("https://www.sportsdirect.lv/football", "https://komanda.lv/c"))

    assert expected_sources(hunt, TABLE) == ["komanda.lv"]


def test_should_stamp_the_house_brand_of_a_single_brand_shop():
    # teamsport.lv is Nike's Latvian distributor and lists "ZM SUPERFLY 10 ELITE SG-PRO",
    # with no "Nike" anywhere — under brands_only that rejected the entire storefront.
    [boot] = stamp_house_brands([_boot("ZM SUPERFLY 10 ELITE SG-PRO", 125.0, "teamsport.lv")], TABLE)
    assert boot.brand == "Nike"
    assert boot.title == "ZM SUPERFLY 10 ELITE SG-PRO"  # the displayed name is untouched


def test_should_not_overwrite_a_brand_the_product_already_states():
    [boot] = stamp_house_brands(
        [_boot("Nike Phantom Elite", 125.0, "teamsport.lv", brand="adidas")], TABLE
    )
    assert boot.brand == "adidas"


def test_should_not_stamp_a_shop_with_no_declared_house_brand():
    [boot] = stamp_house_brands([_boot("Some Elite", 60.0, "prodirectsport.ie")], TABLE)
    assert boot.brand == ""


BRAND_HUNT = Hunt(
    id="boots",
    sizes=("37", "37.5"),
    sizes_by_brand={"adidas": ("37.33",), "nike": ("37.5",)},
)


def test_should_use_the_brand_specific_size_list():
    # adidas EU 37 is printed 37 1/3; Nike's equivalent for the same foot is 37.5 and Nike
    # makes no thirds. One flat list is wrong for both brands.
    adi = _boot("adidas Predator Elite FG", 90.0, "x", sizes=("37.33", "37.5"),
                sizes_known=True, brand="adidas")
    nik = _boot("Nike Phantom Elite FG", 90.0, "x", sizes=("37.33", "37.5"),
                sizes_known=True, brand="Nike")
    assert matched_sizes(adi, BRAND_HUNT) == ["37.33"]
    assert matched_sizes(nik, BRAND_HUNT) == ["37.5"]


def test_should_reject_a_boot_only_in_the_other_brands_size():
    # An adidas boot in 37.5 is not the owner's size, however close the number looks.
    adi = _boot("adidas F50 Elite", 90.0, "x", sizes=("37.5",), sizes_known=True,
                brand="adidas")
    assert matched_sizes(adi, BRAND_HUNT) == []


def test_should_read_the_brand_from_the_title_when_the_field_is_empty():
    boot = _boot("adidas Predator Elite FG", 90.0, "x", sizes=("37.33",), sizes_known=True)
    assert matched_sizes(boot, BRAND_HUNT) == ["37.33"]


def test_should_fall_back_to_the_default_sizes_for_an_unlisted_brand():
    boot = _boot("Mizuno Morelia Neo", 90.0, "x", sizes=("37", "37.33"), sizes_known=True,
                 brand="Mizuno")
    assert matched_sizes(boot, BRAND_HUNT) == ["37"]

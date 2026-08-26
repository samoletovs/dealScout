"""Unit tests for shortlist ranking (pure — no network)."""

from __future__ import annotations

from dealscout.models import Hunt, Product
from dealscout.shortlist import (
    Delivery,
    delivery_for,
    landed_cost,
    matched_sizes,
    pick_diverse,
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

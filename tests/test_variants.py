"""Unit tests for reading per-size stock out of an embedded variant payload."""

from __future__ import annotations

from dealscout.variants import (
    SizeStock,
    extract_size_stock,
    find_variant_arrays,
    in_stock,
    read_magento_swatch,
    read_select_options,
    read_size_boxes,
    read_variants,
)
from dealscout.sizeconvert import us_to_eu


# The shape teamsport.lv (Magento 2, Nike's Latvian distributor) renders: a `US izmēri`
# label beside the swatch, and a `jsonConfig` blob whose size options each carry a
# `products` array — empty when that size is unavailable, non-empty when it can be bought.
# The labels are US numbers with a European decimal comma ("10,5"). This is faithful to
# the live page, trimmed to the fields the reader reads.
def _teamsport_page(options: str, system_label: str = "US izmēri") -> str:
    return (
        '<div class="size-additional-wrapper">'
        f'<div class="size-additional-info">{system_label}</div></div>'
        '<script type="text/x-magento-init">{"[data-role=swatch-options]":'
        '{"Magento_Swatches/js/swatch-renderer":{"jsonConfig":'
        '{"attributes":{"155":{"id":"155","code":"size","label":"Izm\\u0113rs",'
        f'"options":[{options}]}}}}}}}}}}}}</script>'
    )


# US 5 == EU 37.5 (the owner's son's Nike size), US 9 == EU 42.5, US 12 == EU 46.
# 8 (== EU 41) is offered but its products array is empty, so it is out of stock.
_TEAMSPORT_OPTIONS = (
    '{"id":"1","label":"5","products":["111"]},'
    '{"id":"2","label":"8","products":[]},'
    '{"id":"3","label":"9","products":["222"]},'
    '{"id":"4","label":"10,5","products":["333"]},'
    '{"id":"5","label":"12","products":["444"]}'
)


def test_should_read_us_swatch_stock_and_convert_to_eu():
    stock = read_magento_swatch(_teamsport_page(_TEAMSPORT_OPTIONS))
    assert stock.known is True
    # US {5,9,10.5,12} in stock -> EU {37.5,42.5,44.5,46}; US 8 (EU 41) is offered but
    # its products array is empty, so it must NOT appear as available.
    assert stock.sizes == frozenset({"37.5", "42.5", "44.5", "46"})
    assert "41" not in stock.sizes


def test_should_surface_the_owners_son_size_when_teamsport_stocks_it():
    # The behaviour that matters end-to-end: a boot teamsport has in US 5 must be reported
    # in stock as EU 37.5, because that is the size the hunt is written in.
    page = _teamsport_page('{"id":"1","label":"5","products":["111"]}')
    assert extract_size_stock(page).sizes == frozenset({"37.5"})


def test_should_refuse_to_read_the_swatch_when_no_size_system_is_declared():
    # Without the `US izmēri` label the numbers could be US or UK, which differ by ~a full
    # size. Reading them anyway would be the confident wrong answer the engine forbids, so
    # the sizes stay unknown rather than being taken as either system.
    page = _teamsport_page(_TEAMSPORT_OPTIONS, system_label="Izmēri")
    assert read_magento_swatch(page).known is False
    assert extract_size_stock(page).known is False


def test_should_refuse_a_youth_ladder_rather_than_read_it_on_the_mens_table():
    # teamsport prints the SAME `US izmēri` label over youth sizes (a trailing `Y`), but
    # youth US and men's US are different ladders. The reader must not place a youth boot on
    # the men's table: youth `5,5Y` is really ~EU 37.5 (the son's size), while the men's
    # `5,5` this table knows is EU 38 — so a men's reading would be wrong by a rung at
    # exactly the size that matters. The whole swatch is refused (unknown), not partly read.
    mens_twin_eu = us_to_eu("5.5", "nike")  # what the men's ladder WOULD say for a bare 5.5
    page = _teamsport_page(
        '{"id":"1","label":"3,5Y","products":[]},'
        '{"id":"2","label":"5,5Y","products":["222"]},'
        '{"id":"3","label":"6,5Y","products":[]}'
    )
    stock = read_magento_swatch(page)
    assert stock.known is False
    # It must not have leaked the men's-twin EU size for the youth label it saw in stock.
    assert mens_twin_eu not in stock.sizes
    assert extract_size_stock(page).known is False


def test_should_refuse_a_mixed_swatch_if_any_size_is_youth_marked():
    # A defensive case: even a swatch that mixes a placeable men's size with a youth one is
    # refused wholesale, so no men's sizes are surfaced from a page that is really a youth
    # boot. Placing the men's ones and dropping the youth ones would still mislabel the boot.
    page = _teamsport_page(
        '{"id":"1","label":"9","products":["222"]},'
        '{"id":"2","label":"5,5Y","products":["333"]}'
    )
    assert read_magento_swatch(page).known is False


def test_should_drop_a_us_size_outside_the_recorded_ladder_rather_than_guess():
    # A label the Nike ladder does not contain (99) is dropped, not mapped to a nearest EU
    # size — but the sizes it can place are still read.
    page = _teamsport_page(
        '{"id":"1","label":"9","products":["222"]},'
        '{"id":"2","label":"99","products":["999"]}'
    )
    stock = read_magento_swatch(page)
    assert stock.sizes == frozenset({"42.5"})


# The shape futbola-apavi.lv (OpenCart, vs-design theme) renders: a radio "box" per size,
# with the quantity on the input and the size as the label's own text.
SIZE_BOX_HTML = """
<div class="dimensions">
  <label class="size-box " for="o-1"><input class="size-radio" data-qty="50"><p>37</p></label>
  <label class="size-box " for="o-2"><input class="size-radio" data-qty="3"><p>37,5</p></label>
  <label class="size-box " for="o-3"><input class="size-radio" data-qty="1"><p>41 1/3</p></label>
  <label class="size-box " for="o-4"><input class="size-radio" data-qty="0"><p>44</p></label>
</div>
"""


def test_should_read_sizes_from_radio_size_boxes():
    stock = read_size_boxes(SIZE_BOX_HTML)
    assert stock.known is True
    # 44 has data-qty="0" and is not buyable; 41 1/3 is a third size and must survive.
    assert stock.sizes == frozenset({"37", "37.5", "41.33"})


def test_should_treat_a_zero_quantity_size_box_as_unavailable():
    stock = read_size_boxes(
        '<label class="size-box"><input data-qty="0"><p>37</p></label>'
        '<label class="size-box"><input data-qty="0"><p>38</p></label>'
    )
    # Stated, and none buyable — which is not the same as "we could not read the sizes".
    assert stock.known is True
    assert stock.sizes == frozenset()


def test_should_ignore_size_boxes_that_are_not_eu_sizes():
    # A colour or quantity picker reusing the class must not be read as a size table.
    assert read_size_boxes(
        '<label class="size-box"><input data-qty="5"><p>Red</p></label>'
    ).known is False


def test_extract_size_stock_should_fall_back_to_size_boxes():
    # No JSON payload and no <select> — the boxes are the only statement of stock.
    stock = extract_size_stock(SIZE_BOX_HTML)
    assert stock.known is True
    assert "37" in stock.sizes


# The shape unisportstore.com embeds, escaped, in its product pages.
UNISPORT_ROWS = [
    {"name": "EU 36/UK 3½", "availability": "in stock", "price": "44.95",
     "recommended_retail_price": "119.95"},
    {"name": "EU 37/UK 4", "availability": "out of stock", "price": "44.95",
     "recommended_retail_price": "119.95"},
    {"name": "EU 37.5/UK 4½", "availability": "in stock", "price": "44.95",
     "recommended_retail_price": "119.95"},
]


def _page(payload: str) -> str:
    """Wrap an escaped JSON payload the way a page embeds it."""
    return '<html><body><script>var d = "{' + payload + '}";</script></body></html>'


def test_should_read_only_the_sizes_that_are_actually_buyable():
    stock = read_variants(UNISPORT_ROWS)
    assert stock.known is True
    assert stock.sizes == frozenset({"36", "37.5"})


def test_should_read_the_rrp_the_listing_page_never_stated():
    assert read_variants(UNISPORT_ROWS).reference_price == 119.95


def test_should_ignore_an_rrp_that_is_not_above_the_selling_price():
    rows = [{"name": "EU 38", "availability": "in stock", "price": "90", "rrp": "90"}]
    assert read_variants(rows).reference_price is None


def test_should_report_nothing_known_when_no_row_mentions_stock():
    # A size list with no availability is not a stock table; trusting it invents stock.
    rows = [{"name": "EU 37"}, {"name": "EU 38"}]
    assert read_variants(rows) == SizeStock()


def test_should_report_nothing_known_for_an_array_that_is_not_sizes():
    rows = [{"name": "Outlet", "availability": "in stock"}]
    assert read_variants(rows).known is False


def test_should_treat_a_uk_size_table_as_unknown_rather_than_absent():
    # Bare UK sizes normalise fine but mean EU 36-38. Reading them as EU would reject
    # every boot that actually fits.
    rows = [
        {"name": "4", "availability": "in stock"},
        {"name": "4.5", "availability": "in stock"},
        {"name": "5", "availability": "out of stock"},
    ]
    assert read_variants(rows) == SizeStock()


def test_should_accept_a_boolean_stock_flag():
    rows = [
        {"size": "37", "in_stock": True},
        {"size": "38", "in_stock": False},
    ]
    assert read_variants(rows).sizes == frozenset({"37"})


def test_should_accept_a_numeric_quantity_as_stock():
    rows = [{"size": "37", "quantity": 3}, {"size": "38", "quantity": 0}]
    assert read_variants(rows).sizes == frozenset({"37"})


def test_in_stock_should_read_the_common_availability_wordings():
    assert in_stock("http://schema.org/InStock") is True
    assert in_stock("Out of stock") is False
    assert in_stock("sold out") is False
    assert in_stock("Only one left") is None  # says nothing about being buyable
    assert in_stock("") is None


def test_find_variant_arrays_should_parse_an_escaped_embedded_payload():
    page = _page('\\"stock\\":[{\\"name\\":\\"EU 37\\",\\"availability\\":\\"in stock\\"}]')
    [rows] = find_variant_arrays(page.replace('\\"', '"'))
    assert rows == [{"name": "EU 37", "availability": "in stock"}]


def test_find_variant_arrays_should_survive_a_nested_object_inside_a_variant():
    payload = (
        '"stock":[{"name":"EU 37","availability":"in stock",'
        '"delivery":{"min_days":5,"max_days":7}}]'
    )
    [rows] = find_variant_arrays("{" + payload + "}")
    assert rows[0]["delivery"]["max_days"] == 7


def test_find_variant_arrays_should_ignore_an_unterminated_array():
    assert find_variant_arrays('"stock":[{"name":"EU 37"') == []


def test_extract_size_stock_should_read_a_real_escaped_page():
    payload = (
        '\\"stock\\":[' 
        '{\\"name\\":\\"EU 36/UK 3½\\",\\"availability\\":\\"in stock\\",'
        '\\"price\\":\\"44.95\\",\\"recommended_retail_price\\":\\"119.95\\"},'
        '{\\"name\\":\\"EU 37/UK 4\\",\\"availability\\":\\"out of stock\\",'
        '\\"price\\":\\"44.95\\",\\"recommended_retail_price\\":\\"119.95\\"}]'
    )
    stock = extract_size_stock(_page(payload))
    assert stock.known is True
    assert stock.sizes == frozenset({"36"})
    assert stock.reference_price == 119.95


def test_extract_size_stock_should_be_empty_for_a_page_with_no_payload():
    assert extract_size_stock("<html><body>no sizes here</body></html>").is_empty is True


def test_extract_size_stock_should_prefer_the_richest_size_table():
    page = (
        '{"options":[{"size":"37","availability":"in stock"}],'
        '"stock":[{"size":"37","availability":"in stock"},'
        '{"size":"38","availability":"in stock"}]}'
    )
    assert extract_size_stock(page).sizes == frozenset({"37", "38"})


# Sports Direct publishes no per-size JSON at all — the dropdown is the only statement
# of what is buyable. Sizes are dual notation, "UK (EU)".
_SELECT_PAGE = """
<html><body>
<select id="sizeDdl" class="SizeDropDown">
  <option value="0">L&#x16B;dzu, izv&#x113;lieties</option>
  <option value="3.5 (36)" class="greyOut" title="nav pieejams" data-stock-qty="0"> 3.5 (36) </option>
  <option value="4.5 (37.5)" class="greyOut" title="nav pieejams" data-stock-qty="0"> 4.5 (37.5) </option>
  <option value="6 (39)" class="" title="select" data-stock-qty="15"> 6 (39) </option>
</select>
</body></html>
"""


def test_should_read_a_size_dropdown_when_the_page_has_no_json_payload():
    stock = extract_size_stock(_SELECT_PAGE)
    assert stock.known is True
    assert stock.sizes == frozenset({"39"})


def test_should_read_the_eu_half_of_a_uk_eu_dual_size_label():
    # "4.5 (37.5)" is UK 4.5 / EU 37.5 — the hunt is written in EU.
    assert read_select_options(_SELECT_PAGE).known is True
    assert "37.5" not in read_select_options(_SELECT_PAGE).sizes  # greyed out, not buyable


def test_should_treat_a_greyed_out_option_as_out_of_stock():
    assert read_select_options(_SELECT_PAGE).sizes == frozenset({"39"})


def test_should_treat_a_disabled_option_as_out_of_stock():
    page = (
        '<select><option value="37" disabled>37</option>'
        '<option value="38">38</option></select>'
    )
    assert read_select_options(page).sizes == frozenset({"38"})


def test_should_ignore_a_quantity_dropdown():
    # 1-5 are not EU sizes; reading them as sizes would invent stock.
    page = '<select id="qty"><option value="1">1</option><option value="2">2</option></select>'
    assert read_select_options(page).known is False


def test_should_prefer_a_json_payload_over_a_dropdown_when_both_exist():
    page = '{"stock":[{"size":"37","availability":"in stock"}]}' + _SELECT_PAGE
    assert extract_size_stock(page).sizes == frozenset({"37"})

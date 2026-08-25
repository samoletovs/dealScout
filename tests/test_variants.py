"""Unit tests for reading per-size stock out of an embedded variant payload."""

from __future__ import annotations

from dealscout.variants import (
    SizeStock,
    extract_size_stock,
    find_variant_arrays,
    in_stock,
    read_select_options,
    read_variants,
)

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

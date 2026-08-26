"""Unit tests for email rendering helpers (dealscout.notify)."""

from __future__ import annotations

from dealscout.models import Hunt, Product, Verdict
from dealscout.notify import feedback_base_url, markdown_to_html, render_report, render_shortlist
from dealscout.shortlist import Delivery, SourceCoverage


def _signal(url: str = "https://shop.example.com/p", source: str = "Test Store") -> tuple[Product, Verdict]:
    product = Product(
        title="BOSS wool crew",
        category="knitwear",
        price=45.0,
        reference_price=150.0,
        currency="EUR",
        url=url,
        materials={"wool": 1.0},
        brand="BOSS",
        source=source,
    )
    return product, Verdict(True, 42.0, ("70% off", "€45 → must-buy"), "must-buy")


def test_markdown_to_html_makes_feedback_links_clickable():
    body = render_report([_signal()], feedback_base_url="https://courier.example.com/api/feedback")

    html = markdown_to_html(body)

    assert html is not None
    assert '<a href="https://courier.example.com/api/feedback' in html
    assert "👍" in html and "👎" in html
    assert html.lstrip().startswith("<!DOCTYPE html>")


def test_feedback_base_url_derives_from_courier_url(monkeypatch):
    monkeypatch.setenv("COURIER_URL", "https://c.example.com/api/send")

    assert feedback_base_url() == "https://c.example.com/api/feedback"


def test_render_report_links_title_and_omits_raw_url():
    body = render_report([_signal(url="https://shop.example.com/boss")])

    # title is a clickable link to the product; no bare URL line hogging space
    assert "[BOSS wool crew — €45](https://shop.example.com/boss)" in body
    assert "\n- https://shop.example.com/boss" not in body


def test_render_report_shows_discount_when_reference_price_present():
    body = render_report([_signal(url="https://shop.example.com/boss")])

    # _signal has reference_price 150 and price 45 -> 70% off
    assert "was €150 (-70%)" in body


def test_render_report_groups_by_store_most_first():
    a = _signal(url="https://s/1", source="Zalando")
    b = _signal(url="https://s/2", source="Zalando")
    c = _signal(url="https://s/3", source="About You")

    body = render_report([a, c, b])

    assert "## Zalando — 2 deal(s)" in body
    assert "## About You — 1 deal(s)" in body
    assert body.index("Zalando") < body.index("About You")  # store with more deals first
    assert " · new" in body  # everything is labelled new


def test_render_report_prioritizes_and_labels_brand_stores():
    boss = _signal(url="https://s/1", source="Hugo Boss")  # brand's own shop (items are BOSS)
    zal_a = _signal(url="https://s/2", source="Zalando")
    zal_b = _signal(url="https://s/3", source="Zalando")

    body = render_report([zal_a, zal_b, boss])

    assert "## Hugo Boss — 1 deal(s) (brand store)" in body
    # brand store leads even though Zalando has more deals
    assert body.index("Hugo Boss") < body.index("Zalando")


def test_markdown_to_html_renders_headings_and_bullets():
    html = markdown_to_html("# Title\n\n- one\n- two\n")

    assert "<h1>Title</h1>" in html
    assert "<li>one</li>" in html


def test_markdown_to_html_renders_a_pipe_table():
    # Tables are off by default in Python-Markdown, so the per-source breakdown would
    # otherwise reach the reader as a paragraph of literal pipe characters.
    html = markdown_to_html("| Source | Rows |\n|---|---:|\n| Pro:Direct | 6 |\n")

    assert "<table>" in html
    assert "<td>Pro:Direct</td>" in html


SHORTLIST_HUNT = Hunt(id="boots", label="Boots", sizes=("37",))
SHORTLIST_TABLE = {
    "prodirectsport.ie": Delivery(label="Pro:Direct (IE)", shipping=7.0),
    "komanda.lv": Delivery(label="komanda.lv (Rīga)", shipping=0.0),
}


def _boot(title: str, price: float, source: str) -> Product:
    return Product(
        title=title,
        category="football_boots",
        price=price,
        reference_price=None,
        currency="EUR",
        url=f"https://{source}/{title}",
        source=source,
        sizes=frozenset({"37"}),
        sizes_known=True,
    )


def test_render_shortlist_states_how_many_rows_each_source_contributed():
    # Diversity nobody can see is indistinguishable from none, and the "found" column is
    # what tells the reader six rows from one shop is the catalogue talking, not the ranker.
    coverage = [
        SourceCoverage("prodirectsport.ie", "Pro:Direct (IE)", count=6, cheapest=62.0, found=15),
        SourceCoverage("komanda.lv", "komanda.lv (Rīga)", count=2, cheapest=212.0, found=2),
    ]

    body = render_shortlist(
        SHORTLIST_HUNT,
        [_boot("A Elite", 40.0, "prodirectsport.ie")],
        [],
        SHORTLIST_TABLE,
        coverage=coverage,
    )

    assert "### Where these came from" in body
    assert "| Pro:Direct (IE) | 6 | €62 | 15 |" in body
    assert "| komanda.lv (Rīga) | 2 | €212 | 2 |" in body


def test_render_shortlist_names_a_source_that_contributed_nothing():
    # A retailer goes quiet because its parser broke far more often than because it sold
    # out, and a list that merely lacks the row cannot say which happened.
    coverage = [
        SourceCoverage("prodirectsport.ie", "Pro:Direct (IE)", count=6, cheapest=62.0, found=15),
        SourceCoverage("futbola-apavi.lv", "futbola-apavi.lv", count=0),
    ]

    body = render_shortlist(
        SHORTLIST_HUNT,
        [_boot("A Elite", 40.0, "prodirectsport.ie")],
        [],
        SHORTLIST_TABLE,
        coverage=coverage,
    )

    assert "Nothing from futbola-apavi.lv this run" in body
    assert "| futbola-apavi.lv | 0 |" not in body  # a silent shop is a sentence, not a table row


def test_render_shortlist_omits_the_breakdown_when_there_is_no_coverage():
    body = render_shortlist(SHORTLIST_HUNT, [], [], SHORTLIST_TABLE)

    assert "Where these came from" not in body

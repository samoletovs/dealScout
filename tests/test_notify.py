"""Unit tests for email rendering helpers (dealscout.notify)."""

from __future__ import annotations

from dealscout.models import Product, Verdict
from dealscout.notify import feedback_base_url, markdown_to_html, render_report


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

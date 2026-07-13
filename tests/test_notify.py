"""Unit tests for email rendering helpers (dealscout.notify)."""

from __future__ import annotations

from dealscout.models import Product, Verdict
from dealscout.notify import markdown_to_html, render_report


def _signal(url: str = "https://shop.example.com/p") -> tuple[Product, Verdict]:
    product = Product(
        title="BOSS wool crew",
        category="knitwear",
        price=45.0,
        reference_price=150.0,
        currency="EUR",
        url=url,
        materials={"wool": 1.0},
    )
    return product, Verdict(True, 42.0, ("70% off", "€45 → must-buy"), "must-buy")


def test_markdown_to_html_makes_feedback_links_clickable():
    body = render_report([_signal()], feedback_address="deals@example.com")

    html = markdown_to_html(body)

    assert html is not None
    assert '<a href="mailto:deals@example.com' in html
    assert "👍 keep" in html and "👎 skip" in html
    assert html.lstrip().startswith("<!DOCTYPE html>")


def test_markdown_to_html_renders_headings_and_bullets():
    html = markdown_to_html("# Title\n\n- one\n- two\n")

    assert "<h1>Title</h1>" in html
    assert "<li>one</li>" in html

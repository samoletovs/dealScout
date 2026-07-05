"""Unit tests for the digest composer."""

from __future__ import annotations

from dealscout.digest import compose_digest, render_senders
from dealscout.models import SaleEvent


def _event(brand: str, disc: float, cats: tuple[str, ...]) -> SaleEvent:
    return SaleEvent(brand, f"{disc:.0f}% off", disc, cats, f"http://{brand}", "x")


def test_digest_groups_by_band_and_drops_skips():
    events = [
        (_event("BOSS", 50.0, ("shirt",)), "must-look"),
        (_event("COS", 30.0, ("knitwear",)), "good"),
        (_event("H&M", 70.0, ("tee",)), "skip"),
    ]
    out = compose_digest(events)
    assert "Must-look" in out and "BOSS" in out
    assert "Good offers" in out and "COS" in out
    assert "H&M" not in out  # skipped brands never appear


def test_digest_is_friendly_when_empty():
    assert "nothing worth" in compose_digest([]).lower()


def test_render_senders_lists_domains_with_counts():
    out = render_senders([("hugoboss.com", 3), ("cos.com", 1)])
    assert "hugoboss.com (3)" in out
    assert "cos.com (1)" in out


def test_render_senders_warns_when_no_newsletters():
    assert "check your subscriptions" in render_senders([]).lower()

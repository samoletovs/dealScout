"""The RRP memory: remember a list price across runs, never stock.

Invariants, not counts:
  * a remembered RRP fills a gap but never overrides a live one;
  * only a page-stated RRP is learned, so a cache-filled value cannot refresh its own age;
  * an entry past keep_days is dropped, so a stale RRP cannot live forever;
  * stock is never remembered.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dealscout import rrpcache
from dealscout.models import Product


def _boot(url: str, price: float = 90.0, reference_price: float | None = None) -> Product:
    return Product(
        title="adidas Predator Elite FG",
        category="football_boots",
        price=price,
        reference_price=reference_price,
        currency="EUR",
        url=url,
        source="teamsport.lv",
        brand="adidas",
    )


def test_a_remembered_rrp_fills_a_missing_one():
    boot = _boot("https://a/1", reference_price=None)
    filled = rrpcache.apply([boot], {"https://a/1": 130.0})
    assert filled[0].reference_price == 130.0


def test_a_live_rrp_always_beats_the_cache():
    """A shop correcting its own list price must be believed over yesterday's memory."""
    boot = _boot("https://a/1", reference_price=250.0)
    filled = rrpcache.apply([boot], {"https://a/1": 130.0})
    assert filled[0].reference_price == 250.0, "the page's own RRP wins"


def test_a_relearned_cached_rrp_keeps_its_original_age(tmp_path):
    """The expiry invariant: re-learning a value already in the cache must NOT reset its
    age, or a stale RRP filled from the cache every run would live forever. `save` keeps
    each known URL's original stamp, so an entry near expiry still expires on schedule.
    """
    path = tmp_path / "rrp.json"
    near_expiry = (datetime.now(UTC) - timedelta(days=179)).isoformat()
    # The value is already remembered with an old timestamp...
    rrpcache.save({"https://a/1": 130.0}, stamps={"https://a/1": near_expiry},
                  keep_days=180, path=path)
    stamps = rrpcache.load_stamps(path)
    memory = rrpcache.load(path)
    # ...a later run fills it from the cache and "re-learns" the same value...
    relearned = rrpcache.learn([_boot("https://a/1", reference_price=130.0)], memory)
    # ...and on save its original age is preserved, so two days on it has expired.
    rrpcache.save(relearned, stamps=stamps, keep_days=180, path=path,
                  now=datetime.now(UTC) + timedelta(days=2))
    assert rrpcache.load(path) == {}, "the age was preserved, so the entry expired on time"


def test_a_page_stated_rrp_is_learned():
    from_page = _boot("https://a/1", reference_price=130.0)
    still_missing = _boot("https://a/2", reference_price=None)
    learned = rrpcache.learn([from_page, still_missing], {})
    assert learned == {"https://a/1": 130.0}, "only a boot with an RRP is remembered"


def test_stock_is_never_remembered():
    """The one thing that must not be cached. `learn` returns prices only, no sizes."""
    boot = _boot("https://a/1", reference_price=130.0)
    learned = rrpcache.learn([boot], {})
    assert learned == {"https://a/1": 130.0}
    assert all(not isinstance(v, (set, frozenset)) for v in learned.values())


def test_an_expired_entry_is_dropped_on_save(tmp_path):
    """Stale-knowledge rot: an RRP older than keep_days must not survive a save."""
    path = tmp_path / "rrp.json"
    old = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    fresh = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    rrpcache.save(
        {"https://old/1": 100.0, "https://new/1": 120.0},
        stamps={"https://old/1": old, "https://new/1": fresh},
        keep_days=180,
        path=path,
    )
    reloaded = rrpcache.load(path)
    assert reloaded == {"https://new/1": 120.0}, "the 400-day-old entry is gone"


def test_a_missing_or_corrupt_cache_means_no_memory(tmp_path):
    assert rrpcache.load(tmp_path / "absent.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert rrpcache.load(bad) == {}


def test_a_round_trip_survives_save_and_load(tmp_path):
    path = tmp_path / "rrp.json"
    rrpcache.save({"https://a/1": 130.0}, keep_days=180, path=path)
    assert rrpcache.load(path) == {"https://a/1": 130.0}

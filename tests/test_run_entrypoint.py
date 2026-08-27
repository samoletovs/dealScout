"Unit tests for the wardrobe watcher entrypoint (dealscout.run)."

from __future__ import annotations


# --- a run that reads nothing has not succeeded ---------------------------------------


def test_a_watch_list_where_nothing_loads_should_fail_the_run(monkeypatch, tmp_path):
    """This ran daily for months against `https://www.example.com/product/123`.

    Two 404s, an empty report, and a green tick — because "nothing was a deal" and
    "nothing could be read" produced exactly the same silence.
    """
    import dealscout.run as run_module

    async def _nothing_loads(_item, **_kw):
        return None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_module, "collect", _nothing_loads)
    monkeypatch.setattr(
        run_module, "load_config", lambda _p: {"watch": [{"url": "https://x.example/p", "category": "knitwear"}]}
    )

    assert run_module.main() == 1


def test_a_quiet_week_should_still_succeed(monkeypatch, tmp_path):
    """The distinction that matters: pages read fine, nothing was cheap enough."""
    import dealscout.run as run_module
    from dealscout.models import Product

    async def _loads_but_dear(_item, **_kw):
        return Product(
            title="BOSS wool crew",
            category="knitwear",
            price=300.0,
            reference_price=320.0,
            currency="EUR",
            url="https://x.example/p",
            materials={"wool": 1.0},
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_module, "collect", _loads_but_dear)
    monkeypatch.setattr(
        run_module, "load_config", lambda _p: {"watch": [{"url": "https://x.example/p", "category": "knitwear"}]}
    )

    assert run_module.main() == 0


def test_an_empty_watch_list_should_not_be_called_broken(monkeypatch, tmp_path):
    """Nothing to watch is a configuration choice, not a failure."""
    import dealscout.run as run_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_module, "load_config", lambda _p: {"watch": []})

    assert run_module.main() == 0

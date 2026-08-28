"""Unit tests for the daily hunt entrypoint (dealscout.run_hunt)."""

from __future__ import annotations

import pytest

from pathlib import Path

import dealscout.run_hunt as run_hunt


def test_help_should_print_help_rather_than_become_a_hunt_id():
    """`only` was sys.argv[1], so --help was read as a hunt id, matched nothing, and the
    run reported there was nothing to do — the same footgun run_shortlist had."""
    with pytest.raises(SystemExit) as exit_info:
        run_hunt.parse_args(["--help"])

    assert exit_info.value.code == 0


def test_a_mistyped_no_email_flag_should_refuse_rather_than_send():
    with pytest.raises(SystemExit) as exit_info:
        run_hunt.parse_args(["--no-emails"])

    assert exit_info.value.code != 0


def test_a_named_hunt_should_still_be_honoured():
    assert run_hunt.parse_args(["boots-junior"]).hunt == "boots-junior"
    assert run_hunt.parse_args([]).hunt == ""


def test_no_email_should_be_off_by_default_because_sending_is_the_point():
    assert run_hunt.parse_args([]).no_email is False
    assert run_hunt.parse_args(["--no-email"]).no_email is True


def _one_finding(monkeypatch, tmp_path, send_email):
    """Drive run() to exactly one finding, with a controllable send."""
    import asyncio as _asyncio

    from dealscout.models import Product, Verdict

    boot = Product(
        title="adidas Predator Elite FG",
        category="football_boots",
        price=55.0,
        reference_price=130.0,
        currency="EUR",
        url="https://shop.example/boot",
        brand="adidas",
        source="shop.example",
        sizes=frozenset({"37.33"}),
        sizes_known=True,
    )
    hunt = run_hunt.Hunt(id="boots", label="Boots", category="football_boots", sizes=("37.33",))

    async def _scout(*_a, **_kw):
        return [boot]

    async def _no_feedback():
        return []

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_hunt, "load_config", lambda _p: {"hunts": []})
    monkeypatch.setattr(run_hunt, "load_hunts", lambda _c, _o="": [hunt])
    monkeypatch.setattr(run_hunt, "scout", _scout)
    monkeypatch.setattr(run_hunt, "read_feedback", _no_feedback)
    monkeypatch.setattr(run_hunt, "judge_hunt", lambda *_a, **_k: Verdict(True, 60.0, ("cheap",), "must-buy"))
    monkeypatch.setattr(
        run_hunt,
        "classify",
        lambda product, *_a, **_k: run_hunt.Change(product=product, kind="new"),
    )
    monkeypatch.setattr(run_hunt, "send_email", send_email)
    return _asyncio


def test_findings_that_could_not_be_sent_should_fail_the_run(monkeypatch, tmp_path):
    """Sending is the point. For months this found boots and told nobody, exiting 0."""

    async def _refuses(_subject, _body):
        return False

    aio = _one_finding(monkeypatch, tmp_path, _refuses)

    with pytest.raises(run_hunt.EmailNotDelivered):
        aio.run(run_hunt.run(Path("config.example.yaml")))


def test_no_email_should_not_fail_for_not_sending(monkeypatch, tmp_path):
    """Without this, every local run that found something failed on a machine with no
    courier credentials - which is most machines, and none of them are broken."""

    async def _must_not_be_called(_subject, _body):  # pragma: no cover - the point is it isn't
        raise AssertionError("--no-email must not attempt a send")

    aio = _one_finding(monkeypatch, tmp_path, _must_not_be_called)

    aio.run(run_hunt.run(Path("config.example.yaml"), send=False))


def test_the_undelivered_error_should_name_how_much_was_lost():
    error = run_hunt.EmailNotDelivered(7)

    assert error.findings == 7
    assert "7" in str(error)

def test_observe_only_hunt_logs_but_never_emails(monkeypatch, tmp_path):
    """An observe-only hunt feeds the price log for the whole range but must never reach the
    digest. Without the guard in run(), its always-deal boot becomes a finding and forces a
    send — burying the owner's real finds under the range."""
    logged: list = []

    async def _must_not_send(_subject, _body):  # pragma: no cover - the point is it isn't
        raise AssertionError("an observe-only hunt must not attempt a send")

    aio = _one_finding(monkeypatch, tmp_path, _must_not_send)
    # Turn the driver's single hunt into an observation-only one.
    observe_hunt = run_hunt.Hunt(
        id="boots-archive", label="Archive", category="football_boots", observe_only=True
    )
    monkeypatch.setattr(run_hunt, "load_hunts", lambda _c, _o="": [observe_hunt])
    monkeypatch.setattr(run_hunt, "append_dir", lambda obs, _dir: logged.extend(obs))
    monkeypatch.setattr(run_hunt, "rewrite_dir", lambda _hist, _dir: None)

    findings = aio.run(run_hunt.run(Path("config.example.yaml")))

    assert findings["boots-archive"] == [], "observe-only produces no reportable findings"
    assert len(logged) == 1, "but the boot's price is still logged"

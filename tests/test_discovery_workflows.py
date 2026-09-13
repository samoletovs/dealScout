"""Guard who can spend SerpApi credits and where its durable budget is saved."""

from __future__ import annotations

import re
import shlex
from pathlib import Path, PurePosixPath

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
DISCOVERY_WORKFLOWS = ("serpapi.yml", "hunt.yml", "shortlist.yml")
ENTRYPOINTS = {
    "serpapi.yml": "dealscout.run_serpapi",
    "hunt.yml": "dealscout.run_hunt",
    "shortlist.yml": "dealscout.run_shortlist",
}


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _steps(workflow: dict) -> list[dict]:
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def _entrypoint(workflow: dict, name: str) -> dict:
    matching = [
        step for step in _steps(workflow)
        if re.search(rf"\b{re.escape(ENTRYPOINTS[name])}\b", str(step.get("run", "")))
    ]
    assert len(matching) == 1, f"{name} must execute its expected entrypoint exactly once"
    return matching[0]


def _expression(value: object) -> str:
    return str(value).strip().removeprefix("${{").removesuffix("}}").strip()


def test_only_the_weekly_discovery_workflow_should_reference_the_serpapi_key() -> None:
    referencing = [
        path.name
        for path in sorted(WORKFLOWS.iterdir())
        if path.suffix in {".yml", ".yaml"}
        and "SERPAPI_KEY" in yaml.safe_dump(_workflow(path.name))
    ]

    assert referencing == ["serpapi.yml"]
    refresh = _entrypoint(_workflow("serpapi.yml"), "serpapi.yml")
    assert _expression(refresh["env"]["SERPAPI_KEY"]) == "secrets.SERPAPI_KEY"


def test_discovery_should_have_only_one_weekly_scheduled_refresh() -> None:
    workflow = _workflow("serpapi.yml")
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    schedules = triggers["schedule"]
    assert len(schedules) == 1

    minute, hour, day, month, weekday = schedules[0]["cron"].split()

    assert minute.isdigit() and 0 <= int(minute) < 60
    assert hour.isdigit() and 0 <= int(hour) < 24
    assert day == "*" and month == "*"
    assert weekday.lower() in {"0", "1", "2", "3", "4", "5", "6", "7", "sun", "mon", "tue", "wed", "thu", "fri", "sat"}
    assert "workflow_dispatch" in triggers


@pytest.mark.parametrize("name", ["hunt.yml", "shortlist.yml"])
def test_routine_workflows_should_not_execute_a_paid_discovery_entrypoint(name: str) -> None:
    workflow = _workflow(name)
    _entrypoint(workflow, name)
    commands = "\n".join(str(step.get("run", "")) for step in _steps(workflow))

    assert "dealscout.run_serpapi" not in commands
    assert not re.search(r"serpapi\.com/search(?:\.json)?", commands)
    assert "SERPAPI_KEY" not in yaml.safe_dump(workflow)


def test_all_discovery_state_users_should_share_a_non_cancelling_concurrency_group() -> None:
    concurrency = [_workflow(name)["concurrency"] for name in DISCOVERY_WORKFLOWS]
    groups = {block["group"] for block in concurrency}

    assert len(groups) == 1
    group = next(iter(groups))
    assert isinstance(group, str) and group.strip()
    assert "${{" not in group, "A workflow/ref-specific group cannot serialize all state writers"
    assert all(block.get("cancel-in-progress") is False for block in concurrency)


@pytest.mark.parametrize("name", DISCOVERY_WORKFLOWS)
def test_budget_store_checkout_should_fail_closed_and_precede_the_consumer(name: str) -> None:
    workflow = _workflow(name)
    steps = _steps(workflow)
    checkouts = [
        step for step in steps
        if str(step.get("uses", "")).startswith("actions/checkout@")
        and step.get("with", {}).get("ref") == "price-history"
    ]

    assert len(checkouts) == 1
    checkout = checkouts[0]
    assert checkout.get("continue-on-error", False) is False
    assert "if" not in checkout, "Restoring the budget must not be an optional step"
    assert steps.index(checkout) < steps.index(_entrypoint(workflow, name))


def test_all_consumers_should_share_the_same_discovery_file_on_price_history() -> None:
    paths: set[str] = set()
    for name in DISCOVERY_WORKFLOWS:
        workflow = _workflow(name)
        consumer = _entrypoint(workflow, name)
        path = consumer["env"]["DEALSCOUT_DISCOVERY_PATH"]
        checkout = next(
            step for step in _steps(workflow)
            if step.get("with", {}).get("ref") == "price-history"
        )
        branch_root = checkout["with"]["path"]
        relative = PurePosixPath(path).relative_to(PurePosixPath(branch_root))
        assert ".." not in relative.parts
        assert relative.parts[0] == "discovery"
        paths.add(path)

    assert len(paths) == 1


def test_refresh_failure_should_still_publish_reserved_attempts_to_the_shared_branch() -> None:
    workflow = _workflow("serpapi.yml")
    steps = _steps(workflow)
    refresh = _entrypoint(workflow, "serpapi.yml")
    savers = [
        step for step in steps
        if "commit-price-history.sh" in str(step.get("run", ""))
    ]

    assert len(savers) == 1
    save = savers[0]
    assert _expression(save.get("if", "")) == "always()"
    assert save.get("continue-on-error", False) is False
    assert refresh.get("continue-on-error", False) is False
    assert steps.index(save) > steps.index(refresh)
    assert "|| true" not in save["run"] and "|| true" not in refresh["run"]
    checkout = next(step for step in steps if step.get("with", {}).get("ref") == "price-history")
    assert save["env"]["PRICE_HISTORY_DIR"] == checkout["with"]["path"]
    writer_job = next(job for job in workflow["jobs"].values() if save in job.get("steps", []))
    permissions = writer_job.get("permissions", workflow.get("permissions", {}))
    assert permissions.get("contents") == "write"


def test_state_publisher_should_stage_only_prices_and_the_exact_durable_discovery_file() -> None:
    script = (ROOT / ".github" / "scripts" / "commit-price-history.sh").read_text(encoding="utf-8")
    commands = [line.strip() for line in script.splitlines() if not line.lstrip().startswith("#")]
    staging = [line for line in commands if re.match(r"git\s+add\b", line)]
    staged_paths: set[str] = set()
    for command in staging:
        paths = {
            PurePosixPath(argument).as_posix()
            for argument in shlex.split(command, comments=True)[2:]
            if not argument.startswith("-")
        }
        assert paths, "Unscoped git add may publish discovery lock or atomic-write artifacts"
        staged_paths.update(paths)

    assert staged_paths == {"prices", "discovery/state.json"}, (
        "Stage the durable state file explicitly, never the discovery directory, "
        "a wildcard, or the whole checkout: those can publish .lock/.tmp artifacts"
    )
    assert any(re.match(r"git\s+push\b", command.removeprefix("if ").strip()) for command in commands)
    assert not any("checkout --orphan" in command for command in commands)

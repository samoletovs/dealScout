"""The two dependency lists have to agree, because nothing else makes them.

``requirements.txt`` is what every workflow installs; ``pyproject.toml`` is what
``pip install -e .`` and any container build reads. They describe the same thing and
drifted: ``markdown`` was in one and not the other for long enough that a collaborator
lost time to three failing tests on a clean checkout, and diagnosed it as someone else's
in-flight change.

CI could never have caught it. All seven workflows install ``requirements.txt``, the
list that was correct, so the broken one was never exercised.

The consequence is worse than a test failure. ``notify`` degrades quietly when markdown
is absent — ``markdown_to_html`` returns ``None`` and the mail goes out as plain text,
so the owner gets raw markup and, more to the point, 👍/👎 links he cannot click. No
error, no warning, just a worse email. A dependency whose absence is silent is exactly
the one that has to be declared in both places.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9._-]+)")


def _names(specifiers: list[str]) -> set[str]:
    """Bare distribution names, lowercased, from PEP 508 specifier strings."""
    found = set()
    for line in specifiers:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = _REQUIREMENT.match(line)
        if match:
            found.add(match.group(1).lower().replace("_", "-"))
    return found


def _pyproject_dependencies() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return _names(data["project"]["dependencies"])


def _requirements() -> set[str]:
    return _names((ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines())


def test_every_runtime_requirement_should_be_declared_in_pyproject():
    """The direction that actually bit us: installable via pip but missing a package."""
    missing = _requirements() - _pyproject_dependencies()

    assert not missing, (
        f"in requirements.txt but not pyproject.toml: {sorted(missing)}. "
        "An install from pyproject would omit these, and CI installs requirements.txt "
        "so it would not notice."
    )


def test_every_pyproject_dependency_should_be_installed_by_the_workflows():
    """The other direction: declared but never installed, so CI runs without it."""
    missing = _pyproject_dependencies() - _requirements()

    assert not missing, (
        f"in pyproject.toml but not requirements.txt: {sorted(missing)}. "
        "The workflows install requirements.txt, so CI would run without these."
    )


def test_markdown_is_declared_because_its_absence_is_silent():
    """Pinned by name: this is the one whose absence produces a worse email, not an error."""
    assert "markdown" in _pyproject_dependencies()
    assert "markdown" in _requirements()

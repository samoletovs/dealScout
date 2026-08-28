"""Tests for the published join-key manifest.

The manifest exists so a consumer in *another repository* can check its references against
this classifier. That makes two failure modes matter more than usual:

  * a manifest that goes stale, silently publishing keys the catalogue no longer has, and
  * a manifest that accepts a different set of names than the in-repo guard does, letting a
    row pass one check and fail the other.

Both are guarded here.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from dealscout.export_join_keys import build_manifest, _digest

CATALOGUE = Path("data/football_boots.yaml")
MANIFEST = Path("data/broom/join-keys.json")


def _catalogue() -> dict:
    with CATALOGUE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_the_committed_manifest_matches_the_catalogue():
    """The checked-in manifest must be exactly what the catalogue implies today.

    This is the staleness guard. bRoom fetches this file over HTTP and trusts it to say which
    boots exist; if a catalogue edit lands without regenerating it, that trust is misplaced
    and nothing else would notice.
    """
    expected = build_manifest(_catalogue(), source_digest=_digest(CATALOGUE))
    actual = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert actual == expected, (
        "data/broom/join-keys.json is stale — the catalogue has changed since it was "
        "generated. Run: python -m dealscout.export_join_keys"
    )


def test_the_manifest_accepts_the_same_names_the_in_repo_guard_does():
    """The manifest and `tests/test_broom_dataset.py` must agree on what a valid key is.

    They are two implementations of one rule, in two repositories. If the manifest were more
    permissive, a bRoom row could pass its own drift guard and still fail this repo's join
    test; if it were stricter, a legitimate row would be rejected only after migration. Either
    way the split stops being trustworthy, so the two key sets are asserted equal.
    """
    from tests.test_broom_dataset import _catalogue_generation_keys

    in_repo = _catalogue_generation_keys(_catalogue())
    published = {
        (entry["brand"], entry["line"], token)
        for entry in json.loads(MANIFEST.read_text(encoding="utf-8"))["generations"]
        for token in entry["tokens"]
    }

    assert published == in_repo, (
        "The manifest and the in-repo join guard disagree about which keys are valid.\n"
        f"only in manifest: {sorted(published - in_repo)[:5]}\n"
        f"only in guard:    {sorted(in_repo - published)[:5]}"
    )


def test_the_manifest_carries_the_fields_a_consumer_must_not_restate():
    """Every published generation states the fields the catalogue owns.

    bRoom is forbidden from restating year, status or launch RRP. That rule is only
    enforceable if the manifest actually supplies them — otherwise a consumer has no source
    for the value and will be tempted to copy one in.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["last_verified"], "the manifest must carry the catalogue's last_verified"
    assert manifest["generated_from"].startswith("sha256:")

    for entry in manifest["generations"]:
        for field in ("brand", "line", "tokens", "year", "status", "launch_rrp_eur"):
            assert field in entry, f"{entry.get('line')} is missing {field}"
        assert entry["tokens"], f"{entry['line']} publishes no addressable token"


def test_a_generation_with_no_addressable_token_is_not_published():
    """A generation nameable by neither number nor pattern cannot be referenced.

    Publishing it would advertise a key that resolves to nothing, so it is dropped. Asserted
    directly because no real catalogue entry currently exercises it, and a silent behaviour
    change here would only surface as a confusing miss in another repository.
    """
    catalogue = {
        "last_verified": "2026-08",
        "brands": {
            "nike": {
                "lines": {
                    "phantom": {
                        "generations": [
                            {"gen": 6, "year": 2025, "status": "current"},
                            {"year": 1998, "status": "discontinued"},  # unnameable
                        ]
                    }
                }
            }
        },
    }

    built = build_manifest(catalogue, source_digest="sha256:test")

    assert [e["tokens"] for e in built["generations"]] == [["6"]]

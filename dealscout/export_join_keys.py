"""Export the classifier's join keys as a machine-readable manifest.

WHY THIS EXISTS
---------------
Two repositories hold boot facts, split by job rather than by topic. This repo's
``data/football_boots.yaml`` answers *"what tier and generation is this listing?"* and owns
silo, generation, year, launch RRP and status. The bRoom site holds only presentation data —
technology, plate material, weight, street price, players, heritage — and **references** a
generation without restating it.

That split only stays honest if the referencing side can check its references. Inside this
repo a test does exactly that (``tests/test_broom_dataset.py``), but bRoom is a different
repository: it fetches the presentation data over HTTP and has no way to see the catalogue.
So its drift guard can currently check only that the data is well-formed, not that the boots
it names actually exist.

This manifest closes that gap. It publishes, as JSON, every ``(brand, line, generation)`` the
classifier can answer and the ``year`` / ``status`` / ``launch_rrp_eur`` each resolves to —
so a consumer in another repository can fail its own build when it names a boot this
catalogue has never heard of, or restates a field it does not own.

It is also the agreed **migration trigger**: ``data/broom/README.md`` states that the
presentation dataset moves into the bRoom repo only once this manifest exists, so the drift
guard survives the move.

WHAT IT IS NOT
--------------
Not a second home for the facts. It is a *derived* artifact, regenerated from
``football_boots.yaml`` and never edited by hand. If it disagrees with the catalogue, the
catalogue is right and this is stale — which is why it carries ``generated_from``, the digest
of the source file it was built from, so a consumer can tell.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

CATALOGUE = Path("data/football_boots.yaml")
DEFAULT_OUT = Path("data/broom/join-keys.json")


def _tokens_for(gen: dict[str, Any]) -> set[str]:
    """Every token a consumer might legitimately use to name this generation.

    A generation is addressable by its ``gen`` number, by any of its ``patterns``, or by the
    last word of a pattern — because a presentation row keys on the natural name a shop uses
    ("maestro") while the catalogue may store the fuller pattern ("tiempo maestro").

    This mirrors ``_catalogue_generation_keys`` in ``tests/test_broom_dataset.py`` deliberately:
    a manifest that accepted a different set of names than the in-repo guard would let a row
    pass one check and fail the other.
    """
    tokens: set[str] = set()
    if gen.get("gen") is not None:
        tokens.add(str(gen["gen"]))
    for pattern in gen.get("patterns") or []:
        pattern = str(pattern)
        tokens.add(pattern)
        tokens.add(pattern.split()[-1])
    return tokens


def build_manifest(catalogue: dict[str, Any], *, source_digest: str) -> dict[str, Any]:
    """Turn the catalogue into the published join manifest."""
    entries: list[dict[str, Any]] = []
    for brand, bspec in (catalogue.get("brands") or {}).items():
        for line, lspec in (bspec.get("lines") or {}).items():
            for gen in lspec.get("generations") or []:
                tokens = sorted(_tokens_for(gen))
                if not tokens:
                    # A generation with neither a number nor a pattern cannot be referenced,
                    # so publishing it would imply a key that resolves to nothing.
                    continue
                entries.append(
                    {
                        "brand": brand,
                        "line": line,
                        "tokens": tokens,
                        "year": gen.get("year"),
                        "status": gen.get("status"),
                        "launch_rrp_eur": gen.get("launch_rrp_eur"),
                    }
                )

    entries.sort(key=lambda e: (e["brand"], e["line"], e["tokens"][0]))
    return {
        "_note": (
            "Derived from data/football_boots.yaml — never edit by hand. Published so a "
            "consumer in another repository can verify it only names boots this classifier "
            "knows, and does not restate year/status/launch_rrp_eur, which the catalogue "
            "owns. If this disagrees with the catalogue, the catalogue is right."
        ),
        "last_verified": catalogue.get("last_verified"),
        "generated_from": source_digest,
        "generations": entries,
    }


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the file on disk differs from what the catalogue implies, "
        "instead of writing it. This is what CI runs, so a catalogue edit that forgets to "
        "regenerate the manifest fails in review rather than shipping a stale key set.",
    )
    args = parser.parse_args()

    with args.catalogue.open(encoding="utf-8") as fh:
        catalogue = yaml.safe_load(fh)

    manifest = build_manifest(catalogue, source_digest=_digest(args.catalogue))
    rendered = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if not args.out.exists():
            print(f"{args.out} is missing; run: python -m dealscout.export_join_keys")
            return 1
        if args.out.read_text(encoding="utf-8") != rendered:
            print(
                f"{args.out} is stale — the catalogue has changed since it was generated.\n"
                f"Run: python -m dealscout.export_join_keys"
            )
            return 1
        print(f"{args.out} is current ({len(manifest['generations'])} generations)")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.out} — {len(manifest['generations'])} generations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

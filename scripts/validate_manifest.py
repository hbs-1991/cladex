#!/usr/bin/env python3
"""Validate a catalog manifest against the SAME rules the live catalog enforces.

The agent runs this on a draft before filing it: a manifest that fails here would fail
at approval too, so catching it now saves a human's attention. Rules live in
catalog.parse — this script only wraps it, so the two can never drift.

Usage:
    python scripts/validate_manifest.py path/to/manifest.yaml
    cat manifest.yaml | python scripts/validate_manifest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Run from the repo root so the package import resolves without an install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Domain only: this script validates, it never reads or writes the catalog.
from claude_hermes.modules.extensions.domain import drafts  # noqa: E402
from claude_hermes.modules.extensions.domain import manifest as catalog  # noqa: E402
from claude_hermes.modules.extensions.domain.exceptions import (  # noqa: E402
    DraftError, ManifestError)


def check(raw: str) -> tuple[int, str]:
    """(0, describe) if valid, (1, reason) otherwise."""
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        return 1, f"not valid YAML: {e}"
    if not isinstance(data, dict):
        return 1, "a manifest must be a YAML mapping"
    try:
        m = catalog.parse(data, drafts.kind_of(data))
    except (ManifestError, DraftError) as e:
        return 1, str(e)
    return 0, drafts.describe(m)


def main(argv: list[str]) -> int:
    raw = Path(argv[1]).read_text(encoding="utf-8") if len(argv) > 1 else sys.stdin.read()
    code, out = check(raw)
    print("PASS\n" + out if code == 0 else "FAIL: " + out)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

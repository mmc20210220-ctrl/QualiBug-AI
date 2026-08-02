"""SPEC §20 rule-extraction funnel comparison: off (regex-only) vs augment.

Builds the benchmark_mall knowledge asset twice — semantic_rule_extraction_mode
off vs augment (gates confirmed) — and reports the rule funnel at every stage:
regex candidates, LLM candidates, validated, merged/conflicted, promoted,
canonical rule_library rows, and Behavior IR invariants derived from rules.
No ground truth is loaded; this is a comprehension-stage measurement.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load operator LLM credentials from .env.local (never printed).
_env_local = ROOT / ".env.local"
if _env_local.exists():
    for line in _env_local.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from ai_test_asset_center.enterprise_knowledge_center.composition import (  # noqa: E402
    build_enterprise_business_knowledge_asset,
)

PROJECT = "benchmark_mall"


def _rule_stats(asset: dict) -> dict:
    rule_library = [
        row for row in asset.get("rule_library", []) if isinstance(row, dict)
    ]
    augment_rows = [
        row for row in rule_library if row.get("augment_promoted") is True
    ]
    ledgers = asset.get("rule_candidate_ledger", []) or []
    llm_candidates = sum(
        int(row.get("llm_entry_count") or 0) for row in ledgers
    )
    merged = sum(int(row.get("merged_count") or 0) for row in ledgers)
    conflicted = sum(int(row.get("conflicted_count") or 0) for row in ledgers)
    gates = asset.get("rule_promotion_gates") or {}
    promotion_applied = asset.get("rule_promotion_applied") is True
    return {
        "regex_rule_library_rows": len(rule_library) - len(augment_rows),
        "llm_augment_rows": len(augment_rows),
        "total_rule_library_rows": len(rule_library),
        "llm_rule_candidates": llm_candidates,
        "merged_candidates": merged,
        "conflicted_candidates": conflicted,
        "promotion_applied": promotion_applied,
        "promotion_gates_met": bool(gates.get("gates_met")),
        "promotion_checks": gates.get("checks"),
    }


def main() -> None:
    results: dict[str, dict] = {}
    for label, mode, gates in (
        ("off", "off", False),
        ("augment", "augment", True),
    ):
        print(f"--- building asset with mode={mode} ---", flush=True)
        started = time.monotonic()
        asset = build_enterprise_business_knowledge_asset(
            PROJECT,
            root=ROOT,
            options={
                "semantic_rule_extraction_mode": mode,
                "rule_promotion_gates_met": gates,
                "enable_semantic_extraction": True,
            },
        )
        elapsed = time.monotonic() - started
        stats = _rule_stats(asset)
        stats["asset_build_elapsed_sec"] = round(elapsed, 1)
        results[label] = stats
        print(json.dumps(stats, ensure_ascii=False, indent=2), flush=True)

    print("\n=== COMPARISON (off vs augment) ===")
    print(f"{'metric':<28} {'off':>10} {'augment':>10}")
    for key in (
        "regex_rule_library_rows",
        "llm_augment_rows",
        "total_rule_library_rows",
        "llm_rule_candidates",
        "merged_candidates",
        "conflicted_candidates",
        "promotion_applied",
        "promotion_gates_met",
        "asset_build_elapsed_sec",
    ):
        print(
            f"{key:<28} {str(results['off'].get(key)):>10} "
            f"{str(results['augment'].get(key)):>10}"
        )

    out = ROOT / "_funnel_runs"
    out.mkdir(exist_ok=True)
    (out / "rule_extraction_off_vs_augment.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nwritten: {out / 'rule_extraction_off_vs_augment.json'}")


if __name__ == "__main__":
    main()

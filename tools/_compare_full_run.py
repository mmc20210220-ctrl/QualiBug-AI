"""SPEC §20 full-run comparison: off vs augment on benchmark_mall.

Runs the complete scan mainline twice (semantic_rule_extraction_mode off vs
augment with gates confirmed) and reports the discovery funnel at every stage:
rule candidates, promoted rules, IR invariants, obligations, selected
experiments, and findings. Ground truth is never loaded here.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_env_local = ROOT / ".env.local"
if _env_local.exists():
    for line in _env_local.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from ai_test_asset_center.__main__ import scan  # noqa: E402

PROJECT = "benchmark_mall"


def _funnel(result: dict) -> dict:
    mainline = result.get("mainline_run") or {}
    obligations = result.get("obligations") or {}
    experiments = result.get("experiments") or {}
    findings = result.get("findings") or []
    candidates = result.get("candidate_findings") or []
    by_family = obligations.get("by_family") or {}
    return {
        "total_findings": len(findings),
        "candidate_findings": len(candidates),
        "obligation_count": obligations.get("obligation_count"),
        "obligations_by_family": by_family,
        "experiment_count": experiments.get("compiled_count")
        if isinstance(experiments, dict)
        else None,
        "executed_experiments": (
            experiments.get("executed_count")
            if isinstance(experiments, dict)
            else None
        ),
        "rule_candidate_ledger": result.get("rule_candidate_ledger"),
        "rule_promotion_applied": result.get("rule_promotion_applied"),
        "rule_promotion_gates": result.get("rule_promotion_gates"),
        "mainline_authority": mainline.get("mainline_authority"),
        "run_id": mainline.get("run_id"),
    }


def main() -> None:
    results: dict[str, dict] = {}
    for label, mode, gates in (
        ("off", "off", False),
        ("augment", "augment", True),
    ):
        print(f"--- scan mode={mode} ---", flush=True)
        started = time.monotonic()
        result = scan(
            PROJECT,
            root=ROOT,
            base_url="http://localhost:8080",
            campaign_context={
                "semantic_rule_extraction_mode": mode,
                "rule_promotion_gates_met": gates,
                "enable_semantic_extraction": True,
                "target_id": "benchmark_mall_local_scope",
                "scope_id": "benchmark_mall_local_scope",
                "environment_id": "benchmark_mall_test",
                "environment_ref": "benchmark_mall_test",
                "environment_type": "test",
                "approved_base_url": "http://localhost:8080",
                "policy_version": "v1.0.0-baseline",
            },
        )
        elapsed = time.monotonic() - started
        funnel = _funnel(result)
        funnel["elapsed_sec"] = round(elapsed, 1)
        results[label] = funnel
        print(json.dumps(funnel, ensure_ascii=False, indent=2)[:1200], flush=True)
        out = ROOT / "_funnel_runs"
        out.mkdir(exist_ok=True)
        (out / f"scan_{label}.json").write_text(
            json.dumps(result, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    print("\n=== FULL-RUN COMPARISON (off vs augment) ===")
    for key in (
        "obligation_count",
        "experiment_count",
        "executed_experiments",
        "total_findings",
        "candidate_findings",
        "elapsed_sec",
    ):
        print(
            f"{key:<24} off={results['off'].get(key)}  "
            f"augment={results['augment'].get(key)}"
        )
    print("\noff by_family:", json.dumps(results["off"].get("obligations_by_family"), ensure_ascii=False))
    print("augment by_family:", json.dumps(results["augment"].get("obligations_by_family"), ensure_ascii=False))


if __name__ == "__main__":
    main()

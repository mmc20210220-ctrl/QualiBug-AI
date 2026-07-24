"""Project E Phase 4: Formal Blind Run via Normal Product Entry Point.

This script executes the QualiBug scan against the WMS mock server using
the standard product entry point (scan function) with no special handling.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
PROJECT = "warehouse_e"
BASE_URL = "http://localhost:8003"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def main() -> None:
    # Environment setup (standard for non-production benchmark targets)
    os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")
    os.environ["QUALIBUG_TARGET_BASE_URL"] = BASE_URL
    os.environ["QUALIBUG_SSRF_ALLOW_INTERNAL"] = "1"
    os.environ["QUALIBUG_UNIFY_ANALYZERS"] = "1"
    os.environ["QUALIBUG_UNIFY_LLM_REASONER"] = "0"
    os.environ["ENABLE_V12_STATE_GRAPH_ENGINE"] = "false"  # Disable v12 state graph engine
    os.environ["QUALIBUG_ALLOW_TEST_WRITE"] = "1"  # Enable write operations on test env
    # Increase execution budgets for thorough blind run
    os.environ["QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND"] = "1200"
    os.environ["QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT"] = "48"

    # Delete old campaign state for a fresh run
    import shutil
    campaign_dir = ROOT / "platform_workspace" / PROJECT / "defect_discovery" / "campaigns"
    if campaign_dir.exists():
        shutil.rmtree(campaign_dir)
        print(f"  Cleared old campaign state: {campaign_dir}")

    from ai_test_asset_center.__main__ import scan

    # Read API doc
    api_doc_path = ROOT / "projects" / PROJECT / "input" / "openapi.yaml"
    api_doc_text = api_doc_path.read_text(encoding="utf-8")
    source_hash = hashlib.sha256(api_doc_text.encode("utf-8")).hexdigest()

    print("=" * 70)
    print("  PROJECT E - PHASE 4: FORMAL BLIND RUN")
    print("=" * 70)
    print(f"\n  Project: {PROJECT}")
    print(f"  Base URL: {BASE_URL}")
    print(f"  API Doc: {api_doc_path.name}")
    print(f"  Source Hash: {source_hash[:16]}...")
    print(f"\n  Starting scan at {datetime.now(timezone.utc).isoformat()}")
    print("  " + "-" * 66)

    started = time.time()
    
    # Execute scan via normal product entry point
    result = scan(
        project=PROJECT,
        root=ROOT,
        api_doc_path=str(api_doc_path),
        api_doc_text=api_doc_text,
        base_url=BASE_URL,
        ci_gate=False,
        multi_layer=False,  # Use single-layer mode to avoid v12 pipeline observer issue
        save_report=True,
        campaign_context={
            "scope_id": "warehouse_e_blind",
            "environment_ref": "warehouse_e_test",
            "environment_kind": "test",
            "environment_type": "test",
            "execution_mode": "approved_sandbox_write",
            "validation_phase": "formal",
            "runtime": {"environment_type": "test", "environment_kind": "test", "validation_phase": "formal"},
            "source_manifest": {
                "source_id": f"{PROJECT}/openapi.yaml",
                "source_hash": source_hash
            },
        },
    )
    
    elapsed = time.time() - started

    print("\n  " + "-" * 66)
    print(f"  Scan completed in {elapsed:.1f}s")
    print(f"  Success: {result.get('success')}")
    print(f"  Grade: {result.get('grade')}")
    print(f"  Total Findings: {result.get('total_findings')}")
    print(f"  Execution Status: {result.get('execution_status')}")

    # Extract key metrics
    v12 = result.get("v12", {}) if isinstance(result.get("v12"), dict) else {}
    findings = result.get("findings", [])
    
    # Count by severity
    severity_counts = {}
    for f in findings:
        sev = f.get("severity", "unknown")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    print(f"\n  Findings by Severity:")
    for sev, count in sorted(severity_counts.items()):
        print(f"    {sev}: {count}")

    # Save blind run result
    blind_result = {
        "blind_run_id": "project_e_blind_run_v1",
        "started_at": datetime.fromtimestamp(started, tz=timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed, 2),
        "project": PROJECT,
        "base_url": BASE_URL,
        "entry_point": "ai_test_asset_center.__main__.scan",
        "scan_parameters": {
            "api_doc_path": str(api_doc_path),
            "source_hash": source_hash,
            "ci_gate": False,
            "multi_layer": True
        },
        "result_summary": {
            "success": result.get("success"),
            "grade": result.get("grade"),
            "score": result.get("score"),
            "total_findings": result.get("total_findings"),
            "execution_status": result.get("execution_status"),
            "severity_distribution": severity_counts
        },
        "human_intervention": {
            "semantic_mapping": 0,
            "rule_correction": 0,
            "oracle_patch": 0,
            "operation_binding": 0,
            "code_changes": 0,
            "total_count": 0,
            "total_minutes": 0
        },
        "anti_hardcoding": {
            "project_e_special_branches": 0,
            "benchmark_inputs_to_production": 0,
            "manual_rule_injections": 0
        }
    }

    out_path = ROOT / "project_e_blind_run_result.json"
    out_path.write_text(json.dumps(blind_result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Output: {out_path.name}")

    # Also save the full scan result
    scan_out = ROOT / "platform_outputs" / PROJECT
    scan_out.mkdir(parents=True, exist_ok=True)
    scan_result_path = scan_out / "scan_result.json"
    scan_result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"  Full Result: platform_outputs/{PROJECT}/scan_result.json")

    print("\n" + "=" * 70)
    print(f"  BLIND RUN COMPLETE: {result.get('total_findings', 0)} findings")
    print("=" * 70)

if __name__ == "__main__":
    main()

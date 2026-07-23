"""Diagnostic: analyze experiment planning pipeline for Project C.

Loads input materials, builds Knowledge Asset → Behavior IR → Obligations →
Experiments, then reports compile status and block reasons per obligation.
Saves baseline to experiment_planning_baseline.json.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ai_test_asset_center.enterprise_knowledge_center import (
    build_enterprise_business_knowledge_asset,
    ingest_enterprise_knowledge_files,
)
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir
from ai_test_asset_center.experiment_compiler import compile_experiments


def _text(v: Any) -> str:
    return str(v or "").strip()


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def main() -> None:
    project_id = "contractflow_c"
    input_dir = ROOT / "projects" / project_id / "input"
    if not input_dir.exists():
        print(f"ERROR: input dir not found: {input_dir}")
        return

    # Use a temp root to avoid polluting the real workspace
    with tempfile.TemporaryDirectory(prefix="diag_exp_") as tmp:
        root = Path(tmp)

        # 1. Ingest input files
        files = sorted(p for p in input_dir.iterdir() if p.is_file())
        print(f"[1] Ingesting {len(files)} files from {input_dir.name}/")
        ingest = ingest_enterprise_knowledge_files(
            project_id, files, root=root,
            actor={"name": "diag", "role": "project_owner"},
        )
        print(f"    created={len(ingest.get('created') or [])}, "
              f"duplicates={len(ingest.get('duplicates') or [])}, "
              f"errors={len(ingest.get('errors') or [])}")

        # 2. Build Knowledge Asset
        print("[2] Building Knowledge Asset...")
        asset = build_enterprise_business_knowledge_asset(project_id, root=root)
        summary = _dict(asset.get("summary"))
        print(f"    rules={summary.get('rule_count', '?')}, "
              f"interfaces={summary.get('interface_count', '?')}, "
              f"tables={summary.get('table_count', '?')}")

        # 3. Build Behavior IR
        print("[3] Building Behavior IR...")
        behavior_ir = build_behavior_ir_from_knowledge_asset(
            asset, project_id=project_id,
        )
        ops = _list(behavior_ir.get("operations"))
        actors = _list(behavior_ir.get("actors"))
        invariants = _list(behavior_ir.get("invariants"))
        states = _list(behavior_ir.get("states"))
        relations = _list(behavior_ir.get("relations"))
        print(f"    operations={len(ops)}, actors={len(actors)}, "
              f"invariants={len(invariants)}, states={len(states)}, "
              f"relations={len(relations)}")

        # 4. Compile Obligations
        print("[4] Compiling Obligations...")
        obl_result = compile_obligations_from_behavior_ir(behavior_ir)
        obligations = _list(obl_result.get("obligations"))
        print(f"    obligations={len(obligations)}")

        # 5. Compile Experiments
        print("[5] Compiling Experiments...")
        exp_pack = compile_experiments(
            obligations, behavior_ir=behavior_ir,
            environment_type="staging",
        )
        compiled_count = exp_pack.get("compiled_count", 0)
        blocked_count = exp_pack.get("blocked_count", 0)
        print(f"    compiled={compiled_count}, blocked={blocked_count}")
        print(f"    block_reasons={json.dumps(exp_pack.get('block_reason_counts', {}))}")

        # 6. Per-obligation analysis
        print("\n[6] Per-obligation compile status:")
        print(f"{'Obligation ID':<40} {'Family':<14} {'Status':<10} {'Block Reason'}")
        print("-" * 110)

        baseline_rows = []
        for obl in obligations:
            if not isinstance(obl, dict):
                continue
            oid = _text(obl.get("obligation_id"))
            family = _text(obl.get("risk_family"))
            status = _text(obl.get("compile_status"))
            block_reason = _text(obl.get("block_reason"))
            prop = _dict(obl.get("property"))
            expr = prop.get("expression")
            invariant_ref = _text(prop.get("invariant_ref"))
            operation_ref = _text(prop.get("operation_ref"))

            print(f"{oid:<40} {family:<14} {status:<10} {block_reason}")
            baseline_rows.append({
                "obligation_id": oid,
                "risk_family": family,
                "compile_status": status,
                "block_reason": block_reason,
                "invariant_ref": invariant_ref,
                "operation_ref": operation_ref,
                "expression": expr,
                "source_refs": obl.get("source_refs"),
            })

        # 7. Identify deep targets (EXPERIMENT_NOT_PLANNED candidates)
        print("\n[7] Deep experiment targets (blocked/non-compiled obligations):")
        deep_targets = [
            row for row in baseline_rows
            if row["compile_status"] != "COMPILED"
        ]
        for row in deep_targets:
            print(f"  {row['obligation_id']} [{row['risk_family']}] → {row['block_reason']}")

        # 8. Save baseline
        baseline = {
            "project_id": project_id,
            "pipeline_stage": "experiment_compile",
            "knowledge_asset_summary": summary,
            "behavior_ir_stats": {
                "operations": len(ops),
                "actors": len(actors),
                "invariants": len(invariants),
                "states": len(states),
                "relations": len(relations),
            },
            "obligation_count": len(obligations),
            "compiled_count": compiled_count,
            "blocked_count": blocked_count,
            "block_reason_counts": exp_pack.get("block_reason_counts", {}),
            "obligations": baseline_rows,
            "deep_targets": deep_targets,
        }
        out_path = ROOT / "experiment_planning_baseline.json"
        out_path.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n[8] Baseline saved to {out_path.name}")
        print(f"    Total obligations: {len(obligations)}")
        print(f"    Compiled: {compiled_count}")
        print(f"    Blocked (deep targets): {len(deep_targets)}")


if __name__ == "__main__":
    main()

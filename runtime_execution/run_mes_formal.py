"""MES Formal Run - Real execution against live MES SUT.

Starts the MES server, executes all experiments via real HTTP,
collects findings from Oracle violations, performs independent
reproduction, and outputs real execution deliverables.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

# Add project root to path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from runtime_execution.mes_client import MESClient
from runtime_execution.mes_experiments import run_all_experiments, ExperimentResult


OUT_DIR = os.path.dirname(os.path.abspath(__file__))
MES_SERVER = os.path.join(ROOT, "projects", "mes_f", "mock_server.py")
MES_PORT = 8020


def write_json(filename: str, data):
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  [OUTPUT] {filename}")


def start_mes_server() -> subprocess.Popen:
    """Start MES server as subprocess."""
    print(f"Starting MES server: {MES_SERVER}")
    proc = subprocess.Popen(
        [sys.executable, MES_SERVER],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    return proc


def wait_for_health(client: MESClient, timeout: int = 15) -> bool:
    """Wait for MES server to become reachable."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.health():
            return True
        time.sleep(0.3)
    return False


def reproduce_finding(client: MESClient, exp_fn, run_id: int) -> bool:
    """Reproduce a finding by re-executing the experiment."""
    try:
        result = exp_fn(client)
        return result.is_finding
    except Exception:
        return False


def main():
    print("=" * 60)
    print("MES FORMAL RUN - REAL EXECUTION")
    print("=" * 60)
    ts_start = time.time()

    # 1. Start MES server
    proc = start_mes_server()
    client = MESClient()

    # 2. Wait for health
    print("\nWaiting for MES server...")
    if not wait_for_health(client):
        print("ERROR: MES server not reachable after timeout")
        proc.terminate()
        sys.exit(1)
    print("MES server is UP and healthy.")

    # 3. Execute all experiments
    print("\n--- Executing Experiments ---")
    results = run_all_experiments(client)

    # 4. Collect findings
    findings = [r for r in results if r.is_finding]
    passes = [r for r in results if not r.is_finding]
    print(f"\n--- Results: {len(findings)} FINDINGS, {len(passes)} PASS ---")

    # 5. Independent reproduction (2/2 for each finding)
    print("\n--- Independent Reproduction ---")
    from runtime_execution.mes_experiments import (
        exp_auth_operator_create_product, exp_auth_operator_update_cost,
        exp_auth_operator_delete_wo, exp_auth_operator_work_report_no_factory_check,
        exp_auth_warehouse_create_bom, exp_auth_inspector_release_wo,
        exp_scope_work_centers_cross_org, exp_scope_inspections_cross_org,
        exp_state_release_completed_wo, exp_state_close_from_in_production,
        exp_state_modify_confirmed_plan, exp_state_modify_sales_order_after_confirm,
        exp_state_receipt_without_wo_completed,
        exp_cross_wo_create_no_bom_check, exp_cross_wo_start_no_material_check,
        exp_cross_wo_complete_no_op_check, exp_cross_rework_no_reject_check,
        exp_cross_receipt_no_quality_check, exp_cross_wo_quantity_exceeds_plan,
        exp_conserve_issue_exceeds_reserved, exp_conserve_report_exceeds_planned,
        exp_conserve_inspection_qty_exceeds_sample, exp_conserve_receipt_not_update_wo_qty,
        exp_idempotent_duplicate_sales_order, exp_idempotent_duplicate_receipt,
        exp_compensate_cancel_no_reservation_release, exp_compensate_delete_bom_orphan_lines,
        exp_temporal_wo_start_after_end, exp_temporal_inspection_after_expiry,
        exp_temporal_sales_order_modify_after_plan,
        exp_concurrency_wo_update_no_version, exp_concurrency_issue_return_no_version,
        exp_batch_release_partial_failure, exp_batch_issue_no_atomicity,
    )

    # Map experiment_id -> function
    exp_map = {
        "EXP_AUTH_01": exp_auth_operator_create_product,
        "EXP_AUTH_02": exp_auth_operator_update_cost,
        "EXP_AUTH_03": exp_auth_operator_delete_wo,
        "EXP_AUTH_04": exp_auth_operator_work_report_no_factory_check,
        "EXP_AUTH_05": exp_auth_warehouse_create_bom,
        "EXP_AUTH_06": exp_auth_inspector_release_wo,
        "EXP_SCOPE_01": exp_scope_work_centers_cross_org,
        "EXP_SCOPE_02": exp_scope_inspections_cross_org,
        "EXP_STATE_01": exp_state_release_completed_wo,
        "EXP_STATE_02": exp_state_close_from_in_production,
        "EXP_STATE_03": exp_state_modify_confirmed_plan,
        "EXP_STATE_04": exp_state_modify_sales_order_after_confirm,
        "EXP_STATE_05": exp_state_receipt_without_wo_completed,
        "EXP_CROSS_01": exp_cross_wo_create_no_bom_check,
        "EXP_CROSS_02": exp_cross_wo_start_no_material_check,
        "EXP_CROSS_03": exp_cross_wo_complete_no_op_check,
        "EXP_CROSS_04": exp_cross_rework_no_reject_check,
        "EXP_CROSS_05": exp_cross_receipt_no_quality_check,
        "EXP_CROSS_06": exp_cross_wo_quantity_exceeds_plan,
        "EXP_CONS_01": exp_conserve_issue_exceeds_reserved,
        "EXP_CONS_02": exp_conserve_report_exceeds_planned,
        "EXP_CONS_03": exp_conserve_inspection_qty_exceeds_sample,
        "EXP_CONS_04": exp_conserve_receipt_not_update_wo_qty,
        "EXP_IDEMP_01": exp_idempotent_duplicate_sales_order,
        "EXP_IDEMP_02": exp_idempotent_duplicate_receipt,
        "EXP_COMP_01": exp_compensate_cancel_no_reservation_release,
        "EXP_COMP_02": exp_compensate_delete_bom_orphan_lines,
        "EXP_TEMP_01": exp_temporal_wo_start_after_end,
        "EXP_TEMP_02": exp_temporal_inspection_after_expiry,
        "EXP_TEMP_03": exp_temporal_sales_order_modify_after_plan,
        "EXP_CONC_01": exp_concurrency_wo_update_no_version,
        "EXP_CONC_02": exp_concurrency_issue_return_no_version,
        "EXP_BATCH_01": exp_batch_release_partial_failure,
        "EXP_BATCH_02": exp_batch_issue_no_atomicity,
    }

    reproduction_results = []
    for finding in findings:
        exp_fn = exp_map.get(finding.experiment_id)
        if not exp_fn:
            reproduction_results.append({
                "experiment_id": finding.experiment_id,
                "reproduction_1": "SKIPPED", "reproduction_2": "SKIPPED",
                "stable": False,
            })
            continue
        r1 = reproduce_finding(client, exp_fn, 1)
        r2 = reproduce_finding(client, exp_fn, 2)
        stable = r1 and r2
        reproduction_results.append({
            "experiment_id": finding.experiment_id,
            "reproduction_1": "REPRODUCED" if r1 else "FAILED",
            "reproduction_2": "REPRODUCED" if r2 else "FAILED",
            "reproduction_rate": f"{int(r1)+int(r2)}/2",
            "stable": stable,
        })
        status = "STABLE" if stable else "UNSTABLE"
        print(f"  [{status}] {finding.experiment_id}: {int(r1)+int(r2)}/2")

    # 6. Root cause dedup
    print("\n--- Root Cause Dedup ---")
    root_causes = {}
    for finding in findings:
        key = f"{finding.mechanism}:{finding.oracle_type}"
        if key not in root_causes:
            root_causes[key] = {
                "root_cause_id": f"RC_{finding.experiment_id}",
                "mechanism": finding.mechanism,
                "oracle_type": finding.oracle_type,
                "finding_ids": [],
                "description": finding.description,
                "constraint_ref": finding.oracle_result.constraint_ref,
            }
        root_causes[key]["finding_ids"].append(finding.experiment_id)

    unique_roots = list(root_causes.values())
    print(f"  {len(findings)} findings -> {len(unique_roots)} unique root causes")

    # 7. Output deliverables
    print("\n--- Writing Deliverables ---")
    ts_end = time.time()

    # Execution ledger
    write_json("mes_execution_ledger.json", {
        "schema_version": "qualibug.mes-execution-ledger.v1",
        "run_name": "MES_REAL_EXECUTION_V1",
        "sut": "Discrete Manufacturing MES (projects/mes_f/mock_server.py)",
        "base_url": f"http://localhost:{MES_PORT}",
        "total_experiments": len(results),
        "findings": len(findings),
        "passes": len(passes),
        "execution_mode": "REAL_HTTP",
        "duration_seconds": round(ts_end - ts_start, 2),
        "experiments": [r.to_dict() for r in results],
        "timestamp": ts_start,
    })

    # Findings with evidence
    write_json("mes_findings.json", {
        "schema_version": "qualibug.mes-findings.v1",
        "total_findings": len(findings),
        "findings": [{
            "experiment_id": f.experiment_id,
            "mechanism": f.mechanism,
            "oracle_type": f.oracle_type,
            "description": f.description,
            "oracle_result": f.oracle_result.to_dict(),
            "evidence": f.evidence,
        } for f in findings],
        "timestamp": ts_start,
    })

    # Reproduction results
    reproduced_count = sum(1 for r in reproduction_results if r.get("stable"))
    write_json("mes_reproduction.json", {
        "schema_version": "qualibug.mes-reproduction.v1",
        "total_findings": len(findings),
        "reproduced": reproduced_count,
        "reproduction_rate": f"{reproduced_count}/{len(findings)}",
        "results": reproduction_results,
        "timestamp": ts_start,
    })

    # Root causes
    write_json("mes_root_causes.json", {
        "schema_version": "qualibug.mes-root-causes.v1",
        "total_findings": len(findings),
        "unique_root_causes": len(unique_roots),
        "roots": unique_roots,
        "timestamp": ts_start,
    })

    # Summary
    print(f"\n{'='*60}")
    print(f"MES FORMAL RUN COMPLETE")
    print(f"  Total experiments: {len(results)}")
    print(f"  Findings: {len(findings)}")
    print(f"  Unique root causes: {len(unique_roots)}")
    print(f"  Reproduced: {reproduced_count}/{len(findings)}")
    print(f"  Duration: {ts_end - ts_start:.1f}s")
    print(f"{'='*60}")

    # 8. Stop MES server
    proc.terminate()
    proc.wait(timeout=5)
    print("MES server stopped.")


if __name__ == "__main__":
    main()

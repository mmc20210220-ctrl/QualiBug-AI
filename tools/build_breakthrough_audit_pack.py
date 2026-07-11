"""Build Spec §15 audit pack (secret-free) for the breakthrough implementation."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_test_asset_center.artifact_redactor import scan_for_secrets, write_json_redacted
from ai_test_asset_center.behavior_ir import SCHEMA_VERSION as BIR_SCHEMA
from ai_test_asset_center.experiment_contract import SCHEMA_VERSION as EXP_SCHEMA
from ai_test_asset_center.test_obligation import SCHEMA_VERSION as OBL_SCHEMA


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def _run(cmd: list[str]) -> dict:
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "command": cmd,
        "exit_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
    }


def main() -> None:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    audit = ROOT / "_audit_packs" / f"breakthrough_{stamp}"
    audit.mkdir(parents=True, exist_ok=True)

    # Git identity
    git = {
        "branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "sha": _run(["git", "rev-parse", "HEAD"]),
        "status": _run(["git", "status", "--porcelain"]),
    }
    branch = (git["branch"]["stdout_tail"] or "").strip()
    sha = (git["sha"]["stdout_tail"] or "").strip()

    changed = [
        line.strip()
        for line in (git["status"]["stdout_tail"] or "").splitlines()
        if line.strip()
    ]
    # Audit packs are only reproducible from a committed, clean worktree.
    if changed:
        write_json_redacted(
            audit / "AUDIT_PACK.json",
            {
                "schema_version": "qualibug.breakthrough-audit-pack.v1",
                "created_at_utc": stamp,
                "final_status": "INCOMPLETE",
                "capability_breakthrough_claim": False,
                "reason": "dirty_worktree_blocks_reproducible_audit_pack",
                "code_version": {
                    "branch": branch,
                    "commit_sha": sha,
                    "worktree_changed_file_count": len(changed),
                    "changed_files_manifest": changed[:500],
                    "worktree_clean": False,
                },
                "required_next_step": "Commit or discard local changes, then rebuild the audit pack from a clean worktree.",
            },
        )
        (audit / "README.md").write_text(
            "\n".join([
                "# QualiBug Breakthrough Audit Pack",
                "",
                "Status: **INCOMPLETE** — dirty worktree; pack is not reproducible.",
                "",
                f"- Branch: `{branch}`",
                f"- Commit: `{sha}`",
                f"- Changed files: `{len(changed)}`",
                "",
            ]),
            encoding="utf-8",
        )
        print(json.dumps({
            "audit_dir": str(audit),
            "final_status": "INCOMPLETE",
            "reason": "dirty_worktree_blocks_reproducible_audit_pack",
            "changed_file_count": len(changed),
        }, ensure_ascii=False))
        raise SystemExit(2)

    # Freeze redacted baseline (never overwrite originals)
    baseline_dir = audit / "frozen_baseline_llm_throughput"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    src_summary = ROOT / "_funnel_runs" / "llm_throughput.json"
    src_sub = ROOT / "_funnel_runs" / "llm_throughput.evaluation_submission.json"
    baseline_meta: dict = {
        "schema_version": "qualibug.phase0-baseline-receipt.v1",
        "commercial_claim_status": "NOT_MEASURED",
        "note": "Single-target diagnostic only; not a commercial promotion baseline.",
        "source_files": {"summary": str(src_summary), "submission": str(src_sub)},
    }
    if src_summary.exists():
        write_json_redacted(baseline_dir / "llm_throughput.redacted.json", json.loads(src_summary.read_text(encoding="utf-8")))
        baseline_meta["summary_sha256_original"] = _sha256(src_summary)
        baseline_meta["summary_sha256_redacted"] = _sha256(baseline_dir / "llm_throughput.redacted.json")
    if src_sub.exists():
        write_json_redacted(
            baseline_dir / "llm_throughput.evaluation_submission.redacted.json",
            json.loads(src_sub.read_text(encoding="utf-8")),
        )
        baseline_meta["submission_sha256_original"] = _sha256(src_sub)
        redacted_path = baseline_dir / "llm_throughput.evaluation_submission.redacted.json"
        baseline_meta["submission_sha256_redacted"] = _sha256(redacted_path)
        baseline_meta["redacted_submission_secret_scan"] = scan_for_secrets(
            json.loads(redacted_path.read_text(encoding="utf-8"))
        )
    write_json_redacted(baseline_dir / "baseline_receipt.json", baseline_meta)

    # Tests
    test_cmds = [
        ["python", "-m", "pytest", "-q",
         "tests/test_phase0_trust_security_baseline.py",
         "tests/test_behavior_ir_obligation_experiment.py",
         "tests/test_v12_behavior_ir_vertical_slice.py",
         "tests/test_fixture_dag.py",
         "tests/test_pipeline_health_visibility.py"],
        ["python", "tools/discovery_evaluation.py", "inspect",
         "--manifest", str(ROOT / "_private_eval" / "commercial_v1" / "evaluation_manifest.json")],
    ]
    test_results = [_run(cmd) for cmd in test_cmds]

    frontend_cmds = [
        ["npm", "run", "lint"],
        ["npm", "run", "typecheck"],
        ["npm", "run", "build"],
    ]
    frontend_results = []
    frontend_cwd = ROOT / "frontend"
    for cmd in frontend_cmds:
        proc = subprocess.run(
            cmd,
            cwd=str(frontend_cwd),
            capture_output=True,
            text=True,
            shell=True,
            encoding="utf-8",
            errors="replace",
        )
        frontend_results.append({
            "command": cmd,
            "exit_code": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-2000:],
            "stderr_tail": (proc.stderr or "")[-1000:],
        })

    # Config floors
    floor_check = _run([
        "python", "-c",
        "from ai_test_asset_center.policy_registry import get_policy_registry;"
        "from ai_test_asset_center.stage_reason_all_v2 import MAX_HYPOTHESES;"
        "s=get_policy_registry().get_active_strategy();"
        "assert s.reasoner.timeout_seconds>=300;"
        "assert s.reasoner.max_tokens>=32768;"
        "assert s.execution.max_tokens>=32768;"
        "assert s.reasoner.max_workers<=4;"
        "assert MAX_HYPOTHESES==15;"
        "print('FLOORS_OK', s.reasoner.timeout_seconds, s.reasoner.max_tokens, MAX_HYPOTHESES, s.reasoner.max_workers)",
    ])

    commercial_manifest = ROOT / "_private_eval" / "commercial_v1" / "evaluation_manifest.json"
    diagnostic_manifest = ROOT / "_private_eval" / "diagnostic_benchmark_mall_v1" / "evaluation_manifest.json"
    diagnostic_receipts = list((ROOT / "_private_eval" / "diagnostic_benchmark_mall_v1" / "receipts").rglob("*.json"))
    diagnostic_metrics = None
    if diagnostic_receipts:
        receipt = json.loads(diagnostic_receipts[0].read_text(encoding="utf-8"))
        diagnostic_metrics = {
            "path": str(diagnostic_receipts[0]),
            "sha256": _sha256(diagnostic_receipts[0]),
            "measurement_status": receipt.get("measurement_status"),
            "metrics": receipt.get("metrics"),
            "target_id": receipt.get("target_id"),
        }

    pack = {
        "schema_version": "qualibug.breakthrough-audit-pack.v1",
        "created_at_utc": stamp,
        "final_status": "INCOMPLETE",
        "capability_breakthrough_claim": False,
        "reason": (
            "Commercial-shape private manifest is frozen and a single-target diagnostic "
            "external evaluate receipt exists, but held-out/clean live runs and four "
            "champion/challenger replay+shadow reports are still missing. Gate D is not met."
        ),
        "code_version": {
            "branch": branch,
            "commit_sha": sha,
            "worktree_changed_file_count": 0,
            "changed_files_manifest": [],
            "worktree_clean": True,
        },
        "schema_versions": {
            "behavior_ir": BIR_SCHEMA,
            "test_obligation": OBL_SCHEMA,
            "experiment": EXP_SCHEMA,
            "fixture_dag": "qualibug.fixture-dag.v1",
            "artifact_redactor": "qualibug.artifact-redactor.v1",
            "quality_projection": "qualibug.discovery-quality-projection.v1",
        },
        "fingerprints": {
            "baseline_receipt": baseline_meta,
            "commercial_manifest": str(commercial_manifest) if commercial_manifest.exists() else None,
            "commercial_manifest_sha256": _sha256(commercial_manifest) if commercial_manifest.exists() else None,
            "diagnostic_manifest": str(diagnostic_manifest) if diagnostic_manifest.exists() else None,
            "diagnostic_manifest_sha256": _sha256(diagnostic_manifest) if diagnostic_manifest.exists() else None,
        },
        "commands": {
            "tests": test_results,
            "frontend": frontend_results,
            "config_floors": floor_check,
        },
        "evaluation": {
            "commercial_shape_manifest_ready": commercial_manifest.exists(),
            "measurement_status": "PARTIAL",
            "held_in_diagnostic": diagnostic_metrics,
            "held_out": None,
            "clean_target": None,
            "champion_challenger_reports": [],
            "baseline_vs_candidate": None,
            "blocked_receipts": None,
            "gate_d": {
                "recall_ge_30": False,
                "precision_ge_50": False,
                "note": "Single-target diagnostic only; not Gate D evidence",
            },
        },
        "safety": {
            "production_requests": None,
            "write_audit": None,
            "cleanup": None,
            "dirty_environment": None,
            "security_incidents": None,
            "secret_scan_baseline_redacted": baseline_meta.get("redacted_submission_secret_scan"),
            "funnel_submission_redacted_in_place": True,
        },
        "open_items": [
            "Run live held-out + clean target scans against distinct non-prod environments",
            "Produce four champion/challenger replay+shadow reports for promotion",
            "Full llm_throughput re-run after Phase 1-5 to refresh diagnostic funnel",
            "Continue heuristic→contract oracle migration beyond demotion hooks",
        ],
        "known_risks": [
            "Commercial manifest currently points multiple industries at localhost:8080 for scaffolding; live multi-target execution still required",
            "Diagnostic held-in recall/precision are far below Gate D thresholds",
            "Legacy slice path remains active alongside obligation/experiment compile",
        ],
        "live_smoke": None,
    }
    # Attach latest live smoke receipt if present
    smoke_dirs = sorted((ROOT / "_audit_packs").glob("live_smoke_*"), reverse=True)
    if smoke_dirs:
        smoke_receipt = smoke_dirs[0] / "live_smoke_receipt.json"
        if smoke_receipt.exists():
            pack["live_smoke"] = {
                "path": str(smoke_receipt),
                "sha256": _sha256(smoke_receipt),
                "summary": json.loads(smoke_receipt.read_text(encoding="utf-8")),
            }
    blocked_dirs = sorted((ROOT / "_audit_packs").glob("blocked_receipts_*"), reverse=True)
    if blocked_dirs:
        blocked_pack = blocked_dirs[0] / "BLOCKED_RECEIPTS.json"
        if blocked_pack.exists():
            blocked_payload = json.loads(blocked_pack.read_text(encoding="utf-8"))
            pack["evaluation"]["blocked_receipts"] = {
                "path": str(blocked_pack),
                "sha256": _sha256(blocked_pack),
                "measurement_status": blocked_payload.get("measurement_status"),
                "receipt_ids": sorted((blocked_payload.get("receipts") or {}).keys()),
            }
            pack["evaluation"]["held_out"] = {
                "status": "BLOCKED",
                "reason": ((blocked_payload.get("receipts") or {}).get("held_out_live") or {}).get("blocked_reason"),
            }
            pack["evaluation"]["clean_target"] = {
                "status": "BLOCKED",
                "reason": ((blocked_payload.get("receipts") or {}).get("clean_target_live") or {}).get("blocked_reason"),
            }
            pack["evaluation"]["champion_challenger_reports"] = [
                {
                    "status": "BLOCKED",
                    "reason": ((blocked_payload.get("receipts") or {}).get("champion_challenger") or {}).get("blocked_reason"),
                }
            ]
    write_json_redacted(audit / "AUDIT_PACK.json", pack)
    (audit / "README.md").write_text(
        "\n".join([
            "# QualiBug Breakthrough Audit Pack",
            "",
            f"Status: **{pack['final_status']}** / commercial claim: **NOT_MEASURED**",
            "",
            "This pack is secret-free. Do not distribute original evaluation submissions",
            "that predate artifact_redactor write-boundary enforcement.",
            "",
            f"- Branch: `{branch}`",
            f"- Commit: `{sha}`",
            f"- Behavior IR: `{BIR_SCHEMA}`",
            f"- Obligation: `{OBL_SCHEMA}`",
            f"- Experiment: `{EXP_SCHEMA}`",
            "",
        ]),
        encoding="utf-8",
    )
    print(json.dumps({"audit_dir": str(audit), "final_status": pack["final_status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

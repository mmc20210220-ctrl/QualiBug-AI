"""Formal revalidation scan after cleanup-equivalence root-cause fix.

Reuses frozen Canonical 1498 + Unlock Set 61. Entry authority is this start
manifest only (POST /api/v1/scan). Does not alter Unlock Set IDs.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "spec_v1_6_2_cleanup_reval"
UNLOCK = ROOT / "artifacts" / "spec_v1_6_2" / "v162_candidate_unlock_set.json"
CANONICAL = ROOT / "artifacts" / "spec_v1_6_2" / "v162_canonical_obligation_manifest.json"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(cmd: list[str]) -> str:
    return subprocess.check_output(["git", *cmd], cwd=ROOT, text=True).strip()


def freeze_start_manifest() -> dict:
    unlock = json.loads(UNLOCK.read_text(encoding="utf-8"))
    ids = list(unlock.get("obligation_ids") or [])
    assert len(ids) == 61, f"unlock set size drifted: {len(ids)}"
    sorted_ids = sorted(ids)
    unlock_hash = hashlib.sha256(
        json.dumps(ids, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    sorted_hash = hashlib.sha256(
        json.dumps(sorted_ids, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    canonical = json.loads(CANONICAL.read_text(encoding="utf-8"))
    canon_hash = (
        canonical.get("canonical_obligation_manifest", {}).get("manifest_hash")
        or canonical.get("manifest_hash")
        or _sha256_file(CANONICAL)
    )
    commit = _git(["rev-parse", "HEAD"])
    tree = _git(["rev-parse", "HEAD^{tree}"])
    entry = {
        "method": "POST",
        "url": "http://127.0.0.1:8088/api/v1/scan",
        "headers": {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-QualiBug-Actor": "formal_cleanup_reval",
            "X-QualiBug-Role": "project_owner",
            "X-QualiBug-Project-Scopes": "benchmark_mall_131",
        },
        "body": {
            "project_id": "benchmark_mall_131",
            "base_url": "http://127.0.0.1:8080",
            "approved_base_url": "http://127.0.0.1:8080",
            "environment_type": "test",
            "environment_ref": "sandbox",
            # Force a fresh campaign so cleanup-equivalence revalidation is not
            # poisoned by resumed cached attempt ledgers from V1.6.2-R1.
            "campaign_rerun_key": "v162_cleanup_equivalence_reval_v14",
        },
    }
    manifest = {
        "schema_version": "qualibug.v162-cleanup-reval-start-manifest.v1",
        "spec_version": "V1.6.2-CLEANUP-EQUIVALENCE-REVAL",
        "run_name": "V1_6_2_CLEANUP_EQUIVALENCE_ROOTCAUSE_REVAL_V14",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "commit_sha": commit,
        "tree_hash": tree,
        "working_tree_dirty": bool(_git(["status", "--porcelain"])),
        "canonical_obligation_count": int(
            canonical.get("canonical_obligation_manifest", {}).get("obligation_count")
            or canonical.get("obligation_count")
            or 1498
        ),
        "canonical_obligation_manifest_hash": canon_hash,
        "candidate_unlock_set_size": 61,
        "candidate_unlock_set_hash": unlock.get("candidate_unlock_set_hash")
        or unlock_hash,
        "candidate_unlock_set_sorted_ids_hash": sorted_hash,
        "candidate_unlock_set_frozen": True,
        "shared_fix_point": "FINALIZER_RECEIPT_RETENTION_ACROSS_FOLLOW_ON_BATCHES",
        "prior_breakpoint": "UNLOCK_COVERAGE_EXPANSION",
        "entry_request": entry,
        "post_start_tuning_forbidden": True,
        "file_hashes": {
            "cleanup_observation_adapter": _sha256_file(
                ROOT / "ai_test_asset_center" / "cleanup_observation_adapter.py"
            ),
            "cleanup_execution_receipt": _sha256_file(
                ROOT / "ai_test_asset_center" / "cleanup_execution_receipt.py"
            ),
            "cleanup_equivalence": _sha256_file(
                ROOT / "ai_test_asset_center" / "cleanup_equivalence.py"
            ),
            "write_reversibility_contract": _sha256_file(
                ROOT / "ai_test_asset_center" / "write_reversibility_contract.py"
            ),
            "experiment_cleanup": _sha256_file(
                ROOT / "ai_test_asset_center" / "experiment_cleanup.py"
            ),
            "experiment_cleanup_executor": _sha256_file(
                ROOT / "ai_test_asset_center" / "experiment_cleanup_executor.py"
            ),
            "experiment_outcome_finalizer": _sha256_file(
                ROOT / "ai_test_asset_center" / "experiment_outcome_finalizer.py"
            ),
            "experiment_runtime_support": _sha256_file(
                ROOT / "ai_test_asset_center" / "experiment_runtime_support.py"
            ),
            "runtime_binding_graph": _sha256_file(
                ROOT / "ai_test_asset_center" / "runtime_binding_graph.py"
            ),
            "behavior_ir": _sha256_file(
                ROOT / "ai_test_asset_center" / "behavior_ir.py"
            ),
            "observer_contracts_base": _sha256_file(
                ROOT / "ai_test_asset_center" / "observer_contracts_base.py"
            ),
            "discovery_runtime_execution": _sha256_file(
                ROOT / "ai_test_asset_center" / "discovery_runtime_execution.py"
            ),
            "discovery_runtime_execution_support": _sha256_file(
                ROOT
                / "ai_test_asset_center"
                / "discovery_runtime_execution_support.py"
            ),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "reval_start_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (OUT / "reval_candidate_unlock_set_reuse_receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "qualibug.v162-cleanup-reval-unlock-reuse.v1",
                "unlock_set_reuse": {
                    "original_unlock_set_path": str(UNLOCK.relative_to(ROOT)),
                    "original_unlock_set_hash": manifest["candidate_unlock_set_hash"],
                    "obligation_ids": ids,
                    "count": 61,
                    "modified_after_freeze": False,
                    "added_ids": [],
                    "removed_ids": [],
                },
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def post_scan(entry: dict) -> dict:
    body = json.dumps(entry["body"]).encode("utf-8")
    headers = dict(entry.get("headers") or {})
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json")
    req = urllib.request.Request(
        entry["url"],
        data=body,
        headers=headers,
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=1800) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = int(exc.code)
        elapsed_ms = int((time.time() - started) * 1000)
        try:
            data = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            data = {"raw_error_body": raw[:8000]}
        data["_http_status"] = status
        data["_elapsed_ms_wall"] = elapsed_ms
        data["_scanned_at"] = datetime.now(timezone.utc).isoformat()
        data["_error"] = f"HTTPError:{status}"
        (OUT / "reval_scan_response.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return data
    except (ConnectionResetError, TimeoutError, OSError) as exc:
        elapsed_ms = int((time.time() - started) * 1000)
        data = {
            "ok": False,
            "error": f"transport_{type(exc).__name__}:{exc}",
            "_http_status": 0,
            "_elapsed_ms_wall": elapsed_ms,
            "_scanned_at": datetime.now(timezone.utc).isoformat(),
        }
        (OUT / "reval_scan_response.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return data
    elapsed_ms = int((time.time() - started) * 1000)
    data = json.loads(raw)
    data["_http_status"] = status
    data["_elapsed_ms_wall"] = elapsed_ms
    data["_scanned_at"] = datetime.now(timezone.utc).isoformat()
    (OUT / "reval_scan_response.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return data


def main() -> None:
    manifest = freeze_start_manifest()
    entry = manifest["entry_request"]
    print("run", manifest["run_name"])
    print("commit", manifest["commit_sha"][:12], "tree", manifest["tree_hash"][:12])
    print("unlock_N", manifest["candidate_unlock_set_size"])
    print("POST", entry["url"], "...")
    data = post_scan(entry)
    camp = data.get("campaign") or {}
    print(
        "done status=",
        data.get("execution_status"),
        "http=",
        data.get("_http_status"),
        "ms=",
        data.get("total_ms") or data.get("_elapsed_ms_wall"),
        "selected=",
        camp.get("obligation_attempt_selected_count"),
        "terminal=",
        camp.get("obligation_attempt_terminal_count"),
    )


if __name__ == "__main__":
    main()

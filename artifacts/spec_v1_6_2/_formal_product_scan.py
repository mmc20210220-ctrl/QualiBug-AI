"""V1.6.2 formal product entry scan (POST /api/v1/scan) + funnel capture."""
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
OUT = ROOT / "artifacts" / "spec_v1_6_2"
REPORT = ROOT / "platform_outputs" / "benchmark_mall_131" / "intelligence_report.json"

ENTRY = {
    "method": "POST",
    "url": "http://127.0.0.1:8088/api/v1/scan",
    "body": {
        "project_id": "benchmark_mall_131",
        "base_url": "http://127.0.0.1:8080",
        "approved_base_url": "http://127.0.0.1:8080",
        "environment_type": "test",
        "environment_ref": "sandbox",
    },
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_start_manifest() -> dict:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    )
    man = json.loads((OUT / "v162_canonical_obligation_manifest.json").read_text(encoding="utf-8"))
    unlock = json.loads((OUT / "v162_candidate_unlock_set.json").read_text(encoding="utf-8"))
    payload = {
        "schema_version": "qualibug.v162-start-manifest.v1",
        "spec_version": "V1.6.2",
        "run_name": "V1_6_2_RECEIPT_AUTHORITY_COVERAGE_V1",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "commit_sha": commit,
        "tree_hash": tree,
        "working_tree_dirty": dirty,
        "dirty_policy": "artifacts/spec_v1_6_2 untracked evidence only; product code committed+pushed",
        "canonical_obligation_count": 1498,
        "canonical_obligation_manifest_hash": man["canonical_obligation_manifest"]["manifest_hash"],
        "shared_fix_point": unlock["shared_fix_point"],
        "candidate_unlock_set_size": unlock["N"],
        "candidate_unlock_set_frozen": unlock["frozen"],
        "file_hashes": {
            "operational_receipts": _sha(ROOT / "ai_test_asset_center/operational_receipts.py"),
            "experiment_outcome_finalizer": _sha(
                ROOT / "ai_test_asset_center/experiment_outcome_finalizer.py"
            ),
        },
        "entry_request": ENTRY,
        "post_start_tuning_forbidden": True,
    }
    (OUT / "v162_start_manifest.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def post_scan() -> dict:
    body = json.dumps(ENTRY["body"]).encode("utf-8")
    req = urllib.request.Request(
        ENTRY["url"],
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=1800) as resp:
        raw = resp.read().decode("utf-8")
        status = resp.status
    elapsed_ms = int((time.time() - started) * 1000)
    data = json.loads(raw)
    data["_http_status"] = status
    data["_elapsed_ms_wall"] = elapsed_ms
    (OUT / "v162_scan_response.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return data


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    start = write_start_manifest()
    print("start_manifest", start["commit_sha"][:12], "dirty", start["working_tree_dirty"])
    print("POST", ENTRY["url"], "...")
    try:
        data = post_scan()
    except Exception as exc:
        (OUT / "v162_scan_error.json").write_text(
            json.dumps({"error": str(exc), "at": datetime.now(timezone.utc).isoformat()}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        raise
    print(
        "scan done status=",
        data.get("execution_status"),
        "http=",
        data.get("_http_status"),
        "ms=",
        data.get("total_ms") or data.get("_elapsed_ms_wall"),
    )
    camp = data.get("campaign") or {}
    print(
        "selected=",
        camp.get("obligation_attempt_selected_count"),
        "terminal=",
        camp.get("obligation_attempt_terminal_count"),
        "fp=",
        camp.get("obligation_attempt_ledger_fingerprint"),
    )


if __name__ == "__main__":
    main()

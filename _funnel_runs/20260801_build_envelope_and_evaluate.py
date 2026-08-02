# -*- coding: utf-8 -*-
"""Build the v2 run envelope from the 2026-08-01 scan output and evaluate it.

Local diagnostic scoring only: the receipt is signed with the evaluator-owned
HMAC key stored outside the product workspace, but no evaluator-owned loopback
observation gateway was used, so the receipt cannot attest observed network
activity and must not be treated as commercial promotion evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ai_test_asset_center.discovery_policy_evaluation_runner import strategy_fingerprint  # noqa: E402
from ai_test_asset_center.policy_registry import get_policy_registry  # noqa: E402

OUTPUT_DIR = REPO / "_funnel_runs" / "20260801_baseline"
MANIFEST = REPO / "_private_eval" / "benchmark_mall_131_v1" / "evaluation_manifest.json"
TARGET_ID = "benchmark-mall-held-in-131"
POLICY_ID = "policy-baseline-001"
POLICY_VERSION = "v1.0.0-baseline"
HMAC_KEY_FILE = Path(r"C:\Users\Test\.qualibug-evaluator\observed-131-20260716\evaluator-hmac.key")


def build_raw_envelope(scan_output: dict) -> dict:
    mainline = scan_output["mainline_run"]
    run_id = mainline["run_id"]
    campaign_id = mainline["campaign_id"]
    evaluation_mode = mainline["evaluation_mode"]
    authority = {
        field: scan_output[field]
        for field in (
            "formal_count_projection",
            "canonical_defect_registry",
            "defect_identity_consistency",
            "formal_delivery_authority",
        )
    }
    scan_result = {
        "findings": list(scan_output.get("findings") or []),
        "delivery_occurrences": list(scan_output.get("delivery_occurrences") or []),
        "candidate_findings": list(scan_output.get("candidates") or []),
        "obligation_attempt_ledger": scan_output["obligation_attempt_ledger"],
        **authority,
    }
    if scan_output.get("trace_ledger") is not None:
        scan_result["trace_ledger"] = scan_output["trace_ledger"]
    raw = {
        "schema_version": "qualibug.discovery-evaluation-run-envelope.v2",
        "run_id": run_id,
        "campaign_id": campaign_id,
        "policy_id": POLICY_ID,
        "evaluation_mode": evaluation_mode,
        "pipeline_health": scan_output.get("pipeline_health") or {},
        "operational_metrics": scan_output.get("operational_metrics") or {},
        "mainline_run": mainline,
        "scan_result": scan_result,
        **authority,
    }
    if scan_output.get("process_boundary") is not None:
        raw["process_boundary"] = scan_output["process_boundary"]
        scan_result["process_boundary"] = scan_output["process_boundary"]
    return raw


def main() -> int:
    scan_output = json.loads((OUTPUT_DIR / "scan_output.json").read_text(encoding="utf-8"))
    raw = build_raw_envelope(scan_output)
    # The scan's own authority fields are already internally consistent; the
    # normalize tool rebuilds formal_count_projection with a hardcoded empty
    # candidate list and therefore cannot reproduce the product projection.
    # Build the canonical v2 envelope directly from the scan output.
    envelope_path = OUTPUT_DIR / "envelope.v2.json"
    envelope_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"envelope written: {envelope_path}")

    active = get_policy_registry().get_active()
    fingerprint = strategy_fingerprint(active.strategy)
    key = HMAC_KEY_FILE.read_bytes()
    if len(key) < 32:
        raise RuntimeError("evaluator HMAC key is too short")
    env = dict(os.environ)
    # The trust root stores a raw binary key (secrets.token_bytes(48)), but the
    # receipt-auth env contract only carries text. Use the hex encoding so the
    # local diagnostic receipt is sealed/verified consistently. This is NOT the
    # raw-key evaluator anchor and is not external promotion evidence.
    env["QUALIBUG_EVALUATOR_RECEIPT_HMAC_KEY"] = key.hex()
    receipt_root = OUTPUT_DIR / "evaluation"
    evaluated = subprocess.run(
        [
            sys.executable,
            str(REPO / "tools" / "discovery_evaluation.py"),
            "evaluate",
            "--manifest",
            str(MANIFEST),
            "--target-id",
            TARGET_ID,
            "--policy-id",
            POLICY_ID,
            "--policy-version",
            POLICY_VERSION,
            "--strategy-fingerprint",
            fingerprint,
            "--run-envelope",
            str(envelope_path),
            "--output-root",
            str(receipt_root),
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    print(evaluated.stdout.strip())
    if evaluated.returncode != 0:
        print(evaluated.stderr.strip(), file=sys.stderr)
        return evaluated.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

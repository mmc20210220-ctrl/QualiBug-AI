"""Offline: inspect HARNESS_FAILED cleanup failure details."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

opt = json.loads(
    Path(r"D:/QualiBug-AI/QualiBug-AI-main/_funnel_runs/optimized.json").read_text(
        encoding="utf-8"
    )
)
attempts = opt["full_result"]["obligation_attempt_ledger"]["attempts"]
hf = [a for a in attempts if a.get("terminal_status") == "HARNESS_FAILED"]

# Map ops
bir_ops = {
    o.get("id"): (
        o.get("method"),
        o.get("path") or o.get("path_template"),
    )
    for o in opt["full_result"]["v12"]["behavior_ir"]["operations"]
}

print("HF count", len(hf))
for a in hf:
    ops = [bir_ops.get(x, x) for x in (a.get("operation_refs") or [])]
    deb = a.get("delivery_evidence_bundle") or {}
    finding = deb.get("finding") if isinstance(deb.get("finding"), dict) else {}
    oracle = deb.get("oracle_receipt") if isinstance(deb.get("oracle_receipt"), dict) else {}
    er = deb.get("execution_receipt") if isinstance(deb.get("execution_receipt"), dict) else {}
    cleanup = (a.get("operational_receipt") or {}).get("cleanup_outcome") or {}
    print("---")
    print("ops", ops, "risk", a.get("risk_family"))
    print(
        "oracle",
        oracle.get("status"),
        "violated",
        oracle.get("violated"),
        "reason",
        oracle.get("reason_code"),
    )
    print("finding", (finding.get("title") or finding.get("category") or "")[:120])
    print("cleanup", cleanup)

    # Walk for cleanup step failures
    failures = []

    def walk(o, path=""):
        if isinstance(o, dict):
            status = str(o.get("status") or o.get("cleanup_status") or "")
            if status.upper() in {"FAILED", "ERROR"} or o.get("failure_count"):
                if any(
                    k in o
                    for k in (
                        "status_code",
                        "error",
                        "message",
                        "failure_reason",
                        "path",
                        "method",
                        "cleanup_action",
                    )
                ):
                    failures.append((path, {k: o.get(k) for k in list(o)[:20]}))
            for k, v in o.items():
                if k in {"cleanup", "cleanup_steps", "compensations", "steps", "writes"}:
                    walk(v, path + "/" + k)
                elif isinstance(v, (dict, list)) and path.count("/") < 6:
                    walk(v, path + "/" + k)
        elif isinstance(o, list):
            for i, v in enumerate(o[:30]):
                walk(v, f"{path}[{i}]")

    walk(deb)
    walk(er)
    for path, row in failures[:6]:
        print(" fail@", path, json.dumps(row, ensure_ascii=False)[:240])

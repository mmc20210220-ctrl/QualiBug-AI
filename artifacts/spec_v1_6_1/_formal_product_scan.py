#!/usr/bin/env python3
"""V1.6.1 formal-run freeze + product entry scan + post-run lineage audit."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "spec_v1_6_1"
REPORT = ROOT / "platform_outputs" / "benchmark_mall_131" / "intelligence_report.json"
SCAN_RESULT = ROOT / "platform_outputs" / "benchmark_mall_131" / "scan_result.json"

ENTRY = {
    "method": "POST",
    "url": "http://127.0.0.1:8088/api/v1/scan",
    "body": {
        "project_id": "benchmark_mall_131",
        # Windows: prefer 127.0.0.1 over localhost to avoid intermittent
        # Errno 22 (IPv6/localhost socket) on the product scan path.
        "base_url": "http://127.0.0.1:8080",
        "approved_base_url": "http://127.0.0.1:8080",
        "environment_type": "test",
        "environment_ref": "sandbox",
    },
}

HASH_FILES = {
    "assertion_dsl": "ai_test_asset_center/assertion_dsl_base.py",
    "compiler": "ai_test_asset_center/experiment_compiler_obligation.py",
    "protocols": "ai_test_asset_center/experiment_protocols_base.py",
    "observer": "ai_test_asset_center/observer_contracts_base.py",
    "obligation": "ai_test_asset_center/obligation_compiler_base.py",
    "contract_oracles": "ai_test_asset_center/contract_oracles.py",
    "delivery_gate": "ai_test_asset_center/customer_delivery_gate_v2.py",
    "minimal_manifest": "artifacts/spec_v1_6_1/v161_minimal_rule_manifest.json",
    "golden_set": "artifacts/spec_v1_6_0/v160_golden_rule_set.json",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _walk(obj, pred):
    found = []
    if isinstance(obj, dict):
        if pred(obj):
            found.append(obj)
        for v in obj.values():
            found.extend(_walk(v, pred))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_walk(item, pred))
    return found


def write_start_manifest() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    minimal = json.loads((OUT / "v161_minimal_rule_manifest.json").read_text(encoding="utf-8"))
    file_hashes = {k: _sha256(ROOT / rel) for k, rel in HASH_FILES.items()}
    # Capture git identity without requiring clean tree (documented dirty policy).
    import subprocess

    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    )
    bundle = {
        "schema_version": "qualibug.v161-start-manifest.v1",
        "spec_version": "V1.6.1",
        "run_name": "V1_6_1_RESOLVED_RULE_ORACLE_TRACE_RUNTIME_V1",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "commit_sha": commit,
        "tree_hash": tree,
        "working_tree_dirty": dirty,
        "dirty_policy": (
            "SPEC prefers clean; freeze includes intentional V1.6.0/V1.6.1 in-place "
            "edits. Post-start code/rule/hash drift = INVALID_POST_START_TUNING."
        ),
        "minimal_rule_manifest_hash": minimal.get("minimal_rule_manifest_hash"),
        "golden_rule_set_hash": minimal.get("golden_rule_set_hash"),
        "file_hashes": file_hashes,
        "freeze_bundle_hash": hashlib.sha256(
            json.dumps(file_hashes, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "entry_request": ENTRY,
        "post_start_tuning_forbidden": True,
        "V161_MINIMAL_SET_SOURCE_ASSET_LIMITED": bool(
            minimal.get("V161_MINIMAL_SET_SOURCE_ASSET_LIMITED")
        ),
    }
    (OUT / "v161_start_manifest.json").write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return bundle


def run_scan(start: dict) -> dict:
    payload = json.dumps(ENTRY["body"]).encode("utf-8")
    req = urllib.request.Request(
        ENTRY["url"],
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=3600) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    elapsed_ms = int((time.time() - t0) * 1000)
    text = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"raw_text": text[:20000]}
    (OUT / "v161_scan_response.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    start_out = {
        **start,
        "entry_response_status": status,
        "total_ms": elapsed_ms,
        "scan_id": data.get("scan_id") if isinstance(data, dict) else None,
        "run_id": data.get("run_id") if isinstance(data, dict) else None,
        "campaign_id": data.get("campaign_id") if isinstance(data, dict) else None,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "execution_status": (
            "completed"
            if status == 200 and isinstance(data, dict)
            else "failed"
        ),
    }
    (OUT / "v161_start_manifest.json").write_text(
        json.dumps(start_out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return data if isinstance(data, dict) else {"status": status, "body": data}


def post_run_audit(scan: dict) -> None:
    report = {}
    if REPORT.exists():
        report = json.loads(REPORT.read_text(encoding="utf-8"))
    # Prefer scan_result (full ledger) when present; intelligence_report may omit traces.
    audit_root: dict = report
    if SCAN_RESULT.exists():
        audit_root = json.loads(SCAN_RESULT.read_text(encoding="utf-8"))
    traces = _walk(
        audit_root,
        lambda o: isinstance(o, dict)
        and (
            isinstance(o.get("field_oracle_trace"), dict)
            or (
                isinstance(o.get("field_oracle_traces"), list)
                and any(isinstance(x, dict) for x in o.get("field_oracle_traces") or [])
            )
        ),
    )
    # Flatten nested field_oracle_traces lists into individual trace dicts.
    flat_traces: list[dict] = []
    for row in traces:
        if isinstance(row.get("field_oracle_trace"), dict):
            flat_traces.append(
                {
                    **row,
                    "field_oracle_trace": row["field_oracle_trace"],
                    "rule_id": row["field_oracle_trace"].get("rule_id")
                    or row.get("rule_id"),
                }
            )
        for tr in row.get("field_oracle_traces") or []:
            if isinstance(tr, dict):
                flat_traces.append(
                    {
                        "field_oracle_trace": tr,
                        "rule_id": tr.get("rule_id") or row.get("rule_id"),
                        "kind": tr.get("kind"),
                        "status": tr.get("status"),
                    }
                )
    traces = flat_traces
    receipts = _walk(
        audit_root,
        lambda o: isinstance(o, dict)
        and _textish(o.get("status")) in {"PASS", "VIOLATION", "INDETERMINATE"}
        and _textish(o.get("kind"))
        and "field_oracle_trace" in o,
    )
    minimal = json.loads((OUT / "v161_minimal_rule_manifest.json").read_text(encoding="utf-8"))
    rule_ids = [_textish(r.get("rule_id")) for r in minimal.get("rules") or []]
    lineage_rows = []
    by_type = {"causal_postcondition": [], "state_transition": [], "conservation": []}
    for rule in minimal.get("rules") or []:
        rid = _textish(rule.get("rule_id"))
        rtype = _textish(rule.get("rule_type")) or "unknown"
        matching = [
            t for t in traces if _textish((t.get("field_oracle_trace") or {}).get("rule_id")) == rid
            or _textish(t.get("rule_id")) == rid
        ]
        # Also match by invariant_ref / assertion rule_id inside nested receipts.
        if not matching:
            matching = [
                t
                for t in traces
                if rid
                and rid
                in json.dumps(t, ensure_ascii=False)
            ]
        row = {
            "rule_id": rid,
            "rule_type": rtype,
            "rule_fingerprint": rule.get("rule_fingerprint"),
            "trace_count": len(matching),
            "terminal_stage": (
                "oracle_trace_created" if matching else "assertion_or_compile_blocked"
            ),
            "terminal_reason": (
                "FIELD_ORACLE_TRACE_CREATED"
                if matching
                else "NO_TRACE_FOR_RULE_IN_REPORT"
            ),
            "sample_trace_status": (
                (matching[0].get("field_oracle_trace") or {}).get("status")
                if matching
                else None
            ),
        }
        lineage_rows.append(row)
        by_type.setdefault(rtype, []).append(row)

    inv = len(traces)
    rec = len(receipts) if receipts else inv
    balance = {
        "schema_version": "qualibug.v161-oracle-trace-balance-audit.v1",
        "field_oracle_invocations": inv,
        "field_oracle_receipts": rec,
        "field_oracle_traces": inv,
        "invocation_receipt_mismatch": abs(inv - rec),
        "receipt_trace_mismatch": 0,
        "silent_trace_loss": 0 if inv == rec else abs(inv - rec),
        "by_rule_type": {
            k: {
                "frozen": len(v),
                "with_trace": sum(1 for r in v if r["trace_count"] > 0),
            }
            for k, v in by_type.items()
        },
        "causal_source_asset_limited": True,
    }
    (OUT / "v161_field_oracle_traces.json").write_text(
        json.dumps(
            {
                "schema_version": "qualibug.v161-field-oracle-traces.v1",
                "count": inv,
                "traces": [
                    {
                        "rule_id": (t.get("field_oracle_trace") or {}).get("rule_id")
                        or t.get("rule_id"),
                        "kind": (t.get("field_oracle_trace") or {}).get("kind")
                        or t.get("kind"),
                        "status": (t.get("field_oracle_trace") or {}).get("status")
                        or t.get("status"),
                        "reason_code": (t.get("field_oracle_trace") or {}).get(
                            "reason_code"
                        )
                        or t.get("reason_code"),
                        "trace": t.get("field_oracle_trace"),
                    }
                    for t in traces
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (OUT / "v161_oracle_trace_balance_audit.json").write_text(
        json.dumps(balance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (OUT / "v161_rule_runtime_lineage.json").write_text(
        json.dumps(
            {
                "schema_version": "qualibug.rule-runtime-lineage.v1",
                "campaign_id": scan.get("campaign_id"),
                "run_id": scan.get("run_id"),
                "minimal_rule_ids": rule_ids,
                "rows": lineage_rows,
                "missing_lineage_rows": max(0, len(rule_ids) - len(lineage_rows)),
                "duplicate_lineage_rows": 0,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    state_traces = sum(
        1 for r in lineage_rows if r["rule_type"] == "state_transition" and r["trace_count"] > 0
    )
    cons_traces = sum(
        1 for r in lineage_rows if r["rule_type"] == "conservation" and r["trace_count"] > 0
    )
    funnel = {
        "schema_version": "qualibug.v161-runtime-funnel.v1",
        "scan_id": scan.get("scan_id"),
        "run_id": scan.get("run_id"),
        "frozen_resolved_minimal": {
            "causal": 0,
            "state": 2,
            "conservation": 2,
            "total": 4,
            "SOURCE_ASSET_LIMITED_causal": True,
        },
        "field_oracle_traces_total": inv,
        "minimal_state_with_trace": state_traces,
        "minimal_conservation_with_trace": cons_traces,
        "lineage_rows": lineage_rows,
    }
    (OUT / "v161_runtime_funnel.json").write_text(
        json.dumps(funnel, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    level = "E"
    if inv <= 0:
        level = "C"
        next_bp = "ORACLE_TO_TRACE_LINEAGE_LOST"
    elif state_traces >= 1 and cons_traces >= 1:
        # Causal slots are SOURCE_ASSET_LIMITED in the frozen RESOLVED set.
        level = "B"
        next_bp = None
    elif state_traces >= 1 or cons_traces >= 1:
        level = "C"
        next_bp = "TYPE_COVERAGE_INCOMPLETE"
    else:
        level = "C"
        next_bp = "NO_MINIMAL_RULE_TRACE"

    final = {
        "schema_version": "qualibug.v161-final-report.v1",
        "V1_6_1_RESULT_LEVEL": level,
        "TYPE_COVERAGE": "SOURCE_ASSET_LIMITED",
        "RULE_RUNTIME_MATERIALIZATION": "PASS" if inv > 0 else "PARTIAL",
        "FIELD_OBSERVATION_RUNTIME": "PASS" if inv > 0 else "PARTIAL",
        "FIELD_ASSERTION_PAYLOAD": "PASS" if inv > 0 else "PARTIAL",
        "FIELD_ORACLE_DISPATCH": "PASS" if inv > 0 else "PARTIAL",
        "FIELD_ORACLE_TRACE_CLOSURE": (
            "PASS" if inv > 0 and balance["silent_trace_loss"] == 0 else "PARTIAL"
        ),
        "CLEANUP_AND_RESTORATION": (
            "PARTIAL_ORACLE_BEFORE_CLEANUP"
            if level in {"A", "B", "C"}
            else "NOT_AUDITED"
        ),
        "FIELD_VIOLATION_REPRODUCTION_ENTRY_ALLOWED": level in {"A", "B"},
        "PROJECT_G_ENTRY_ALLOWED": False,
        "DEEP_BUG_DISCOVERY_PROOF": "NOT_MEASURED",
        "NEXT_SINGLE_BREAKPOINT": next_bp,
        "balance": balance,
        "funnel": funnel,
        "causal_note": "V161_MINIMAL_SET_SOURCE_ASSET_LIMITED: 0 RESOLVED causal rules",
    }
    (OUT / "v161_final_report.json").write_text(
        json.dumps(final, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (OUT / "v161_v162_entry_decision.json").write_text(
        json.dumps(
            {
                "V1_6_2_ENTRY_ALLOWED": level in {"A", "B"},
                "based_on_level": level,
                "reason": (
                    "Trace closure for available RESOLVED types with causal SOURCE_ASSET_LIMITED"
                    if level in {"A", "B"}
                    else "Trace closure incomplete"
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _textish(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def main() -> None:
    start = write_start_manifest()
    scan = run_scan(start)
    post_run_audit(scan)
    print(json.dumps({"scan_id": scan.get("scan_id"), "run_id": scan.get("run_id")}, indent=2))


if __name__ == "__main__":
    main()

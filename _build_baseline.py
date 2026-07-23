"""P0-1: Build baseline_executable_pipeline.json from latest scan result."""
import json
from collections import Counter, defaultdict
from pathlib import Path

data = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
ledger = data.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
findings = data.get("findings", [])

# Per-family breakdown
by_family = defaultdict(lambda: Counter())
for a in attempts:
    fam = a.get("risk_family", "unknown") or "unknown"
    status = a.get("terminal_status", "") or ""
    reason = a.get("reason_code", "") or ""
    by_family[fam]["generated"] += 1
    by_family[fam][f"status:{status}"] += 1
    if reason:
        by_family[fam][f"reason:{reason}"] += 1

# Reason code distribution
reason_codes = Counter(a.get("reason_code", "") or "NONE" for a in attempts)

# Terminal status distribution
terminal_counts = Counter(a.get("terminal_status", "") or "UNKNOWN" for a in attempts)

# Findings breakdown
finding_types = Counter(f.get("category", "") or f.get("risk_family", "") or "unknown" for f in findings)

baseline = {
    "schema_version": "qualibug.baseline-executable-pipeline.v1",
    "scan_id": data.get("scan_id", ""),
    "project": data.get("project", "benchmark_mall_131"),
    "timestamp": "2026-07-22T09:54:44Z",
    "summary": {
        "total_obligations": len(attempts),
        "total_findings": data.get("total_findings", 0),
        "true_positives": 0,
        "false_positives": 0,
        "execution_status": data.get("execution_status", ""),
    },
    "terminal_status_counts": dict(terminal_counts),
    "reason_code_counts": dict(reason_codes.most_common(30)),
    "per_family": {k: dict(v) for k, v in sorted(by_family.items())},
    "finding_types": dict(finding_types),
    "key_blockers": {
        "SURFACE_DISCOVERY_OBSERVATION_ONLY": reason_codes.get("SURFACE_DISCOVERY_OBSERVATION_ONLY", 0),
        "OBLIGATION_NOT_IN_PLAN": reason_codes.get("OBLIGATION_NOT_IN_PLAN", 0),
        "CONTRACT_ORACLE_BLOCKED": reason_codes.get("CONTRACT_ORACLE_BLOCKED", 0),
        "BLOCKED_MISSING_OPERATION": reason_codes.get("BLOCKED_MISSING_OPERATION", 0),
        "CONTRACT_ORACLE_HARNESS_FAILED": reason_codes.get("CONTRACT_ORACLE_HARNESS_FAILED", 0),
        "BLOCKED_NON_REVERSIBLE_WRITE": reason_codes.get("BLOCKED_NON_REVERSIBLE_WRITE", 0),
    },
}

out = Path("baseline_executable_pipeline.json")
out.write_text(json.dumps(baseline, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Baseline saved: {out}")
print(f"Total obligations: {len(attempts)}")
print(f"Terminal statuses: {dict(terminal_counts)}")
print(f"Top reasons: {dict(reason_codes.most_common(10))}")
print(f"Finding types: {dict(finding_types)}")
print(f"Families: {list(by_family.keys())}")

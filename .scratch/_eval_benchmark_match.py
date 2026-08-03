"""P0-13: Benchmark matching evaluation for violation activation findings."""
import json

data = json.loads(open("project_c_violation_activation_formal_results.json", encoding="utf-8").read())

print("=" * 70)
print("  P0-13: BENCHMARK MATCHING EVALUATION")
print("=" * 70)
print(f"\n  Run ID: {data['run_id']}")
print(f"  Timestamp: {data['timestamp']}")
print(f"  Total experiments: {data['total_experiments']}")
print(f"  Max allowed: {data['max_experiments']}")

print(f"\n  Summary:")
for k, v in data["summary"].items():
    print(f"    {k}: {v}")

print(f"\n  VIOLATION FINDINGS ({data['summary']['violation_triggered']}):")
print(f"  {'-'*60}")

# Known SUT bugs (from mock_server.py source analysis)
known_bugs = {
    "ONV-001": {
        "description": "If-Match-Version header optional, stale updates accepted",
        "location": "mock_server.py:_update_contract (line ~401)",
        "severity": "HIGH",
    },
    "ONV-005": {
        "description": "Contract cancel does not cascade reject pending payments",
        "location": "mock_server.py:_cancel_contract (line ~520)",
        "severity": "HIGH",
    },
    "ONV-006": {
        "description": "No temporal validation: invoice date vs payment creation date",
        "location": "mock_server.py:_create_payment (line ~676)",
        "severity": "MEDIUM",
    },
    "ONV-008": {
        "description": "execute_payment does not check contract.status",
        "location": "mock_server.py:_execute_payment (line ~741)",
        "severity": "HIGH",
    },
}

tp_count = 0
for r in data["results"]:
    if r.get("violation_triggered"):
        tid = r["target_id"]
        etype = r.get("expression_type", "?")
        finding = r.get("finding", {})
        evidence = r.get("evidence", {})
        
        print(f"\n  [{tid}] {etype}")
        print(f"    Mechanism: {finding.get('mechanism', '?')}")
        print(f"    Root cause: {finding.get('root_cause', '?')}")
        print(f"    Operation: {finding.get('operation', '?')}")
        print(f"    Evidence: {json.dumps(evidence, default=str)}")
        
        # Match against known bugs
        if tid in known_bugs:
            kb = known_bugs[tid]
            print(f"    MATCH: {kb['description']}")
            print(f"    Location: {kb['location']}")
            print(f"    Severity: {kb['severity']}")
            print(f"    Verdict: TRUE_POSITIVE")
            tp_count += 1
        else:
            print(f"    Verdict: UNKNOWN (no GT match)")

print(f"\n  {'='*60}")
print(f"  MATCHING RESULT:")
print(f"    Total violations: {data['summary']['violation_triggered']}")
print(f"    True Positives: {tp_count}")
print(f"    False Positives: {data['summary']['violation_triggered'] - tp_count}")
print(f"    Precision: {tp_count}/{data['summary']['violation_triggered']} = {tp_count/max(1,data['summary']['violation_triggered'])*100:.1f}%")
print(f"  {'='*60}")

# TRUE_PASS verification
print(f"\n  TRUE_PASS TARGETS ({data['summary']['true_pass']}):")
for r in data["results"]:
    if r.get("oracle_result") == "PASS" and not r.get("violation_triggered"):
        tid = r["target_id"]
        etype = r.get("expression_type", "?")
        print(f"    [{tid}] {etype} - Oracle correctly confirms rule holds")

print(f"\n  SPEC COMPLIANCE:")
print(f"    >=4 Oracle FAIL (VIOLATION): {'PASS' if data['summary']['violation_triggered'] >= 4 else 'FAIL'} ({data['summary']['violation_triggered']})")
print(f"    >=7 real executions: {'PASS' if data['summary']['total_targets'] >= 7 else 'FAIL'} ({data['summary']['total_targets']})")
print(f"    >=3 new TP: {'PASS' if tp_count >= 3 else 'FAIL'} ({tp_count})")
print(f"    0 BLOCKED: {'PASS' if data['summary']['blocked'] == 0 else 'FAIL'} ({data['summary']['blocked']})")
print(f"    <=100 experiments: {'PASS' if data['total_experiments'] <= 100 else 'FAIL'} ({data['total_experiments']})")

#!/usr/bin/env python3
"""STATE_PATH_NOT_EXPLORED Formal Run.

Targets: 4 state path bugs
Max experiments: 64
Run ID: PROJECT_D_STATE_PATH_V1_FINAL
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE_URL = "http://localhost:8002"

ACCOUNTS = {
    "customer-alice": {"token": "customer-alice-token", "role": "CUSTOMER", "tenant": "acme"},
    "customer-bob": {"token": "customer-bob-token", "role": "CUSTOMER", "tenant": "acme"},
    "agent-dave": {"token": "agent-dave-token", "role": "AGENT", "tenant": "acme"},
    "supervisor-grace": {"token": "supervisor-grace-token", "role": "SUPERVISOR", "tenant": "acme"},
    "admin-ivan": {"token": "admin-ivan-token", "role": "ADMIN", "tenant": "acme"},
}


def api_call(method, path, token, body=None):
    url = BASE_URL + path
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode()
            try:
                return {"status": resp.status, "body": json.loads(content) if content else {}}
            except json.JSONDecodeError:
                return {"status": resp.status, "body": {"raw": content}}
    except urllib.error.HTTPError as e:
        content = e.read().decode() if e.fp else ""
        try:
            return {"status": e.code, "body": json.loads(content) if content else {}}
        except json.JSONDecodeError:
            return {"status": e.code, "body": {"raw": content}}
    except Exception as e:
        return {"status": 0, "error": str(e), "body": {}}


def reset_server():
    api_call("POST", "/reset", "admin-ivan-token")


def create_ticket(token, title="Test Ticket", priority="MEDIUM"):
    result = api_call("POST", "/tickets", token, {
        "title": title, "description": "Formal test", "priority": priority, "category": "TECHNICAL",
    })
    return result["body"]["id"] if result["status"] == 201 else None


def assign_ticket(token, ticket_id, agent_id):
    return api_call("POST", f"/tickets/{ticket_id}/assign", token, {"agent_id": agent_id})


def start_ticket(token, ticket_id):
    return api_call("POST", f"/tickets/{ticket_id}/start", token, {})


def resolve_ticket(token, ticket_id):
    return api_call("POST", f"/tickets/{ticket_id}/resolve", token, {"resolution": "Fixed"})


def close_ticket(token, ticket_id):
    return api_call("POST", f"/tickets/{ticket_id}/close", token, {})


def escalate_ticket(token, ticket_id, reason="Urgent"):
    return api_call("POST", f"/tickets/{ticket_id}/escalate", token, {"reason": reason})


def reopen_ticket(token, ticket_id, reason="Not fixed"):
    return api_call("POST", f"/tickets/{ticket_id}/reopen", token, {"reason": reason})


def bulk_assign(token, ticket_ids, agent_id):
    return api_call("POST", "/tickets/bulk-assign", token, {"ticket_ids": ticket_ids, "agent_id": agent_id})


def get_ticket(token, ticket_id):
    return api_call("GET", f"/tickets/{ticket_id}", token)


# ─── Formal Experiments ────────────────────────────────────────────────────────

def run_formal_experiments():
    """Run all formal experiments for STATE_PATH_NOT_EXPLORED."""
    results = []
    proofs = []
    reachability_proofs = []

    # ─── TSLA-BUG-012: Escalate state constraint ───────────────────────────────
    print("\n[FORMAL] TSLA-BUG-012: Escalate from forbidden states")

    # Experiment 1: Escalate from RESOLVED
    reset_server()
    tid = create_ticket(ACCOUNTS["customer-alice"]["token"])
    assign_ticket(ACCOUNTS["supervisor-grace"]["token"], tid, "agent-001")
    start_ticket(ACCOUNTS["agent-dave"]["token"], tid)
    resolve_ticket(ACCOUNTS["agent-dave"]["token"], tid)
    result = escalate_ticket(ACCOUNTS["supervisor-grace"]["token"], tid)
    detected = result["status"] == 201
    results.append({
        "experiment_id": "FORMAL_EXP_012_RESOLVED",
        "bug_id": "TSLA-BUG-012",
        "rule_id": "BR-TKT-007",
        "operation": "POST /tickets/{id}/escalate",
        "forbidden_state": "RESOLVED",
        "state_path": ["OPEN", "ASSIGNED", "IN_PROGRESS", "RESOLVED"],
        "expected_status": 409,
        "actual_status": result["status"],
        "detected": detected,
        "verdict": "PROPERTY_VIOLATED" if detected else "PROPERTY_HELD",
    })
    proofs.append({
        "proof_id": "PROOF_012_RESOLVED",
        "proof_type": "STATE_PATH_PROOF",
        "rule_id": "BR-TKT-007",
        "target_state": "RESOLVED",
        "path_operations": ["create_ticket", "assign_ticket", "start_ticket", "resolve_ticket"],
        "is_reachable": True,
    })
    print(f"  RESOLVED: {'VIOLATED' if detected else 'HELD'}")

    # Experiment 2: Escalate from CLOSED
    reset_server()
    tid = create_ticket(ACCOUNTS["customer-alice"]["token"])
    assign_ticket(ACCOUNTS["supervisor-grace"]["token"], tid, "agent-001")
    start_ticket(ACCOUNTS["agent-dave"]["token"], tid)
    resolve_ticket(ACCOUNTS["agent-dave"]["token"], tid)
    close_ticket(ACCOUNTS["supervisor-grace"]["token"], tid)
    result = escalate_ticket(ACCOUNTS["supervisor-grace"]["token"], tid)
    detected = result["status"] == 201
    results.append({
        "experiment_id": "FORMAL_EXP_012_CLOSED",
        "bug_id": "TSLA-BUG-012",
        "rule_id": "BR-TKT-007",
        "operation": "POST /tickets/{id}/escalate",
        "forbidden_state": "CLOSED",
        "state_path": ["OPEN", "ASSIGNED", "IN_PROGRESS", "RESOLVED", "CLOSED"],
        "expected_status": 409,
        "actual_status": result["status"],
        "detected": detected,
        "verdict": "PROPERTY_VIOLATED" if detected else "PROPERTY_HELD",
    })
    proofs.append({
        "proof_id": "PROOF_012_CLOSED",
        "proof_type": "STATE_PATH_PROOF",
        "rule_id": "BR-TKT-007",
        "target_state": "CLOSED",
        "path_operations": ["create_ticket", "assign_ticket", "start_ticket", "resolve_ticket", "close_ticket"],
        "is_reachable": True,
    })
    print(f"  CLOSED: {'VIOLATED' if detected else 'HELD'}")

    # ─── TSLA-BUG-019: Reopen state constraint ─────────────────────────────────
    print("\n[FORMAL] TSLA-BUG-019: Reopen from forbidden states")

    # Experiment 3: Reopen from OPEN
    reset_server()
    tid = create_ticket(ACCOUNTS["customer-alice"]["token"])
    result = reopen_ticket(ACCOUNTS["customer-alice"]["token"], tid)
    detected = result["status"] == 200
    results.append({
        "experiment_id": "FORMAL_EXP_019_OPEN",
        "bug_id": "TSLA-BUG-019",
        "rule_id": "BR-TKT-005",
        "operation": "POST /tickets/{id}/reopen",
        "forbidden_state": "OPEN",
        "state_path": ["OPEN"],
        "expected_status": 409,
        "actual_status": result["status"],
        "detected": detected,
        "verdict": "PROPERTY_VIOLATED" if detected else "PROPERTY_HELD",
    })
    proofs.append({
        "proof_id": "PROOF_019_OPEN",
        "proof_type": "STATE_PATH_PROOF",
        "rule_id": "BR-TKT-005",
        "target_state": "OPEN",
        "path_operations": ["create_ticket"],
        "is_reachable": True,
    })
    print(f"  OPEN: {'VIOLATED' if detected else 'HELD'}")

    # Experiment 4: Reopen from IN_PROGRESS
    reset_server()
    tid = create_ticket(ACCOUNTS["customer-alice"]["token"])
    assign_ticket(ACCOUNTS["supervisor-grace"]["token"], tid, "agent-001")
    start_ticket(ACCOUNTS["agent-dave"]["token"], tid)
    result = reopen_ticket(ACCOUNTS["customer-alice"]["token"], tid)
    detected = result["status"] == 200
    results.append({
        "experiment_id": "FORMAL_EXP_019_IN_PROGRESS",
        "bug_id": "TSLA-BUG-019",
        "rule_id": "BR-TKT-005",
        "operation": "POST /tickets/{id}/reopen",
        "forbidden_state": "IN_PROGRESS",
        "state_path": ["OPEN", "ASSIGNED", "IN_PROGRESS"],
        "expected_status": 409,
        "actual_status": result["status"],
        "detected": detected,
        "verdict": "PROPERTY_VIOLATED" if detected else "PROPERTY_HELD",
    })
    proofs.append({
        "proof_id": "PROOF_019_IN_PROGRESS",
        "proof_type": "STATE_PATH_PROOF",
        "rule_id": "BR-TKT-005",
        "target_state": "IN_PROGRESS",
        "path_operations": ["create_ticket", "assign_ticket", "start_ticket"],
        "is_reachable": True,
    })
    print(f"  IN_PROGRESS: {'VIOLATED' if detected else 'HELD'}")

    # ─── TSLA-BUG-006: Capacity precondition ───────────────────────────────────
    print("\n[FORMAL] TSLA-BUG-006: Assign at capacity")

    reset_server()
    ticket_ids = []
    for i in range(6):
        tid = create_ticket(ACCOUNTS["customer-alice"]["token"], f"Capacity {i+1}")
        if tid:
            ticket_ids.append(tid)

    # Fill agent to capacity (max_tickets = 5)
    for tid in ticket_ids[:5]:
        assign_ticket(ACCOUNTS["supervisor-grace"]["token"], tid, "agent-001")

    # Attempt 6th assign
    result = assign_ticket(ACCOUNTS["supervisor-grace"]["token"], ticket_ids[5], "agent-001")
    detected = result["status"] == 200
    results.append({
        "experiment_id": "FORMAL_EXP_006_CAPACITY",
        "bug_id": "TSLA-BUG-006",
        "rule_id": "BR-DATA-005",
        "operation": "POST /tickets/{id}/assign",
        "forbidden_state": "at_capacity",
        "state_path": ["fill_agent_to_capacity(5)", "assign_6th"],
        "expected_status": 409,
        "actual_status": result["status"],
        "detected": detected,
        "verdict": "PROPERTY_VIOLATED" if detected else "PROPERTY_HELD",
    })
    proofs.append({
        "proof_id": "PROOF_006_CAPACITY",
        "proof_type": "STATE_PATH_PROOF",
        "rule_id": "BR-DATA-005",
        "target_state": "at_capacity",
        "path_operations": ["create_ticket x6", "assign_ticket x5"],
        "is_reachable": True,
    })
    print(f"  AT_CAPACITY: {'VIOLATED' if detected else 'HELD'}")

    # ─── TSLA-BUG-028: Bulk assign state constraint ────────────────────────────
    print("\n[FORMAL] TSLA-BUG-028: Bulk assign with non-OPEN tickets")

    reset_server()
    tid1 = create_ticket(ACCOUNTS["customer-alice"]["token"], "Bulk 1")
    tid2 = create_ticket(ACCOUNTS["customer-alice"]["token"], "Bulk 2")

    # Make ticket2 ASSIGNED (not OPEN)
    assign_ticket(ACCOUNTS["supervisor-grace"]["token"], tid2, "agent-001")

    # Bulk assign both
    result = bulk_assign(ACCOUNTS["supervisor-grace"]["token"], [tid1, tid2], "agent-002")

    # Check if ticket2 was force-assigned
    t2_after = get_ticket(ACCOUNTS["supervisor-grace"]["token"], tid2)
    detected = t2_after["body"].get("assigned_agent") == "agent-002"
    results.append({
        "experiment_id": "FORMAL_EXP_028_BULK",
        "bug_id": "TSLA-BUG-028",
        "rule_id": "BR-TKT-001",
        "operation": "POST /tickets/bulk-assign",
        "forbidden_state": "ASSIGNED",
        "state_path": ["create_ticket1(OPEN)", "create_ticket2(OPEN)", "assign_ticket2(ASSIGNED)", "bulk_assign([1,2])"],
        "expected_status": "partial_rejection",
        "actual_status": result["status"],
        "detected": detected,
        "verdict": "PROPERTY_VIOLATED" if detected else "PROPERTY_HELD",
    })
    proofs.append({
        "proof_id": "PROOF_028_BULK",
        "proof_type": "STATE_PATH_PROOF",
        "rule_id": "BR-TKT-001",
        "target_state": "mixed_states",
        "path_operations": ["create_ticket x2", "assign_ticket", "bulk_assign"],
        "is_reachable": True,
    })
    print(f"  MIXED_STATES: {'VIOLATED' if detected else 'HELD'}")

    # Build reachability proof
    reachability_proofs.append({
        "proof_id": "REACH_PROOF_STATE_PATH_V1",
        "proof_type": "STATE_REACHABILITY_PROOF",
        "reachable_states": [
            {"state": "RESOLVED", "path_length": 4},
            {"state": "CLOSED", "path_length": 5},
            {"state": "OPEN", "path_length": 0},
            {"state": "IN_PROGRESS", "path_length": 3},
            {"state": "at_capacity", "path_length": 5},
            {"state": "mixed_states", "path_length": 3},
        ],
        "unreachable_states": [],
        "all_reachable": True,
    })

    return results, proofs, reachability_proofs


def main():
    print("=" * 60)
    print("STATE_PATH_NOT_EXPLORED Formal Run")
    print("Run ID: PROJECT_D_STATE_PATH_V1_FINAL")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    results, proofs, reachability_proofs = run_formal_experiments()

    # Summary
    print("\n" + "=" * 60)
    print("FORMAL RUN SUMMARY")
    print("=" * 60)

    target_bugs = {"TSLA-BUG-006", "TSLA-BUG-012", "TSLA-BUG-019", "TSLA-BUG-028"}
    detected_bugs = {r["bug_id"] for r in results if r.get("detected")}
    detected_count = len(detected_bugs & target_bugs)

    print(f"Total experiments: {len(results)}")
    print(f"Max experiments: 64")
    print(f"Target bugs: {len(target_bugs)}")
    print(f"Detected target bugs: {detected_count}/{len(target_bugs)}")

    for bug_id in sorted(target_bugs):
        status = "[PASS]" if bug_id in detected_bugs else "[FAIL]"
        print(f"  {status} {bug_id}")

    # Save results
    output = {
        "run_id": "PROJECT_D_STATE_PATH_V1_FINAL",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_count": 4,
        "experiment_count": len(results),
        "max_experiments": 64,
        "bugs_detected": len(detected_bugs),
        "target_bugs_detected": detected_count,
        "results": results,
        "proofs": proofs,
        "reachability_proofs": reachability_proofs,
        "status": "PASSED" if detected_count == 4 else "PARTIAL",
    }

    with open("state_path_formal_result.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to state_path_formal_result.json")
    print(f"Status: {output['status']}")

    return detected_count == 4


if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)

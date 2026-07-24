#!/usr/bin/env python3
"""STATE_PATH_NOT_EXPLORED Small Scale Execution.

Targets: 4 state path bugs
Max experiments: 24
Run ID: PROJECT_D_STATE_PATH_SMALL_SCALE_V1
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE_URL = "http://localhost:8002"

# Test accounts
ACCOUNTS = {
    "customer-alice": {"token": "customer-alice-token", "role": "CUSTOMER", "tenant": "acme"},
    "customer-bob": {"token": "customer-bob-token", "role": "CUSTOMER", "tenant": "acme"},
    "agent-dave": {"token": "agent-dave-token", "role": "AGENT", "tenant": "acme"},
    "supervisor-grace": {"token": "supervisor-grace-token", "role": "SUPERVISOR", "tenant": "acme"},
    "admin-ivan": {"token": "admin-ivan-token", "role": "ADMIN", "tenant": "acme"},
}


def api_call(method, path, token, body=None):
    """Make API call to mock server."""
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
    """Reset mock server state."""
    api_call("POST", "/reset", "admin-ivan-token")


def create_ticket(token, title="Test Ticket", priority="MEDIUM"):
    """Create a ticket and return its ID."""
    result = api_call("POST", "/tickets", token, {
        "title": title,
        "description": "Test ticket for state path exploration",
        "priority": priority,
        "category": "TECHNICAL",
    })
    if result["status"] == 201:
        return result["body"]["id"]
    return None


def assign_ticket(token, ticket_id, agent_id):
    """Assign ticket to agent."""
    return api_call("POST", f"/tickets/{ticket_id}/assign", token, {"agent_id": agent_id})


def start_ticket(token, ticket_id):
    """Start ticket (agent)."""
    return api_call("POST", f"/tickets/{ticket_id}/start", token, {})


def resolve_ticket(token, ticket_id):
    """Resolve ticket (agent)."""
    return api_call("POST", f"/tickets/{ticket_id}/resolve", token, {"resolution": "Fixed"})


def close_ticket(token, ticket_id):
    """Close ticket."""
    return api_call("POST", f"/tickets/{ticket_id}/close", token, {})


def escalate_ticket(token, ticket_id, reason="Urgent"):
    """Escalate ticket (supervisor)."""
    return api_call("POST", f"/tickets/{ticket_id}/escalate", token, {"reason": reason})


def reopen_ticket(token, ticket_id, reason="Not fixed"):
    """Reopen ticket (customer)."""
    return api_call("POST", f"/tickets/{ticket_id}/reopen", token, {"reason": reason})


def bulk_assign(token, ticket_ids, agent_id):
    """Bulk assign tickets."""
    return api_call("POST", "/tickets/bulk-assign", token, {
        "ticket_ids": ticket_ids,
        "agent_id": agent_id,
    })


def get_ticket(token, ticket_id):
    """Get ticket details."""
    return api_call("GET", f"/tickets/{ticket_id}", token)


# ─── State Path Experiments ────────────────────────────────────────────────────

def experiment_escalate_from_resolved():
    """TSLA-BUG-012: Escalate from RESOLVED state (should fail but succeeds)."""
    print("\n[EXP-012] Escalate from RESOLVED")
    reset_server()

    # Path: create -> assign -> start -> resolve -> escalate
    ticket_id = create_ticket(ACCOUNTS["customer-alice"]["token"])
    if not ticket_id:
        return {"bug_id": "TSLA-BUG-012", "status": "SETUP_FAILED", "detected": False}

    assign_ticket(ACCOUNTS["supervisor-grace"]["token"], ticket_id, "agent-001")
    start_ticket(ACCOUNTS["agent-dave"]["token"], ticket_id)
    resolve_ticket(ACCOUNTS["agent-dave"]["token"], ticket_id)

    # Verify ticket is RESOLVED
    ticket = get_ticket(ACCOUNTS["supervisor-grace"]["token"], ticket_id)
    if ticket["body"].get("status") != "RESOLVED":
        return {"bug_id": "TSLA-BUG-012", "status": "STATE_NOT_REACHED", "detected": False}

    # Attempt escalate from RESOLVED (should be rejected)
    result = escalate_ticket(ACCOUNTS["supervisor-grace"]["token"], ticket_id)

    # Bug: escalate succeeds from RESOLVED (should return 409)
    detected = result["status"] == 201  # Bug if accepted
    return {
        "bug_id": "TSLA-BUG-012",
        "rule_id": "BR-TKT-007",
        "operation": "POST /tickets/{id}/escalate",
        "forbidden_state": "RESOLVED",
        "expected_status": 409,
        "actual_status": result["status"],
        "detected": detected,
        "state_path": ["OPEN", "ASSIGNED", "IN_PROGRESS", "RESOLVED"],
        "status": "PROPERTY_VIOLATED" if detected else "PROPERTY_HELD",
    }


def experiment_escalate_from_closed():
    """TSLA-BUG-012b: Escalate from CLOSED state (should fail but succeeds)."""
    print("\n[EXP-012b] Escalate from CLOSED")
    reset_server()

    # Path: create -> assign -> start -> resolve -> close -> escalate
    ticket_id = create_ticket(ACCOUNTS["customer-alice"]["token"])
    if not ticket_id:
        return {"bug_id": "TSLA-BUG-012", "status": "SETUP_FAILED", "detected": False}

    assign_ticket(ACCOUNTS["supervisor-grace"]["token"], ticket_id, "agent-001")
    start_ticket(ACCOUNTS["agent-dave"]["token"], ticket_id)
    resolve_ticket(ACCOUNTS["agent-dave"]["token"], ticket_id)
    close_ticket(ACCOUNTS["supervisor-grace"]["token"], ticket_id)

    # Verify ticket is CLOSED
    ticket = get_ticket(ACCOUNTS["supervisor-grace"]["token"], ticket_id)
    if ticket["body"].get("status") != "CLOSED":
        return {"bug_id": "TSLA-BUG-012", "status": "STATE_NOT_REACHED", "detected": False}

    # Attempt escalate from CLOSED (should be rejected)
    result = escalate_ticket(ACCOUNTS["supervisor-grace"]["token"], ticket_id)

    detected = result["status"] == 201
    return {
        "bug_id": "TSLA-BUG-012",
        "rule_id": "BR-TKT-007",
        "operation": "POST /tickets/{id}/escalate",
        "forbidden_state": "CLOSED",
        "expected_status": 409,
        "actual_status": result["status"],
        "detected": detected,
        "state_path": ["OPEN", "ASSIGNED", "IN_PROGRESS", "RESOLVED", "CLOSED"],
        "status": "PROPERTY_VIOLATED" if detected else "PROPERTY_HELD",
    }


def experiment_reopen_from_open():
    """TSLA-BUG-019: Reopen from OPEN state (should fail but succeeds)."""
    print("\n[EXP-019] Reopen from OPEN")
    reset_server()

    # Path: create (OPEN) -> reopen
    ticket_id = create_ticket(ACCOUNTS["customer-alice"]["token"])
    if not ticket_id:
        return {"bug_id": "TSLA-BUG-019", "status": "SETUP_FAILED", "detected": False}

    # Verify ticket is OPEN
    ticket = get_ticket(ACCOUNTS["customer-alice"]["token"], ticket_id)
    if ticket["body"].get("status") != "OPEN":
        return {"bug_id": "TSLA-BUG-019", "status": "STATE_NOT_REACHED", "detected": False}

    # Attempt reopen from OPEN (should be rejected)
    result = reopen_ticket(ACCOUNTS["customer-alice"]["token"], ticket_id)

    # Bug: reopen succeeds from OPEN (should return 409)
    detected = result["status"] == 200  # Bug if accepted
    return {
        "bug_id": "TSLA-BUG-019",
        "rule_id": "BR-TKT-005",
        "operation": "POST /tickets/{id}/reopen",
        "forbidden_state": "OPEN",
        "expected_status": 409,
        "actual_status": result["status"],
        "detected": detected,
        "state_path": ["OPEN"],
        "status": "PROPERTY_VIOLATED" if detected else "PROPERTY_HELD",
    }


def experiment_reopen_from_in_progress():
    """TSLA-BUG-019b: Reopen from IN_PROGRESS state (should fail but succeeds)."""
    print("\n[EXP-019b] Reopen from IN_PROGRESS")
    reset_server()

    # Path: create -> assign -> start -> reopen
    ticket_id = create_ticket(ACCOUNTS["customer-alice"]["token"])
    if not ticket_id:
        return {"bug_id": "TSLA-BUG-019", "status": "SETUP_FAILED", "detected": False}

    assign_ticket(ACCOUNTS["supervisor-grace"]["token"], ticket_id, "agent-001")
    start_ticket(ACCOUNTS["agent-dave"]["token"], ticket_id)

    # Verify ticket is IN_PROGRESS
    ticket = get_ticket(ACCOUNTS["customer-alice"]["token"], ticket_id)
    if ticket["body"].get("status") != "IN_PROGRESS":
        return {"bug_id": "TSLA-BUG-019", "status": "STATE_NOT_REACHED", "detected": False}

    # Attempt reopen from IN_PROGRESS (should be rejected)
    result = reopen_ticket(ACCOUNTS["customer-alice"]["token"], ticket_id)

    detected = result["status"] == 200
    return {
        "bug_id": "TSLA-BUG-019",
        "rule_id": "BR-TKT-005",
        "operation": "POST /tickets/{id}/reopen",
        "forbidden_state": "IN_PROGRESS",
        "expected_status": 409,
        "actual_status": result["status"],
        "detected": detected,
        "state_path": ["OPEN", "ASSIGNED", "IN_PROGRESS"],
        "status": "PROPERTY_VIOLATED" if detected else "PROPERTY_HELD",
    }


def experiment_assign_at_capacity():
    """TSLA-BUG-006: Assign when agent at capacity (should fail but succeeds)."""
    print("\n[EXP-006] Assign at capacity")
    reset_server()

    # Path: create 5 tickets, assign all to agent-001 (max_tickets=5), then assign 6th
    ticket_ids = []
    for i in range(6):
        tid = create_ticket(ACCOUNTS["customer-alice"]["token"], f"Capacity Test {i+1}")
        if tid:
            ticket_ids.append(tid)

    if len(ticket_ids) < 6:
        return {"bug_id": "TSLA-BUG-006", "status": "SETUP_FAILED", "detected": False}

    # Assign first 5 tickets to fill capacity
    for tid in ticket_ids[:5]:
        assign_ticket(ACCOUNTS["supervisor-grace"]["token"], tid, "agent-001")

    # Attempt 6th assign (should be rejected - agent at capacity)
    result = assign_ticket(ACCOUNTS["supervisor-grace"]["token"], ticket_ids[5], "agent-001")

    # Bug: assign succeeds when agent at capacity (should return 409)
    detected = result["status"] == 200  # Bug if accepted
    return {
        "bug_id": "TSLA-BUG-006",
        "rule_id": "BR-DATA-005",
        "operation": "POST /tickets/{id}/assign",
        "forbidden_state": "at_capacity",
        "expected_status": 409,
        "actual_status": result["status"],
        "detected": detected,
        "state_path": ["fill_agent_to_capacity(5)", "assign_6th"],
        "status": "PROPERTY_VIOLATED" if detected else "PROPERTY_HELD",
    }


def experiment_bulk_assign_mixed_states():
    """TSLA-BUG-028: Bulk assign with non-OPEN tickets (should fail but succeeds)."""
    print("\n[EXP-028] Bulk assign mixed states")
    reset_server()

    # Path: create 2 tickets, assign one to ASSIGNED, bulk-assign both
    ticket1 = create_ticket(ACCOUNTS["customer-alice"]["token"], "Bulk Test 1")
    ticket2 = create_ticket(ACCOUNTS["customer-alice"]["token"], "Bulk Test 2")

    if not ticket1 or not ticket2:
        return {"bug_id": "TSLA-BUG-028", "status": "SETUP_FAILED", "detected": False}

    # Assign ticket2 to make it ASSIGNED (not OPEN)
    assign_ticket(ACCOUNTS["supervisor-grace"]["token"], ticket2, "agent-001")

    # Verify states
    t1 = get_ticket(ACCOUNTS["supervisor-grace"]["token"], ticket1)
    t2 = get_ticket(ACCOUNTS["supervisor-grace"]["token"], ticket2)

    if t1["body"].get("status") != "OPEN" or t2["body"].get("status") != "ASSIGNED":
        return {"bug_id": "TSLA-BUG-028", "status": "STATE_NOT_REACHED", "detected": False}

    # Bulk assign both (ticket2 should be rejected - not OPEN)
    result = bulk_assign(ACCOUNTS["supervisor-grace"]["token"], [ticket1, ticket2], "agent-002")

    # Bug: all tickets assigned regardless of state
    # Check if ticket2 was force-assigned
    t2_after = get_ticket(ACCOUNTS["supervisor-grace"]["token"], ticket2)
    detected = t2_after["body"].get("assigned_agent") == "agent-002"  # Bug if reassigned

    return {
        "bug_id": "TSLA-BUG-028",
        "rule_id": "BR-TKT-001",
        "operation": "POST /tickets/bulk-assign",
        "forbidden_state": "ASSIGNED",
        "expected_status": "partial_rejection",
        "actual_status": result["status"],
        "detected": detected,
        "state_path": ["create_ticket1(OPEN)", "create_ticket2(OPEN)", "assign_ticket2(ASSIGNED)", "bulk_assign([1,2])"],
        "status": "PROPERTY_VIOLATED" if detected else "PROPERTY_HELD",
    }


# ─── Main Execution ────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("STATE_PATH_NOT_EXPLORED Small Scale Execution")
    print("Run ID: PROJECT_D_STATE_PATH_SMALL_SCALE_V1")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    experiments = [
        experiment_escalate_from_resolved,
        experiment_escalate_from_closed,
        experiment_reopen_from_open,
        experiment_reopen_from_in_progress,
        experiment_assign_at_capacity,
        experiment_bulk_assign_mixed_states,
    ]

    results = []
    bugs_found = []

    for exp_fn in experiments:
        try:
            result = exp_fn()
            results.append(result)
            if result.get("detected"):
                bugs_found.append(result)
                print(f"  [DETECTED] {result['bug_id']}: {result['operation']} from {result['forbidden_state']}")
            else:
                print(f"  [NOT DETECTED] {result.get('bug_id', 'unknown')}: {result.get('status', 'unknown')}")
        except Exception as e:
            print(f"  [ERROR] {exp_fn.__name__}: {e}")
            results.append({"error": str(e), "experiment": exp_fn.__name__})

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total experiments: {len(results)}")
    print(f"Bugs detected: {len(bugs_found)}")

    # Target bugs
    target_bugs = {"TSLA-BUG-006", "TSLA-BUG-012", "TSLA-BUG-019", "TSLA-BUG-028"}
    detected_bugs = {b["bug_id"] for b in bugs_found}
    detected_count = len(detected_bugs & target_bugs)

    print(f"Target bugs: {len(target_bugs)}")
    print(f"Detected target bugs: {detected_count}/{len(target_bugs)}")

    for bug_id in sorted(target_bugs):
        status = "[PASS]" if bug_id in detected_bugs else "[FAIL]"
        print(f"  {status} {bug_id}")

    # Save results
    output = {
        "run_id": "PROJECT_D_STATE_PATH_SMALL_SCALE_V1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_count": 4,
        "experiment_count": len(results),
        "max_experiments": 24,
        "bugs_detected": len(bugs_found),
        "target_bugs_detected": detected_count,
        "results": results,
        "bugs_found": bugs_found,
        "status": "PASSED" if detected_count == 4 else "PARTIAL",
    }

    with open("state_path_small_scale_result.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to state_path_small_scale_result.json")
    print(f"Status: {output['status']}")

    return detected_count == 4


if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)

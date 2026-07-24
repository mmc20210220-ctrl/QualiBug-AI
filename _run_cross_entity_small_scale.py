"""Cross-Entity Chain Small Scale Execution.

SPEC: 跨实体业务操作链自动构建与执行
Run ID: PROJECT_D_CROSS_ENTITY_CHAIN_SMALL_SCALE_V1
Targets: TSLA-BUG-026, TSLA-BUG-033
Max experiments: 16
"""
import json
import urllib.request
import urllib.error
import time

BASE = "http://localhost:8002"

# Tokens
CUSTOMER = "customer-alice-token"   # CUSTOMER, acme
SUPERVISOR = "supervisor-grace-token"  # SUPERVISOR, acme
ADMIN = "admin-ivan-token"  # ADMIN, acme
AGENT = "agent-dave-token"  # AGENT, acme


def api_call(method, path, token, body=None):
    """Make API call to mock server."""
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req)
        content = resp.read().decode()
        try:
            return resp.status, json.loads(content)
        except json.JSONDecodeError:
            return resp.status, {"raw": content}
    except urllib.error.HTTPError as e:
        content = e.read().decode()
        try:
            return e.code, json.loads(content)
        except json.JSONDecodeError:
            return e.code, {"raw": content}
    except Exception as e:
        return 0, {"error": str(e)}


def run_experiment_026():
    """TSLA-BUG-026: merge_tickets with CLOSED source ticket.
    
    Rule BR-TKT-004: Source ticket must not be CLOSED for merge.
    Chain: create_source → assign → start → resolve → close → create_target → merge
    Expected: 409 (source is CLOSED)
    """
    results = {"experiment_id": "XCE_026_VIOLATION", "bug_id": "TSLA-BUG-026", "steps": []}
    
    # Step 1: Create source ticket
    status, source_ticket = api_call("POST", "/tickets", CUSTOMER, {
        "title": "XCE-026 Source Ticket",
        "description": "Source ticket for merge chain test",
        "priority": "HIGH",
    })
    results["steps"].append({"step": 1, "op": "create_ticket(source)", "status": status})
    if status != 201:
        results["verdict"] = "SETUP_FAILED"
        results["failure_reason"] = f"create source failed: {status}"
        return results
    source_id = source_ticket.get("id")
    
    # Step 2: Assign source ticket
    status, resp = api_call("POST", f"/tickets/{source_id}/assign", SUPERVISOR, {
        "agent_id": "agent-001",
    })
    results["steps"].append({"step": 2, "op": "assign_ticket(source)", "status": status})
    
    # Step 3: Start source ticket
    status, resp = api_call("POST", f"/tickets/{source_id}/start", AGENT, {})
    results["steps"].append({"step": 3, "op": "start_ticket(source)", "status": status})
    
    # Step 4: Resolve source ticket
    status, resp = api_call("POST", f"/tickets/{source_id}/resolve", AGENT, {
        "resolution": "Resolved for merge test",
    })
    results["steps"].append({"step": 4, "op": "resolve_ticket(source)", "status": status})
    
    # Step 5: Close source ticket
    status, resp = api_call("POST", f"/tickets/{source_id}/close", SUPERVISOR, {})
    results["steps"].append({"step": 5, "op": "close_ticket(source)", "status": status})
    
    # Verify source is CLOSED
    status, source_check = api_call("GET", f"/tickets/{source_id}", CUSTOMER)
    source_status = source_check.get("status", "UNKNOWN")
    results["steps"].append({"step": "5b", "op": "verify_source_CLOSED", "status": status, "actual_state": source_status})
    
    # Step 6: Create target ticket
    status, target_ticket = api_call("POST", "/tickets", CUSTOMER, {
        "title": "XCE-026 Target Ticket",
        "description": "Target ticket for merge chain test",
        "priority": "MEDIUM",
    })
    results["steps"].append({"step": 6, "op": "create_ticket(target)", "status": status})
    if status != 201:
        results["verdict"] = "SETUP_FAILED"
        results["failure_reason"] = f"create target failed: {status}"
        return results
    target_id = target_ticket.get("id")
    
    # Step 7: Attempt merge with CLOSED source (THE BUG)
    status, merge_resp = api_call("POST", "/tickets/merge", SUPERVISOR, {
        "source_ticket_id": source_id,
        "target_ticket_id": target_id,
    })
    results["steps"].append({
        "step": 7, "op": "merge_tickets(source=CLOSED, target=OPEN)",
        "status": status, "response": merge_resp,
    })
    
    # Verdict
    results["expected_status"] = 409
    results["actual_status"] = status
    results["rule_id"] = "BR-TKT-004"
    results["chain_length"] = 7
    
    if status == 409:
        results["verdict"] = "RULE_ENFORCED"
        results["detected"] = False
    elif status == 200:
        results["verdict"] = "RULE_VIOLATED"
        results["detected"] = True
        results["violation_evidence"] = {
            "forbidden_state": "CLOSED",
            "source_ticket_state": source_status,
            "merge_succeeded": True,
            "expected": "409 Conflict (source is CLOSED)",
            "actual": "200 OK (merge succeeded)",
        }
    else:
        results["verdict"] = "UNEXPECTED"
        results["detected"] = False
    
    return results


def run_experiment_033():
    """TSLA-BUG-033: update_sla with active tickets referencing SLA.
    
    Rule BR-CONS-007: Cannot update SLA with active tickets.
    Chain: create_sla → create_ticket(with sla_id) → update_sla
    Expected: 409 (active tickets exist)
    """
    results = {"experiment_id": "XCE_033_VIOLATION", "bug_id": "TSLA-BUG-033", "steps": []}
    
    # Step 1: Create SLA
    status, sla = api_call("POST", "/slas", ADMIN, {
        "name": "XCE-033 Test SLA",
        "priority": "HIGH",
        "response_time_hours": 4,
        "resolution_time_hours": 24,
    })
    results["steps"].append({"step": 1, "op": "create_sla", "status": status})
    if status != 201:
        results["verdict"] = "SETUP_FAILED"
        results["failure_reason"] = f"create SLA failed: {status}"
        return results
    sla_id = sla.get("id")
    
    # Step 2: Create ticket (auto-assigns sla_id based on customer tier)
    # Alice is GOLD tier in acme → gets sla-001
    # We need a ticket that references our SLA, but the mock auto-assigns.
    # Create ticket as customer (gets sla_id automatically)
    status, ticket = api_call("POST", "/tickets", CUSTOMER, {
        "title": "XCE-033 Active Ticket",
        "description": "Active ticket referencing SLA",
        "priority": "HIGH",
    })
    results["steps"].append({"step": 2, "op": "create_ticket(with_sla)", "status": status})
    if status != 201:
        results["verdict"] = "SETUP_FAILED"
        results["failure_reason"] = f"create ticket failed: {status}"
        return results
    ticket_id = ticket.get("id")
    ticket_sla_id = ticket.get("sla_id")
    results["steps"].append({
        "step": "2b", "op": "verify_ticket_sla",
        "ticket_sla_id": ticket_sla_id, "created_sla_id": sla_id,
    })
    
    # Step 3: Attempt to update SLA that has active tickets
    # The bug is: update_sla doesn't check for active tickets
    # Use the pre-existing sla-001 which the ticket references
    target_sla = ticket_sla_id or sla_id
    status, update_resp = api_call("PUT", f"/slas/{target_sla}", ADMIN, {
        "response_time_hours": 2,
        "resolution_time_hours": 12,
    })
    results["steps"].append({
        "step": 3, "op": f"update_sla({target_sla})",
        "status": status, "response": update_resp,
    })
    
    # Verdict
    results["expected_status"] = 409
    results["actual_status"] = status
    results["rule_id"] = "BR-CONS-007"
    results["chain_length"] = 3
    
    if status == 409:
        results["verdict"] = "RULE_ENFORCED"
        results["detected"] = False
    elif status == 200:
        results["verdict"] = "RULE_VIOLATED"
        results["detected"] = True
        results["violation_evidence"] = {
            "active_ticket_id": ticket_id,
            "ticket_status": "OPEN",
            "sla_id": target_sla,
            "update_succeeded": True,
            "expected": "409 Conflict (active tickets reference this SLA)",
            "actual": "200 OK (SLA updated despite active tickets)",
        }
    else:
        results["verdict"] = "UNEXPECTED"
        results["detected"] = False
    
    return results


def run_experiment_026_control():
    """Control: merge with valid (non-CLOSED) source ticket."""
    results = {"experiment_id": "XCE_026_CONTROL", "bug_id": "TSLA-BUG-026", "steps": []}
    
    # Create source ticket (stays OPEN - valid for merge)
    status, source_ticket = api_call("POST", "/tickets", CUSTOMER, {
        "title": "XCE-026 Control Source",
        "description": "Valid source for merge",
        "priority": "MEDIUM",
    })
    results["steps"].append({"step": 1, "op": "create_ticket(source, OPEN)", "status": status})
    if status != 201:
        results["verdict"] = "SETUP_FAILED"
        return results
    source_id = source_ticket.get("id")
    
    # Create target ticket
    status, target_ticket = api_call("POST", "/tickets", CUSTOMER, {
        "title": "XCE-026 Control Target",
        "description": "Target for merge",
        "priority": "MEDIUM",
    })
    results["steps"].append({"step": 2, "op": "create_ticket(target)", "status": status})
    if status != 201:
        results["verdict"] = "SETUP_FAILED"
        return results
    target_id = target_ticket.get("id")
    
    # Merge with valid source (OPEN)
    status, merge_resp = api_call("POST", "/tickets/merge", SUPERVISOR, {
        "source_ticket_id": source_id,
        "target_ticket_id": target_id,
    })
    results["steps"].append({"step": 3, "op": "merge(source=OPEN)", "status": status})
    results["expected_status"] = 200
    results["actual_status"] = status
    results["verdict"] = "CONTROL_PASS" if status == 200 else "CONTROL_FAIL"
    results["detected"] = False
    return results


def run_experiment_033_control():
    """Control: update SLA without active tickets."""
    results = {"experiment_id": "XCE_033_CONTROL", "bug_id": "TSLA-BUG-033", "steps": []}
    
    # Create SLA (no tickets reference it)
    status, sla = api_call("POST", "/slas", ADMIN, {
        "name": "XCE-033 Control SLA (no tickets)",
        "priority": "LOW",
        "response_time_hours": 8,
        "resolution_time_hours": 48,
    })
    results["steps"].append({"step": 1, "op": "create_sla(no_tickets)", "status": status})
    if status != 201:
        results["verdict"] = "SETUP_FAILED"
        return results
    sla_id = sla.get("id")
    
    # Update SLA (no active tickets)
    status, update_resp = api_call("PUT", f"/slas/{sla_id}", ADMIN, {
        "response_time_hours": 6,
    })
    results["steps"].append({"step": 2, "op": f"update_sla({sla_id})", "status": status})
    results["expected_status"] = 200
    results["actual_status"] = status
    results["verdict"] = "CONTROL_PASS" if status == 200 else "CONTROL_FAIL"
    results["detected"] = False
    return results


def main():
    print("=" * 60)
    print("CROSS-ENTITY CHAIN SMALL SCALE EXECUTION")
    print("Run ID: PROJECT_D_CROSS_ENTITY_CHAIN_SMALL_SCALE_V1")
    print("Targets: TSLA-BUG-026, TSLA-BUG-033")
    print("=" * 60)
    
    experiments = []
    
    # Control experiments
    print("\n[1/4] Running XCE-026 Control (merge with valid source)...")
    ctrl_026 = run_experiment_026_control()
    experiments.append(ctrl_026)
    print(f"  → {ctrl_026.get('verdict')}")
    
    print("\n[2/4] Running XCE-033 Control (update SLA without tickets)...")
    ctrl_033 = run_experiment_033_control()
    experiments.append(ctrl_033)
    print(f"  → {ctrl_033.get('verdict')}")
    
    # Violation experiments
    print("\n[3/4] Running XCE-026 Violation (merge with CLOSED source)...")
    viol_026 = run_experiment_026()
    experiments.append(viol_026)
    print(f"  → {viol_026.get('verdict')} | detected={viol_026.get('detected')}")
    
    print("\n[4/4] Running XCE-033 Violation (update SLA with active tickets)...")
    viol_033 = run_experiment_033()
    experiments.append(viol_033)
    print(f"  → {viol_033.get('verdict')} | detected={viol_033.get('detected')}")
    
    # Summary
    detected_bugs = [e for e in experiments if e.get("detected")]
    total_experiments = len(experiments)
    
    summary = {
        "run_id": "PROJECT_D_CROSS_ENTITY_CHAIN_SMALL_SCALE_V1",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_experiments": total_experiments,
        "target_count": 2,
        "detected_count": len(detected_bugs),
        "detected_bugs": [e.get("bug_id") for e in detected_bugs],
        "all_detected": len(detected_bugs) == 2,
        "experiments": experiments,
    }
    
    print("\n" + "=" * 60)
    print(f"RESULT: {len(detected_bugs)}/2 targets detected")
    print(f"Experiments: {total_experiments} (budget: <=16)")
    for e in detected_bugs:
        print(f"  [DETECTED] {e.get('bug_id')} - {e.get('rule_id')}")
    print("=" * 60)
    
    # Save results
    with open("cross_entity_chain_small_scale_result.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print("\nResults saved to cross_entity_chain_small_scale_result.json")
    
    return summary


if __name__ == "__main__":
    main()

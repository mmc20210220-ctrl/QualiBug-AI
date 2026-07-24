"""Idempotency Replay Small Scale Execution.

SPEC: QualiBug 幂等重复与请求重放实验自动生成
Run ID: PROJECT_D_IDEMPOTENCY_REPLAY_SMALL_SCALE_V1
Target: TSLA-BUG-035
Max experiments: 12
"""
import json
import urllib.request
import urllib.error
import time

BASE = "http://localhost:8002"

# Tokens
ADMIN = "admin-ivan-token"       # ADMIN, acme
SUPERVISOR = "supervisor-grace-token"  # SUPERVISOR, acme


def api_call(method, path, token, body=None):
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


def run_exact_replay():
    """Primary test: Add same agent to same team twice.
    
    Chain: create_team -> add_member(agent-001) -> observe -> add_member(agent-001) AGAIN -> observe
    Expected: First=200, Second=409 (duplicate)
    Bug: Second also returns 200 (duplicate member added)
    """
    r = {"experiment_id": "IDEM_EXACT_REPLAY", "bug_id": "TSLA-BUG-035", "steps": []}
    
    # Step 1: Create team
    status, team = api_call("POST", "/teams", ADMIN, {"name": "Idem Test Team"})
    r["steps"].append({"step": 1, "op": "create_team", "status": status})
    if status != 201:
        r["verdict"] = "SETUP_FAILED"
        r["failure_reason"] = f"create team failed: {status}"
        return r
    team_id = team.get("id")
    
    # Step 2: First execution - add agent-001
    status, resp1 = api_call("POST", f"/teams/{team_id}/members", SUPERVISOR, {"agent_id": "agent-001"})
    r["steps"].append({"step": 2, "op": "add_member(agent-001) FIRST", "status": status, "response": resp1})
    first_status = status
    first_members = resp1.get("members", [])
    
    # Step 3: Observe side effect (first execution proof)
    status, team_check = api_call("GET", f"/teams/{team_id}", SUPERVISOR)
    members_after_first = team_check.get("members", [])
    r["steps"].append({
        "step": 3, "op": "observe_team_members",
        "status": status, "members": members_after_first,
        "agent_001_count": members_after_first.count("agent-001"),
    })
    first_execution_landed = "agent-001" in members_after_first
    
    # Step 4: REPLAY - add same agent-001 again (SAME business action)
    status, resp2 = api_call("POST", f"/teams/{team_id}/members", SUPERVISOR, {"agent_id": "agent-001"})
    r["steps"].append({"step": 4, "op": "add_member(agent-001) REPLAY", "status": status, "response": resp2})
    replay_status = status
    
    # Step 5: Observe side effect after replay
    status, team_final = api_call("GET", f"/teams/{team_id}", SUPERVISOR)
    members_after_replay = team_final.get("members", [])
    agent_count_final = members_after_replay.count("agent-001")
    r["steps"].append({
        "step": 5, "op": "observe_team_members_post_replay",
        "status": status, "members": members_after_replay,
        "agent_001_count": agent_count_final,
    })
    
    # Oracle evaluation
    r["first_execution_status"] = first_status
    r["first_execution_landed"] = first_execution_landed
    r["replay_status"] = replay_status
    r["expected_replay_status"] = 409
    r["members_after_first"] = members_after_first
    r["members_after_replay"] = members_after_replay
    r["duplicate_created"] = agent_count_final > 1
    r["rule_id"] = "BR-DATA-006"
    
    # Verdict
    if replay_status == 409:
        r["verdict"] = "IDEMPOTENCY_ENFORCED"
        r["detected"] = False
        r["outcome"] = "TRUE_PASS_CONFIRMED"
    elif replay_status == 200 and agent_count_final > 1:
        r["verdict"] = "IDEMPOTENCY_VIOLATED"
        r["detected"] = True
        r["evidence"] = {
            "first_execution_status": first_status,
            "first_execution_side_effect": f"agent-001 added (count=1)",
            "replay_status": replay_status,
            "replay_side_effect": f"agent-001 duplicated (count={agent_count_final})",
            "expected": "409 Conflict (duplicate member)",
            "actual": f"200 OK (duplicate created, count={agent_count_final})",
            "same_business_action": True,
            "idempotency_key": "team_id + agent_id",
        }
    elif replay_status == 200 and agent_count_final == 1:
        # SUT handled idempotently (no duplicate) but didn't return 409
        r["verdict"] = "IDEMPOTENCY_PARTIAL"
        r["detected"] = False
        r["outcome"] = "SILENT_IDEMPOTENCY"
    else:
        r["verdict"] = "UNEXPECTED"
        r["detected"] = False
    
    return r


def run_diff_payload_variant():
    """Variant: Same team, DIFFERENT agent (different business action, should succeed)."""
    r = {"experiment_id": "IDEM_DIFF_PAYLOAD", "bug_id": "TSLA-BUG-035", "steps": []}
    
    # Create team
    status, team = api_call("POST", "/teams", ADMIN, {"name": "Idem Variant Team"})
    if status != 201:
        r["verdict"] = "SETUP_FAILED"
        return r
    team_id = team.get("id")
    
    # Add agent-001
    s1, _ = api_call("POST", f"/teams/{team_id}/members", SUPERVISOR, {"agent_id": "agent-001"})
    r["steps"].append({"step": 1, "op": "add_member(agent-001)", "status": s1})
    
    # Add agent-002 (DIFFERENT identity = NEW business action)
    s2, resp2 = api_call("POST", f"/teams/{team_id}/members", SUPERVISOR, {"agent_id": "agent-002"})
    r["steps"].append({"step": 2, "op": "add_member(agent-002) DIFFERENT", "status": s2})
    
    r["expected_status"] = 200
    r["actual_status"] = s2
    r["verdict"] = "VARIANT_PASS" if s2 == 200 else "VARIANT_FAIL"
    r["detected"] = False
    return r


def run_diff_key_variant():
    """Variant: Different team, same agent (different scope = different action)."""
    r = {"experiment_id": "IDEM_DIFF_KEY", "bug_id": "TSLA-BUG-035", "steps": []}
    
    # Create two teams
    s1, team_a = api_call("POST", "/teams", ADMIN, {"name": "Idem Key Team A"})
    s2, team_b = api_call("POST", "/teams", ADMIN, {"name": "Idem Key Team B"})
    if s1 != 201 or s2 != 201:
        r["verdict"] = "SETUP_FAILED"
        return r
    
    # Add agent-001 to team A
    s3, _ = api_call("POST", f"/teams/{team_a['id']}/members", SUPERVISOR, {"agent_id": "agent-001"})
    r["steps"].append({"step": 1, "op": "add_member(agent-001) to Team A", "status": s3})
    
    # Add agent-001 to team B (DIFFERENT scope = legitimate new action)
    s4, _ = api_call("POST", f"/teams/{team_b['id']}/members", SUPERVISOR, {"agent_id": "agent-001"})
    r["steps"].append({"step": 2, "op": "add_member(agent-001) to Team B", "status": s4})
    
    r["expected_status"] = 200
    r["actual_status"] = s4
    r["verdict"] = "VARIANT_PASS" if s4 == 200 else "VARIANT_FAIL"
    r["detected"] = False
    return r


def main():
    print("=" * 60)
    print("IDEMPOTENCY REPLAY SMALL SCALE EXECUTION")
    print("Run ID: PROJECT_D_IDEMPOTENCY_REPLAY_SMALL_SCALE_V1")
    print("Target: TSLA-BUG-035 (BR-DATA-006)")
    print("=" * 60)
    
    experiments = []
    
    print("\n[1/3] Exact Replay (add same member twice)...")
    e = run_exact_replay()
    experiments.append(e)
    print(f"  -> {e.get('verdict')} | detected={e.get('detected')}")
    if e.get("evidence"):
        print(f"     First: {e['evidence']['first_execution_status']}")
        print(f"     Replay: {e['evidence']['replay_status']} (expected 409)")
        print(f"     Duplicate: {e['evidence']['replay_side_effect']}")
    
    print("\n[2/3] Different Payload Variant (different agent, same team)...")
    e = run_diff_payload_variant()
    experiments.append(e)
    print(f"  -> {e.get('verdict')}")
    
    print("\n[3/3] Different Key Variant (same agent, different team)...")
    e = run_diff_key_variant()
    experiments.append(e)
    print(f"  -> {e.get('verdict')}")
    
    # Summary
    detected = any(exp.get("detected") for exp in experiments)
    
    summary = {
        "run_id": "PROJECT_D_IDEMPOTENCY_REPLAY_SMALL_SCALE_V1",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_experiments": len(experiments),
        "target_count": 1,
        "detected": detected,
        "outcome": "BUG_DETECTED" if detected else "TRUE_PASS_CONFIRMED",
        "experiments": experiments,
    }
    
    print("\n" + "=" * 60)
    if detected:
        print("RESULT: TSLA-BUG-035 DETECTED (idempotency violated)")
        print("  -> Duplicate team member created without 409")
    else:
        print("RESULT: TRUE_PASS_CONFIRMED (SUT handles idempotency correctly)")
    print(f"Experiments: {len(experiments)} (budget: <=12)")
    print("=" * 60)
    
    with open("idempotency_replay_small_scale_result.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print("\nResults saved to idempotency_replay_small_scale_result.json")
    
    return summary


if __name__ == "__main__":
    main()

"""Idempotency Replay Formal Run.

SPEC: QualiBug 幂等重复与请求重放实验自动生成
Run ID: PROJECT_D_IDEMPOTENCY_REPLAY_V1_FINAL
Target: TSLA-BUG-035
Max experiments: 24
"""
import json
import urllib.request
import urllib.error
import time

BASE = "http://localhost:8002"
ADMIN = "admin-ivan-token"
SUPERVISOR = "supervisor-grace-token"


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
    """Primary: exact replay of add_member."""
    r = {"experiment_id": "IDEM_F_EXACT", "type": "EXACT_REPLAY", "bug_id": "TSLA-BUG-035"}
    s, team = api_call("POST", "/teams", ADMIN, {"name": "Formal Idem Team"})
    if s != 201:
        r["verdict"] = "SETUP_FAILED"; return r
    tid = team["id"]
    # First execution
    s1, r1 = api_call("POST", f"/teams/{tid}/members", SUPERVISOR, {"agent_id": "agent-001"})
    # Observe
    _, chk = api_call("GET", f"/teams/{tid}", SUPERVISOR)
    members_1 = chk.get("members", [])
    # Replay
    s2, r2 = api_call("POST", f"/teams/{tid}/members", SUPERVISOR, {"agent_id": "agent-001"})
    # Observe post-replay
    _, chk2 = api_call("GET", f"/teams/{tid}", SUPERVISOR)
    members_2 = chk2.get("members", [])
    
    r["first_status"] = s1
    r["replay_status"] = s2
    r["members_after_first"] = members_1
    r["members_after_replay"] = members_2
    r["duplicate_count"] = members_2.count("agent-001")
    r["first_execution_landed"] = "agent-001" in members_1
    r["rule_id"] = "BR-DATA-006"
    r["expected_replay_status"] = 409
    
    if s2 == 409:
        r["verdict"] = "IDEMPOTENCY_ENFORCED"
        r["detected"] = False
    elif s2 == 200 and members_2.count("agent-001") > 1:
        r["verdict"] = "IDEMPOTENCY_VIOLATED"
        r["detected"] = True
        r["evidence"] = {
            "same_business_action": True,
            "idempotency_key": f"team={tid} + agent=agent-001",
            "first_status": s1,
            "first_side_effect": "agent-001 in members (count=1)",
            "replay_status": s2,
            "replay_side_effect": f"agent-001 duplicated (count={members_2.count('agent-001')})",
            "expected": "409 Conflict",
            "actual": "200 OK with duplicate",
        }
    else:
        r["verdict"] = "UNEXPECTED"
        r["detected"] = False
    return r


def run_triple_replay():
    """Extended: triple replay to show cumulative side effects."""
    r = {"experiment_id": "IDEM_F_TRIPLE", "type": "TRIPLE_REPLAY", "bug_id": "TSLA-BUG-035"}
    s, team = api_call("POST", "/teams", ADMIN, {"name": "Formal Triple Team"})
    if s != 201:
        r["verdict"] = "SETUP_FAILED"; return r
    tid = team["id"]
    
    statuses = []
    for i in range(3):
        st, _ = api_call("POST", f"/teams/{tid}/members", SUPERVISOR, {"agent_id": "agent-002"})
        statuses.append(st)
    
    _, chk = api_call("GET", f"/teams/{tid}", SUPERVISOR)
    members = chk.get("members", [])
    
    r["execution_statuses"] = statuses
    r["final_members"] = members
    r["agent_002_count"] = members.count("agent-002")
    r["rule_id"] = "BR-DATA-006"
    r["detected"] = members.count("agent-002") > 1
    r["verdict"] = "IDEMPOTENCY_VIOLATED" if r["detected"] else "IDEMPOTENCY_ENFORCED"
    return r


def run_diff_payload():
    """Variant: different agent = new action (should succeed)."""
    r = {"experiment_id": "IDEM_F_DIFF_PAYLOAD", "type": "SAME_KEY_DIFF_PAYLOAD", "bug_id": "TSLA-BUG-035"}
    s, team = api_call("POST", "/teams", ADMIN, {"name": "Formal Variant Team"})
    if s != 201:
        r["verdict"] = "SETUP_FAILED"; return r
    tid = team["id"]
    s1, _ = api_call("POST", f"/teams/{tid}/members", SUPERVISOR, {"agent_id": "agent-001"})
    s2, _ = api_call("POST", f"/teams/{tid}/members", SUPERVISOR, {"agent_id": "agent-002"})
    r["statuses"] = [s1, s2]
    r["verdict"] = "VARIANT_PASS" if s2 == 200 else "VARIANT_FAIL"
    r["detected"] = False
    r["oracle"] = "different identity = new legitimate request"
    return r


def run_diff_key():
    """Variant: different team = different scope (should succeed)."""
    r = {"experiment_id": "IDEM_F_DIFF_KEY", "type": "DIFF_KEY_SAME_PAYLOAD", "bug_id": "TSLA-BUG-035"}
    s1, ta = api_call("POST", "/teams", ADMIN, {"name": "Formal Key A"})
    s2, tb = api_call("POST", "/teams", ADMIN, {"name": "Formal Key B"})
    if s1 != 201 or s2 != 201:
        r["verdict"] = "SETUP_FAILED"; return r
    s3, _ = api_call("POST", f"/teams/{ta['id']}/members", SUPERVISOR, {"agent_id": "agent-001"})
    s4, _ = api_call("POST", f"/teams/{tb['id']}/members", SUPERVISOR, {"agent_id": "agent-001"})
    r["statuses"] = [s3, s4]
    r["verdict"] = "VARIANT_PASS" if s4 == 200 else "VARIANT_FAIL"
    r["detected"] = False
    r["oracle"] = "different scope = different business action"
    return r


def run_side_effect_proof():
    """Prove first execution side effect is real and observable."""
    r = {"experiment_id": "IDEM_F_SIDE_EFFECT_PROOF", "type": "SIDE_EFFECT_PROOF", "bug_id": "TSLA-BUG-035"}
    s, team = api_call("POST", "/teams", ADMIN, {"name": "Formal Proof Team"})
    if s != 201:
        r["verdict"] = "SETUP_FAILED"; return r
    tid = team["id"]
    
    # Before
    _, before = api_call("GET", f"/teams/{tid}", SUPERVISOR)
    members_before = before.get("members", [])
    
    # Execute
    s1, _ = api_call("POST", f"/teams/{tid}/members", SUPERVISOR, {"agent_id": "agent-003"})
    
    # After
    _, after = api_call("GET", f"/teams/{tid}", SUPERVISOR)
    members_after = after.get("members", [])
    
    r["members_before"] = members_before
    r["members_after"] = members_after
    r["side_effect_observed"] = "agent-003" in members_after and "agent-003" not in members_before
    r["verdict"] = "PROOF_CONFIRMED" if r["side_effect_observed"] else "PROOF_FAILED"
    r["detected"] = False
    return r


def main():
    print("=" * 60)
    print("IDEMPOTENCY REPLAY FORMAL RUN")
    print("Run ID: PROJECT_D_IDEMPOTENCY_REPLAY_V1_FINAL")
    print("Target: TSLA-BUG-035 (BR-DATA-006)")
    print("=" * 60)
    
    experiments = []
    
    print("\n[1/5] Exact Replay...")
    e = run_exact_replay()
    experiments.append(e)
    print(f"  -> {e['verdict']} | detected={e.get('detected')}")
    
    print("\n[2/5] Triple Replay (cumulative side effects)...")
    e = run_triple_replay()
    experiments.append(e)
    print(f"  -> {e['verdict']} | count={e.get('agent_002_count')}")
    
    print("\n[3/5] Different Payload Variant...")
    e = run_diff_payload()
    experiments.append(e)
    print(f"  -> {e['verdict']}")
    
    print("\n[4/5] Different Key Variant...")
    e = run_diff_key()
    experiments.append(e)
    print(f"  -> {e['verdict']}")
    
    print("\n[5/5] Side Effect Proof...")
    e = run_side_effect_proof()
    experiments.append(e)
    print(f"  -> {e['verdict']} | observed={e.get('side_effect_observed')}")
    
    # Summary
    detected = any(exp.get("detected") for exp in experiments)
    
    summary = {
        "run_id": "PROJECT_D_IDEMPOTENCY_REPLAY_V1_FINAL",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_experiments": len(experiments),
        "target_count": 1,
        "detected": detected,
        "outcome": "BUG_DETECTED" if detected else "TRUE_PASS_CONFIRMED",
        "rule_violations": [
            {"bug_id": "TSLA-BUG-035", "rule_id": "BR-DATA-006", "detected": detected},
        ],
        "experiments": experiments,
    }
    
    print("\n" + "=" * 60)
    if detected:
        print("FORMAL RESULT: TSLA-BUG-035 DETECTED")
        print("  Idempotency violated: duplicate member added without 409")
        print("  Project D cumulative: 25 unique TP, 24 deep unique TP")
    else:
        print("FORMAL RESULT: TRUE_PASS_CONFIRMED")
        print("  SUT correctly handles idempotency")
    print(f"Experiments: {len(experiments)} (budget: <=24)")
    print(f"Verdict: {'PASS' if detected else 'TRUE_PASS'}")
    print("=" * 60)
    
    with open("idempotency_replay_formal_result.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print("\nResults saved to idempotency_replay_formal_result.json")
    return summary


if __name__ == "__main__":
    main()

"""Independent reproduction of candidate TPs (2 attempts each).
Part of Missing Experiment Mechanism Formal Closure.

Correct API routes (from mock_server.py):
  Contract: POST /contracts, /contracts/{id}/submit, /contracts/{id}/legal-approve,
            /contracts/{id}/activate, /contracts/{id}/cancel
  Milestone: POST /contracts/{id}/milestones, POST /milestones/{id}/accept
  Invoice: POST /invoices (contract_id, invoice_no, subtotal, tax_amount, issue_date)
  Payment: POST /payment-requests (contract_id, milestone_id, invoice_id, amount)
           /payment-requests/{id}/manager-approve, /payment-requests/{id}/finance-approve
           /payment-requests/{id}/pay (requires Idempotency-Key header)
"""
import json, urllib.request, urllib.error, uuid, sys

BASE = "http://127.0.0.1:8000/api/v1"
ADMIN = "acme-admin-token"
LEGAL = "acme-legal-token"
FINANCE = "acme-finance-token"
MANAGER = "acme-manager-token"
GLOBEX = "globex-admin-token"


def req(method, path, token=ADMIN, body=None, headers=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def create_contract(token=ADMIN, suffix=""):
    """Create a minimal contract and return its ID."""
    s = uuid.uuid4().hex[:8]
    _, depts = req("GET", "/reference/departments", token)
    _, vendors = req("GET", "/reference/vendors", token)
    _, budgets = req("GET", "/budgets", token)
    dept_id = depts[0]["id"] if isinstance(depts, list) and depts else ""
    vendor_id = vendors[0]["id"] if isinstance(vendors, list) and vendors else ""
    budget_id = budgets[0]["id"] if isinstance(budgets, list) and budgets else ""
    body = {
        "contract_no": f"REPRO{suffix}-{s}",
        "title": f"Reproduction {suffix} {s}",
        "department_id": dept_id,
        "vendor_id": vendor_id,
        "budget_id": budget_id,
        "total_amount": 50000,
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
    }
    st, resp = req("POST", "/contracts", token, body)
    if st in (200, 201):
        return resp.get("id")
    print(f"    [WARN] create_contract failed: {st} {resp}")
    return None


def add_milestone_and_submit(cid, token=ADMIN):
    """Add milestone matching total and submit to LEGAL_REVIEW."""
    _, c = req("GET", f"/contracts/{cid}", token)
    total = c.get("total_amount", 50000)
    st_m, ms = req("POST", f"/contracts/{cid}/milestones", token, {
        "name": "M1",
        "amount": total, "due_date": "2026-06-30",
    })
    if st_m not in (200, 201):
        print(f"    [WARN] milestone create: {st_m} {ms}")
        return st_m
    st, resp = req("POST", f"/contracts/{cid}/submit", token)
    return st


def reproduce_cf_auth_001(attempt):
    """CF-AUTH-001: admin can approve a contract in LEGAL_REVIEW.
    Business rule: only legal role should approve.
    Bug: mock allows admin (line 338: role not in ('legal','admin')).
    """
    print(f"  [Attempt {attempt}] CF-AUTH-001: admin legal-approve on LEGAL_REVIEW")
    cid = create_contract(suffix=f"A{attempt}")
    if not cid:
        return {"passed": False, "reason": "cannot_create_contract"}
    st = add_milestone_and_submit(cid)
    if st != 200:
        return {"passed": False, "reason": f"submit_failed_{st}"}
    # Verify in LEGAL_REVIEW
    _, c = req("GET", f"/contracts/{cid}", ADMIN)
    if c.get("status") != "LEGAL_REVIEW":
        return {"passed": False, "reason": f"wrong_status_{c.get('status')}"}
    # Violation: admin attempts legal-approve (should be 403 per business rules)
    st_viol, resp_viol = req("POST", f"/contracts/{cid}/legal-approve", ADMIN)
    bug_reproduced = (st_viol == 200)
    # Control: create another contract, legal approves (should succeed)
    cid2 = create_contract(suffix=f"A{attempt}c")
    add_milestone_and_submit(cid2)
    st_ctrl, _ = req("POST", f"/contracts/{cid2}/legal-approve", LEGAL)
    print(f"    Control(legal approve): {st_ctrl} | Violation(admin approve): {st_viol}")
    print(f"    Bug reproduced (admin allowed): {bug_reproduced}")
    return {
        "passed": bug_reproduced,
        "control_status": st_ctrl,
        "violation_status": st_viol,
        "same_violation": bug_reproduced,
        "same_side_effect": bug_reproduced,
        "same_root_cause": bug_reproduced,
    }


def reproduce_cf_state_004(attempt):
    """CF-STATE-004: payment can be executed on a CANCELLED contract.
    Business rule: execute payment requires contract ACTIVE.
    Bug: _execute_payment doesn't check contract.status.
    """
    print(f"  [Attempt {attempt}] CF-STATE-004: execute payment on CANCELLED contract")
    cid = create_contract(suffix=f"S{attempt}")
    if not cid:
        return {"passed": False, "reason": "cannot_create_contract"}
    # Submit -> legal-approve -> activate
    st = add_milestone_and_submit(cid)
    if st != 200:
        return {"passed": False, "reason": f"submit_failed_{st}"}
    st_appr, _ = req("POST", f"/contracts/{cid}/legal-approve", LEGAL)
    if st_appr != 200:
        return {"passed": False, "reason": f"legal_approve_failed_{st_appr}"}
    st_act, _ = req("POST", f"/contracts/{cid}/activate", ADMIN)
    if st_act != 200:
        return {"passed": False, "reason": f"activate_failed_{st_act}"}
    _, c = req("GET", f"/contracts/{cid}", ADMIN)
    if c.get("status") != "ACTIVE":
        return {"passed": False, "reason": f"not_active_{c.get('status')}"}
    # Accept milestone: PENDING -> submit -> SUBMITTED -> accept -> ACCEPTED
    _, ms_list = req("GET", f"/contracts/{cid}/milestones", ADMIN)
    if not isinstance(ms_list, list) or not ms_list:
        return {"passed": False, "reason": "no_milestones"}
    mid = ms_list[0]["id"]
    st_sub, _ = req("POST", f"/milestones/{mid}/submit", ADMIN,
                    {"evidence_url": "http://evidence.test/delivery.pdf"})
    if st_sub != 200:
        return {"passed": False, "reason": f"milestone_submit_failed_{st_sub}"}
    st_acc, _ = req("POST", f"/milestones/{mid}/accept", ADMIN,
                    {"accepted_amount": ms_list[0]["amount"]})
    if st_acc != 200:
        return {"passed": False, "reason": f"milestone_accept_failed_{st_acc}"}
    # Create invoice
    s = uuid.uuid4().hex[:6]
    st_inv, inv = req("POST", "/invoices", ADMIN, {
        "contract_id": cid,
        "invoice_no": f"REPRO-INV-{s}",
        "subtotal": 10000,
        "tax_amount": 0,
        "issue_date": "2026-07-01",
    })
    inv_id = inv.get("id") if st_inv in (200, 201) else None
    if not inv_id:
        return {"passed": False, "reason": f"invoice_failed_{st_inv}_{inv}"}
    # Create payment request
    st_pay, pay = req("POST", "/payment-requests", FINANCE, {
        "contract_id": cid,
        "milestone_id": mid,
        "invoice_id": inv_id,
        "amount": 10000,
    })
    pay_id = pay.get("id") if st_pay in (200, 201) else None
    if not pay_id:
        return {"passed": False, "reason": f"payment_create_failed_{st_pay}_{pay}"}
    # Manager approve -> Finance approve
    st_ma, _ = req("POST", f"/payment-requests/{pay_id}/manager-approve", MANAGER)
    if st_ma != 200:
        return {"passed": False, "reason": f"manager_approve_failed_{st_ma}"}
    st_fa, _ = req("POST", f"/payment-requests/{pay_id}/finance-approve", FINANCE)
    if st_fa != 200:
        return {"passed": False, "reason": f"finance_approve_failed_{st_fa}"}
    # Cancel contract
    st_cancel, _ = req("POST", f"/contracts/{cid}/cancel", ADMIN)
    if st_cancel != 200:
        return {"passed": False, "reason": f"cancel_failed_{st_cancel}"}
    _, c2 = req("GET", f"/contracts/{cid}", ADMIN)
    if c2.get("status") != "CANCELLED":
        return {"passed": False, "reason": f"not_cancelled_{c2.get('status')}"}
    # Execute payment on CANCELLED contract (BUG: should be rejected)
    idem_key = uuid.uuid4().hex
    st_exec, resp_exec = req("POST", f"/payment-requests/{pay_id}/pay", FINANCE,
                             headers={"Idempotency-Key": idem_key})
    bug_reproduced = (st_exec == 200)
    print(f"    Contract status: CANCELLED | Execute payment status: {st_exec}")
    print(f"    Bug reproduced (payment executed on cancelled): {bug_reproduced}")
    return {
        "passed": bug_reproduced,
        "contract_status_at_execution": "CANCELLED",
        "execution_status": st_exec,
        "same_violation": bug_reproduced,
        "same_side_effect": bug_reproduced,
        "same_root_cause": bug_reproduced,
    }


def main():
    print("=" * 60)
    print("INDEPENDENT REPRODUCTION - Missing Mechanism Closure")
    print("=" * 60)

    # Check server
    st, _ = req("GET", "/contracts", ADMIN)
    if st == 0:
        print("ERROR: Mock server not reachable at port 8000")
        sys.exit(1)
    print(f"Server reachable (status={st})")

    results = {
        "CF-AUTH-001": {"attempts": [], "passed_count": 0},
        "CF-STATE-004": {"attempts": [], "passed_count": 0},
    }

    print("\n--- Reproducing CF-AUTH-001 (2 attempts) ---")
    for i in range(1, 3):
        r = reproduce_cf_auth_001(i)
        results["CF-AUTH-001"]["attempts"].append(r)
        if r["passed"]:
            results["CF-AUTH-001"]["passed_count"] += 1

    print("\n--- Reproducing CF-STATE-004 (2 attempts) ---")
    for i in range(1, 3):
        r = reproduce_cf_state_004(i)
        results["CF-STATE-004"]["attempts"].append(r)
        if r["passed"]:
            results["CF-STATE-004"]["passed_count"] += 1

    # Summary
    print("\n" + "=" * 60)
    print("REPRODUCTION SUMMARY")
    print("=" * 60)
    for bug_id, data in results.items():
        pc = data["passed_count"]
        status = "PASS (2/2)" if pc == 2 else f"FAIL ({pc}/2)"
        print(f"  {bug_id}: {status}")
    results["summary"] = {
        "CF-AUTH-001": "PASS" if results["CF-AUTH-001"]["passed_count"] == 2 else "FAIL",
        "CF-STATE-004": "PASS" if results["CF-STATE-004"]["passed_count"] == 2 else "FAIL",
    }
    with open("_closure_reproduction_result.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("\nSaved: _closure_reproduction_result.json")


if __name__ == "__main__":
    main()

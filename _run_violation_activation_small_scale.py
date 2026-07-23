"""Violation Activation Small Scale Runner.

Executes Control/Violation experiment pairs against the ContractFlow mock server
for the 9 ORACLE_NOT_VIOLATED target rules.

Run ID: PROJECT_C_VIOLATION_ACTIVATION_SMALL_SCALE_V1
Max experiments: 40
"""
from __future__ import annotations

import json
import sys
import time
import uuid
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta

BASE_URL = "http://127.0.0.1:8000/api/v1"
ADMIN_TOKEN = "acme-admin-token"
FINANCE_TOKEN = "acme-finance-token"
LEGAL_TOKEN = "acme-legal-token"

MAX_EXPERIMENTS = 100
RUN_ID = "PROJECT_C_VIOLATION_ACTIVATION_V1_FINAL"

# ─── HTTP Client ──────────────────────────────────────────────────────────────

def _request(method: str, path: str, *, token: str = ADMIN_TOKEN,
             body: dict | None = None, headers: dict | None = None) -> dict:
    """Send HTTP request to mock server and return structured result."""
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode()
            return {
                "status_code": resp.status,
                "body": json.loads(resp_body) if resp_body else {},
                "error": None,
            }
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode()
        try:
            parsed = json.loads(resp_body)
        except json.JSONDecodeError:
            parsed = {"raw": resp_body}
        return {"status_code": e.code, "body": parsed, "error": None}
    except Exception as e:
        return {"status_code": 0, "body": {}, "error": str(e)}


def _get(path: str, **kw) -> dict:
    return _request("GET", path, **kw)


def _post(path: str, body: dict | None = None, **kw) -> dict:
    return _request("POST", path, body=body, **kw)


def _patch(path: str, body: dict | None = None, **kw) -> dict:
    return _request("PATCH", path, body=body, **kw)


# ─── Discovery Helpers ────────────────────────────────────────────────────────

def discover_seed_data() -> dict:
    """Discover seed data from the running server."""
    contracts = _get("/contracts")["body"]
    if isinstance(contracts, dict) and "items" in contracts:
        contracts = contracts["items"]
    elif isinstance(contracts, dict) and "data" in contracts:
        contracts = contracts["data"]
    if not isinstance(contracts, list):
        contracts = []

    # Use first contract for acme tenant
    result = {"contracts": [], "budgets": [], "milestones": {}, "invoices": {}, "payments": {}}
    for c in contracts[:3]:
        cid = c.get("id", "")
        result["contracts"].append(c)
        # Get milestones
        ms_resp = _get(f"/contracts/{cid}/milestones")
        ms = ms_resp["body"]
        if isinstance(ms, dict) and "items" in ms:
            ms = ms["items"]
        if not isinstance(ms, list):
            ms = []
        result["milestones"][cid] = ms
        # Get payments
        pay_resp = _get(f"/contracts/{cid}/payment-requests")
        pays = pay_resp["body"]
        if isinstance(pays, dict) and "items" in pays:
            pays = pays["items"]
        if not isinstance(pays, list):
            pays = []
        result["payments"][cid] = pays

    # Get budgets
    bud_resp = _get("/budgets")
    buds = bud_resp["body"]
    if isinstance(buds, dict) and "items" in buds:
        buds = buds["items"]
    if not isinstance(buds, list):
        buds = []
    result["budgets"] = buds

    return result


# ─── Experiment Executors (one per target) ────────────────────────────────────

def exec_onv001_version_conflict(seed: dict) -> dict:
    """ONV-001: Concurrency version conflict.
    
    Bug: If-Match-Version header is optional. Without it, stale updates accepted.
    Control: PATCH with correct version → 200
    Violation: Two actors read same version, A updates, B uses stale version → should 409
    """
    experiments = []
    # Find a DRAFT contract or create one
    contracts = seed["contracts"]
    draft = next((c for c in contracts if c.get("status") == "DRAFT"), None)
    if not draft:
        # Create a new contract for this test
        buds = seed["budgets"]
        bud_id = buds[0]["id"] if buds else ""
        dept_id = ""
        vendor_id = ""
        # Discover dept/vendor from existing contract
        if contracts:
            dept_id = contracts[0].get("department_id", "")
            vendor_id = contracts[0].get("vendor_id", "")
        create_resp = _post("/contracts", body={
            "contract_no": f"VA-TEST-{uuid.uuid4().hex[:8]}",
            "title": "Violation Activation Test Contract",
            "department_id": dept_id,
            "vendor_id": vendor_id,
            "budget_id": bud_id,
            "total_amount": 50000.0,
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        })
        draft = create_resp["body"]
        experiments.append({"step": "setup_create_contract", "response": create_resp})

    cid = draft.get("id", "")
    version = draft.get("version", 1)

    # Control: PATCH with correct version header → should succeed
    ctrl = _patch(f"/contracts/{cid}", body={"title": "Control Update"},
                  headers={"If-Match-Version": str(version)})
    experiments.append({"step": "control_patch_correct_version", "response": ctrl})
    new_version = ctrl["body"].get("version", version + 1) if ctrl["status_code"] == 200 else version

    # Violation: PATCH with STALE version (old version) → should get 409
    viol = _patch(f"/contracts/{cid}", body={"title": "Stale Update"},
                  headers={"If-Match-Version": str(version)})  # old version
    experiments.append({"step": "violation_patch_stale_version", "response": viol})

    # Additional violation: PATCH WITHOUT version header → should be rejected but isn't
    viol2 = _patch(f"/contracts/{cid}", body={"title": "No Version Header Update"})
    experiments.append({"step": "violation_patch_no_version_header", "response": viol2})

    # Oracle evaluation
    control_pass = ctrl["status_code"] == 200
    stale_rejected = viol["status_code"] == 409
    no_header_rejected = viol2["status_code"] == 409

    oracle_result = "PASS"
    violation_triggered = False
    if control_pass and not stale_rejected:
        oracle_result = "VIOLATION"
        violation_triggered = True
    elif control_pass and not no_header_rejected:
        oracle_result = "VIOLATION"
        violation_triggered = True

    return {
        "target_id": "ONV-001",
        "rule_id": "rule:src_75c80a84b78a6d77:21",
        "expression_type": "CONCURRENCY",
        "experiments": experiments,
        "experiment_count": len(experiments),
        "control_result": "PASS" if control_pass else "FAIL",
        "oracle_result": oracle_result,
        "violation_triggered": violation_triggered,
        "evidence": {
            "control_status": ctrl["status_code"],
            "stale_version_status": viol["status_code"],
            "no_header_status": viol2["status_code"],
            "expected_stale": 409,
            "expected_no_header": 409,
        },
        "finding": {
            "root_cause": "version_check_optional_header_not_required",
            "mechanism": "concurrency_optimistic_locking_bypass",
            "operation": "PATCH /contracts/{id}",
        } if violation_triggered else None,
    }


def exec_onv002_milestone_sum(seed: dict) -> dict:
    """ONV-002: SUM(milestone.amount) == contract.total_amount at submit.
    
    Control: Submit with matching sums → 200
    Violation: Create contract with mismatched milestone amounts → submit → should 409
    """
    experiments = []
    contracts = seed["contracts"]
    buds = seed["budgets"]
    bud_id = buds[0]["id"] if buds else ""
    dept_id = contracts[0].get("department_id", "") if contracts else ""
    vendor_id = contracts[0].get("vendor_id", "") if contracts else ""

    # Create fresh contract for isolation
    create_resp = _post("/contracts", body={
        "contract_no": f"VA-SUM-{uuid.uuid4().hex[:8]}",
        "title": "Sum Conservation Test",
        "department_id": dept_id,
        "vendor_id": vendor_id,
        "budget_id": bud_id,
        "total_amount": 100000.0,
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
    })
    contract = create_resp["body"]
    cid = contract.get("id", "")
    experiments.append({"step": "setup_create_contract", "response": create_resp})

    # Add milestone with WRONG amount (60000 instead of matching 100000)
    ms_resp = _post(f"/contracts/{cid}/milestones", body={
        "name": "Mismatched Milestone",
        "amount": 60000.0,  # Does NOT match total 100000
        "due_date": "2026-06-30",
    })
    experiments.append({"step": "setup_add_mismatched_milestone", "response": ms_resp})

    # Violation: Try to submit with mismatched sums
    submit_resp = _post(f"/contracts/{cid}/submit")
    experiments.append({"step": "violation_submit_mismatched", "response": submit_resp})

    # Control: Fix milestone to match, then submit
    ms_id = ms_resp["body"].get("id", "") if ms_resp["status_code"] in (200, 201) else ""
    if ms_id:
        # Add another milestone to make sum = 100000
        ms2_resp = _post(f"/contracts/{cid}/milestones", body={
            "name": "Matching Milestone",
            "amount": 40000.0,  # 60000 + 40000 = 100000
            "due_date": "2026-09-30",
        })
        experiments.append({"step": "control_add_matching_milestone", "response": ms2_resp})

    submit_ctrl = _post(f"/contracts/{cid}/submit")
    experiments.append({"step": "control_submit_matching", "response": submit_ctrl})

    # Oracle
    mismatch_rejected = submit_resp["status_code"] == 409
    match_accepted = submit_ctrl["status_code"] == 200

    oracle_result = "PASS"
    violation_triggered = False
    if not mismatch_rejected:
        # System accepted mismatched sum → BUG
        oracle_result = "VIOLATION"
        violation_triggered = True

    return {
        "target_id": "ONV-002",
        "rule_id": "rule:src_68e5e273aaf8f71e:77",
        "expression_type": "SUM",
        "experiments": experiments,
        "experiment_count": len(experiments),
        "control_result": "PASS" if match_accepted else "FAIL",
        "oracle_result": oracle_result,
        "violation_triggered": violation_triggered,
        "evidence": {
            "mismatch_submit_status": submit_resp["status_code"],
            "match_submit_status": submit_ctrl["status_code"],
            "milestone_sum": 60000.0,
            "contract_total": 100000.0,
        },
        "finding": {
            "root_cause": "submit_accepts_mismatched_milestone_sum",
            "mechanism": "conservation_sum_violation",
            "operation": "POST /contracts/{id}/submit",
        } if violation_triggered else None,
    }


def exec_onv003_budget_reserve(seed: dict) -> dict:
    """ONV-003: activate → budget.available -= amount AND budget.reserved += amount.
    
    Observe budget before/after activate. Verify delta conservation.
    """
    experiments = []

    # Create contract and drive to APPROVED (not yet activated)
    contracts_resp = _get("/contracts")
    contracts = contracts_resp["body"] if isinstance(contracts_resp["body"], list) else []
    dept_id = contracts[0].get("department_id", "") if contracts else ""
    vendor_id = contracts[0].get("vendor_id", "") if contracts else ""
    buds = _get("/budgets")["body"]
    if not isinstance(buds, list):
        buds = []
    bud_id = buds[0]["id"] if buds else ""

    create_resp = _post("/contracts", body={
        "contract_no": f"VA-DELTA-{uuid.uuid4().hex[:8]}",
        "title": "Budget Delta Test",
        "department_id": dept_id, "vendor_id": vendor_id,
        "budget_id": bud_id, "total_amount": 80000.0,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    c = create_resp["body"]
    cid = c.get("id", "")
    experiments.append({"step": "setup_create_contract", "response": create_resp})

    # Add milestone matching total
    ms_resp = _post(f"/contracts/{cid}/milestones", body={
        "name": "M1", "amount": 80000.0, "due_date": "2026-06-30",
    })
    # Submit → legal approve (gets to APPROVED)
    _post(f"/contracts/{cid}/submit")
    la_resp = _post(f"/contracts/{cid}/legal-approve", token=LEGAL_TOKEN)
    experiments.append({"step": "setup_legal_approve", "response": la_resp})

    if la_resp["status_code"] != 200:
        return {"target_id": "ONV-003", "oracle_result": "BLOCKED",
                "reason": f"legal-approve failed: {la_resp['status_code']}",
                "experiments": experiments, "experiment_count": len(experiments)}

    # Before: read budget
    bud_before = _get(f"/budgets/{bud_id}")
    experiments.append({"step": "observe_budget_before", "response": bud_before})
    before_avail = bud_before["body"].get("available_amount", 0)
    before_reserved = bud_before["body"].get("reserved_amount", 0)

    # Execute: activate
    activate_resp = _post(f"/contracts/{cid}/activate")
    experiments.append({"step": "execute_activate", "response": activate_resp})

    # After: read budget
    bud_after = _get(f"/budgets/{bud_id}")
    experiments.append({"step": "observe_budget_after", "response": bud_after})
    after_avail = bud_after["body"].get("available_amount", 0)
    after_reserved = bud_after["body"].get("reserved_amount", 0)

    # Oracle: check delta
    total_amount = 80000.0
    expected_avail_delta = -total_amount
    expected_reserved_delta = total_amount
    actual_avail_delta = after_avail - before_avail
    actual_reserved_delta = after_reserved - before_reserved

    avail_correct = abs(actual_avail_delta - expected_avail_delta) < 0.01
    reserved_correct = abs(actual_reserved_delta - expected_reserved_delta) < 0.01

    oracle_result = "PASS"
    violation_triggered = False
    if activate_resp["status_code"] == 200:
        if not avail_correct or not reserved_correct:
            oracle_result = "VIOLATION"
            violation_triggered = True
    else:
        oracle_result = "BLOCKED"

    return {
        "target_id": "ONV-003",
        "rule_id": "rule:src_68e5e273aaf8f71e:111",
        "expression_type": "DELTA",
        "experiments": experiments,
        "experiment_count": len(experiments),
        "control_result": "N/A",
        "oracle_result": oracle_result,
        "violation_triggered": violation_triggered,
        "evidence": {
            "before_available": before_avail,
            "after_available": after_avail,
            "before_reserved": before_reserved,
            "after_reserved": after_reserved,
            "expected_avail_delta": expected_avail_delta,
            "actual_avail_delta": actual_avail_delta,
            "expected_reserved_delta": expected_reserved_delta,
            "actual_reserved_delta": actual_reserved_delta,
        },
        "finding": {
            "root_cause": "budget_delta_not_conserved_after_activate",
            "mechanism": "conservation_delta_violation",
            "operation": "POST /contracts/{id}/activate",
        } if violation_triggered else None,
    }


def exec_onv004_cancel_budget_release(seed: dict) -> dict:
    """ONV-004: cancel → budget.reserved -= unpaid AND budget.available += unpaid.
    
    Activate a contract, then cancel it. Verify budget released.
    """
    experiments = []
    contracts = seed["contracts"]
    buds = seed["budgets"]
    bud_id = buds[0]["id"] if buds else ""
    dept_id = contracts[0].get("department_id", "") if contracts else ""
    vendor_id = contracts[0].get("vendor_id", "") if contracts else ""

    # Create + submit + approve + activate a contract for isolation
    create_resp = _post("/contracts", body={
        "contract_no": f"VA-CANCEL-{uuid.uuid4().hex[:8]}",
        "title": "Cancel Budget Test",
        "department_id": dept_id, "vendor_id": vendor_id,
        "budget_id": bud_id, "total_amount": 30000.0,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    c = create_resp["body"]
    cid = c.get("id", "")
    experiments.append({"step": "setup_create", "response": create_resp})

    # Add milestone matching total
    _post(f"/contracts/{cid}/milestones", body={"name": "M1", "amount": 30000.0, "due_date": "2026-06-30"})
    # Submit → Legal Approve → Activate
    _post(f"/contracts/{cid}/submit")
    _post(f"/contracts/{cid}/legal-approve", token=LEGAL_TOKEN)
    activate_resp = _post(f"/contracts/{cid}/activate")
    experiments.append({"step": "setup_activate", "response": activate_resp})

    if activate_resp["status_code"] != 200:
        return {"target_id": "ONV-004", "oracle_result": "BLOCKED", "reason": "activate failed",
                "experiments": experiments, "experiment_count": len(experiments)}

    # Before cancel: read budget
    bud_before = _get(f"/budgets/{bud_id}")
    before_avail = bud_before["body"].get("available_amount", 0)
    before_reserved = bud_before["body"].get("reserved_amount", 0)
    experiments.append({"step": "observe_budget_before_cancel", "response": bud_before})

    # Cancel
    cancel_resp = _post(f"/contracts/{cid}/cancel")
    experiments.append({"step": "execute_cancel", "response": cancel_resp})

    # After cancel: read budget
    bud_after = _get(f"/budgets/{bud_id}")
    after_avail = bud_after["body"].get("available_amount", 0)
    after_reserved = bud_after["body"].get("reserved_amount", 0)
    experiments.append({"step": "observe_budget_after_cancel", "response": bud_after})

    # Oracle: unpaid = total - paid = 30000 - 0 = 30000
    unpaid = 30000.0
    expected_reserved_delta = -unpaid
    expected_avail_delta = unpaid
    actual_reserved_delta = after_reserved - before_reserved
    actual_avail_delta = after_avail - before_avail

    reserved_correct = abs(actual_reserved_delta - expected_reserved_delta) < 0.01
    avail_correct = abs(actual_avail_delta - expected_avail_delta) < 0.01

    oracle_result = "PASS"
    violation_triggered = False
    if cancel_resp["status_code"] == 200:
        if not reserved_correct or not avail_correct:
            oracle_result = "VIOLATION"
            violation_triggered = True

    return {
        "target_id": "ONV-004",
        "rule_id": "rule:src_68e5e273aaf8f71e:111",
        "expression_type": "DELTA",
        "experiments": experiments,
        "experiment_count": len(experiments),
        "control_result": "N/A",
        "oracle_result": oracle_result,
        "violation_triggered": violation_triggered,
        "evidence": {
            "before_available": before_avail, "after_available": after_avail,
            "before_reserved": before_reserved, "after_reserved": after_reserved,
            "unpaid_amount": unpaid,
            "actual_avail_delta": actual_avail_delta,
            "actual_reserved_delta": actual_reserved_delta,
        },
        "finding": {
            "root_cause": "budget_not_released_after_cancel",
            "mechanism": "compensation_delta_violation",
            "operation": "POST /contracts/{id}/cancel",
        } if violation_triggered else None,
    }


def _setup_lifecycle_contract(amount: float, tag: str) -> dict:
    """Create a contract and drive it through full lifecycle to ACTIVE.
    
    Returns dict with cid, ms_id, inv_id, budget_id and response info.
    """
    contracts_resp = _get("/contracts")
    contracts = contracts_resp["body"] if isinstance(contracts_resp["body"], list) else []
    dept_id = contracts[0].get("department_id", "") if contracts else ""
    vendor_id = contracts[0].get("vendor_id", "") if contracts else ""
    buds = _get("/budgets")["body"]
    if not isinstance(buds, list):
        buds = []
    bud_id = buds[0]["id"] if buds else ""

    # Create contract
    create_resp = _post("/contracts", body={
        "contract_no": f"VA-{tag}-{uuid.uuid4().hex[:8]}",
        "title": f"VA {tag} Test",
        "department_id": dept_id, "vendor_id": vendor_id,
        "budget_id": bud_id, "total_amount": amount,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    c = create_resp["body"]
    cid = c.get("id", "")

    # Create milestone
    ms_resp = _post(f"/contracts/{cid}/milestones", body={
        "name": "M1", "amount": amount, "due_date": "2026-06-30",
    })
    ms_id = ms_resp["body"].get("id", "")

    # Submit milestone (requires evidence_url)
    _post(f"/milestones/{ms_id}/submit", body={"evidence_url": "http://test/evidence"})
    # Accept milestone
    _post(f"/milestones/{ms_id}/accept", body={"accepted_amount": amount})

    # Submit contract → legal approve → activate
    _post(f"/contracts/{cid}/submit")
    _post(f"/contracts/{cid}/legal-approve", token=LEGAL_TOKEN)
    activate_resp = _post(f"/contracts/{cid}/activate")

    # Create invoice
    inv_resp = _post("/invoices", body={
        "contract_id": cid, "invoice_no": f"INV-{tag}-{uuid.uuid4().hex[:8]}",
        "subtotal": amount, "tax_amount": 0, "issue_date": "2026-01-01",
    })
    inv_id = inv_resp["body"].get("id", "")

    return {
        "cid": cid, "ms_id": ms_id, "inv_id": inv_id,
        "bud_id": bud_id, "amount": amount,
        "activate_status": activate_resp["status_code"],
    }


def exec_onv005_cancel_cascade_payment(seed: dict) -> dict:
    """ONV-005: contract.status=CANCELLED → pending payments rejected.
    
    Bug: Cancel does NOT cascade reject pending payment_requests.
    """
    experiments = []

    # Full lifecycle setup
    setup = _setup_lifecycle_contract(40000.0, "CASCADE")
    cid, ms_id, inv_id = setup["cid"], setup["ms_id"], setup["inv_id"]
    experiments.append({"step": "setup_lifecycle", "activate_status": setup["activate_status"]})

    if setup["activate_status"] != 200:
        return {"target_id": "ONV-005", "oracle_result": "BLOCKED",
                "reason": "activate failed", "experiments": experiments,
                "experiment_count": len(experiments)}

    # Create payment request (DRAFT = pending)
    pay_resp = _post("/payment-requests", body={
        "contract_id": cid, "milestone_id": ms_id,
        "invoice_id": inv_id, "amount": 20000.0,
    })
    pay_id = pay_resp["body"].get("id", "")
    experiments.append({"step": "setup_create_payment", "response": pay_resp})

    # Verify payment is DRAFT (pending)
    pay_before = _get(f"/payment-requests/{pay_id}")
    experiments.append({"step": "observe_payment_before_cancel", "response": pay_before})

    # Cancel contract
    cancel_resp = _post(f"/contracts/{cid}/cancel")
    experiments.append({"step": "execute_cancel", "response": cancel_resp})

    # Check payment status after cancel
    pay_after = _get(f"/payment-requests/{pay_id}")
    experiments.append({"step": "observe_payment_after_cancel", "response": pay_after})
    pay_status_after = pay_after["body"].get("status", "")

    # Oracle: payment should be REJECTED after contract cancelled
    oracle_result = "PASS"
    violation_triggered = False
    if cancel_resp["status_code"] == 200:
        if pay_status_after != "REJECTED":
            oracle_result = "VIOLATION"
            violation_triggered = True

    return {
        "target_id": "ONV-005",
        "rule_id": "rule:src_68e5e273aaf8f71e:238",
        "expression_type": "IMPLIES",
        "experiments": experiments,
        "experiment_count": len(experiments),
        "control_result": "N/A",
        "oracle_result": oracle_result,
        "violation_triggered": violation_triggered,
        "evidence": {
            "payment_status_before": pay_before["body"].get("status", ""),
            "payment_status_after": pay_status_after,
            "expected_after": "REJECTED",
            "contract_status": "CANCELLED",
        },
        "finding": {
            "root_cause": "cancel_does_not_cascade_reject_pending_payments",
            "mechanism": "implication_cascade_violation",
            "operation": "POST /contracts/{id}/cancel",
        } if violation_triggered else None,
    }


def exec_onv006_temporal_invoice(seed: dict) -> dict:
    """ONV-006: invoice.issue_date <= payment_request.created_date.
    
    Bug: No check that invoice date is before payment creation date.
    Create invoice with FUTURE date → create payment → should reject but doesn't.
    """
    experiments = []

    # Full lifecycle setup
    setup = _setup_lifecycle_contract(50000.0, "TEMPORAL")
    cid, ms_id = setup["cid"], setup["ms_id"]
    experiments.append({"step": "setup_lifecycle", "activate_status": setup["activate_status"]})

    if setup["activate_status"] != 200:
        return {"target_id": "ONV-006", "oracle_result": "BLOCKED",
                "reason": "activate failed", "experiments": experiments,
                "experiment_count": len(experiments)}

    # Control: Create invoice with PAST date → create payment → should work
    ctrl_inv = _post("/invoices", body={
        "contract_id": cid, "invoice_no": f"INV-CTRL-{uuid.uuid4().hex[:8]}",
        "subtotal": 1000.0, "tax_amount": 0,
        "issue_date": "2026-01-01",  # Past date
    })
    ctrl_inv_id = ctrl_inv["body"].get("id", "")
    ctrl_pay = _post("/payment-requests", body={
        "contract_id": cid, "milestone_id": ms_id,
        "invoice_id": ctrl_inv_id, "amount": 1000.0,
    })
    experiments.append({"step": "control_valid_date_payment", "response": ctrl_pay})

    # Violation: Create invoice with FUTURE date (beyond today)
    future_date = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")
    viol_inv = _post("/invoices", body={
        "contract_id": cid, "invoice_no": f"INV-VIOL-{uuid.uuid4().hex[:8]}",
        "subtotal": 1000.0, "tax_amount": 0,
        "issue_date": future_date,  # Future date!
    })
    viol_inv_id = viol_inv["body"].get("id", "")
    experiments.append({"step": "violation_create_future_invoice", "response": viol_inv})

    viol_pay = _post("/payment-requests", body={
        "contract_id": cid, "milestone_id": ms_id,
        "invoice_id": viol_inv_id, "amount": 1000.0,
    })
    experiments.append({"step": "violation_payment_with_future_invoice", "response": viol_pay})

    # Oracle: payment with future invoice date should be rejected
    control_ok = ctrl_pay["status_code"] in (200, 201)
    future_rejected = viol_pay["status_code"] in (409, 422)

    oracle_result = "PASS"
    violation_triggered = False
    if control_ok and not future_rejected and viol_pay["status_code"] in (200, 201):
        oracle_result = "VIOLATION"
        violation_triggered = True

    return {
        "target_id": "ONV-006",
        "rule_id": "rule:src_281e48df1c4d2464:25",
        "expression_type": "TEMPORAL",
        "experiments": experiments,
        "experiment_count": len(experiments),
        "control_result": "PASS" if control_ok else "FAIL",
        "oracle_result": oracle_result,
        "violation_triggered": violation_triggered,
        "evidence": {
            "control_payment_status": ctrl_pay["status_code"],
            "future_invoice_date": future_date,
            "violation_payment_status": viol_pay["status_code"],
            "expected_rejection": True,
        },
        "finding": {
            "root_cause": "no_temporal_validation_invoice_date_vs_payment_date",
            "mechanism": "temporal_boundary_violation",
            "operation": "POST /payment-requests",
        } if violation_triggered else None,
    }


def exec_onv007_payment_limit(seed: dict) -> dict:
    """ONV-007: payment.amount <= milestone.accepted_amount - SUM(prior_payments).
    
    Create partial payment first, then try to exceed remaining.
    """
    experiments = []

    # Full lifecycle setup
    ms_amount = 50000.0
    setup = _setup_lifecycle_contract(ms_amount, "LIMIT")
    cid, ms_id, inv_id = setup["cid"], setup["ms_id"], setup["inv_id"]
    experiments.append({"step": "setup_lifecycle", "activate_status": setup["activate_status"]})

    if setup["activate_status"] != 200:
        return {"target_id": "ONV-007", "oracle_result": "BLOCKED",
                "reason": "activate failed", "experiments": experiments,
                "experiment_count": len(experiments)}

    # Control: Create payment within limit (40% of milestone)
    half = ms_amount * 0.4
    ctrl_pay = _post("/payment-requests", body={
        "contract_id": cid, "milestone_id": ms_id,
        "invoice_id": inv_id, "amount": half,
    })
    experiments.append({"step": "control_partial_payment", "response": ctrl_pay})

    # Violation: Try to create payment exceeding remaining (ms_amount - half + extra)
    remaining = ms_amount - half
    exceed_amount = remaining + 1000  # Clearly exceeds remaining
    viol_pay = _post("/payment-requests", body={
        "contract_id": cid, "milestone_id": ms_id,
        "invoice_id": inv_id, "amount": exceed_amount,
    })
    experiments.append({"step": "violation_exceed_remaining", "response": viol_pay})

    # Oracle
    control_ok = ctrl_pay["status_code"] in (200, 201)
    exceed_rejected = viol_pay["status_code"] in (409, 422, 500)

    oracle_result = "PASS"
    violation_triggered = False
    if control_ok and not exceed_rejected and viol_pay["status_code"] in (200, 201):
        oracle_result = "VIOLATION"
        violation_triggered = True

    return {
        "target_id": "ONV-007",
        "rule_id": "rule:src_f6008c42314fa17d:108",
        "expression_type": "LTE",
        "experiments": experiments,
        "experiment_count": len(experiments),
        "control_result": "PASS" if control_ok else "FAIL",
        "oracle_result": oracle_result,
        "violation_triggered": violation_triggered,
        "evidence": {
            "milestone_amount": ms_amount,
            "first_payment": half,
            "remaining": remaining,
            "exceed_amount": exceed_amount,
            "control_status": ctrl_pay["status_code"],
            "violation_status": viol_pay["status_code"],
        },
        "finding": {
            "root_cause": "payment_exceeds_milestone_remaining_accepted",
            "mechanism": "cumulative_limit_violation",
            "operation": "POST /payment-requests",
        } if violation_triggered else None,
    }


def exec_onv008_pay_cancelled_contract(seed: dict) -> dict:
    """ONV-008: contract.status=CANCELLED → pay operation rejected.
    
    Bug: execute_payment doesn't check contract.status.
    Setup: Create payment, get to FINANCE_APPROVED, cancel contract, then pay.
    """
    experiments = []

    # Full lifecycle setup
    setup = _setup_lifecycle_contract(25000.0, "PAYCANCEL")
    cid, ms_id, inv_id = setup["cid"], setup["ms_id"], setup["inv_id"]
    experiments.append({"step": "setup_lifecycle", "activate_status": setup["activate_status"]})

    if setup["activate_status"] != 200:
        return {"target_id": "ONV-008", "oracle_result": "BLOCKED",
                "reason": "activate failed", "experiments": experiments,
                "experiment_count": len(experiments)}

    # Create payment and get it to FINANCE_APPROVED
    pay_resp = _post("/payment-requests", body={
        "contract_id": cid, "milestone_id": ms_id,
        "invoice_id": inv_id, "amount": 10000.0,
    })
    pay_id = pay_resp["body"].get("id", "")
    experiments.append({"step": "setup_payment_created", "response": pay_resp})

    # Manager approve → Finance approve
    _post(f"/payment-requests/{pay_id}/manager-approve", token=ADMIN_TOKEN)
    fa_resp = _post(f"/payment-requests/{pay_id}/finance-approve", token=FINANCE_TOKEN)
    experiments.append({"step": "setup_finance_approved", "response": fa_resp})

    # Cancel the contract
    cancel_resp = _post(f"/contracts/{cid}/cancel")
    experiments.append({"step": "execute_cancel_contract", "response": cancel_resp})

    # Verify contract is CANCELLED
    c_after = _get(f"/contracts/{cid}")
    experiments.append({"step": "observe_contract_cancelled", "response": c_after})

    # Try to pay on cancelled contract
    idem_key = f"va-pay-{uuid.uuid4().hex[:16]}"
    pay_exec = _post(f"/payment-requests/{pay_id}/pay", token=FINANCE_TOKEN,
                     headers={"Idempotency-Key": idem_key})
    experiments.append({"step": "violation_pay_on_cancelled", "response": pay_exec})

    # Oracle: pay should be rejected because contract is CANCELLED
    contract_cancelled = c_after["body"].get("status") == "CANCELLED"
    pay_rejected = pay_exec["status_code"] in (409, 422, 403)

    oracle_result = "PASS"
    violation_triggered = False
    if contract_cancelled and not pay_rejected and pay_exec["status_code"] == 200:
        oracle_result = "VIOLATION"
        violation_triggered = True

    return {
        "target_id": "ONV-008",
        "rule_id": "rule:src_68e5e273aaf8f71e:238",
        "expression_type": "IMPLIES",
        "experiments": experiments,
        "experiment_count": len(experiments),
        "control_result": "N/A",
        "oracle_result": oracle_result,
        "violation_triggered": violation_triggered,
        "evidence": {
            "contract_status": c_after["body"].get("status", ""),
            "payment_status_before_pay": "FINANCE_APPROVED",
            "pay_response_code": pay_exec["status_code"],
            "expected_rejection": True,
        },
        "finding": {
            "root_cause": "pay_does_not_check_contract_status",
            "mechanism": "state_implication_violation",
            "operation": "POST /payment-requests/{id}/pay",
        } if violation_triggered else None,
    }


def exec_onv009_reserved_non_negative(seed: dict) -> dict:
    """ONV-009: budget.reserved_amount >= 0 (invariant).
    
    Bug: reserved_amount -= amount without checking >= 0.
    Setup: pay amount > reserved → reserved goes negative.
    """
    experiments = []

    # Full lifecycle setup with small amount
    setup = _setup_lifecycle_contract(15000.0, "NEGRES")
    cid, ms_id, inv_id, bud_id = setup["cid"], setup["ms_id"], setup["inv_id"], setup["bud_id"]
    experiments.append({"step": "setup_lifecycle", "activate_status": setup["activate_status"]})

    if setup["activate_status"] != 200:
        return {"target_id": "ONV-009", "oracle_result": "BLOCKED",
                "reason": "activate failed", "experiments": experiments,
                "experiment_count": len(experiments)}

    # Read budget before
    bud_before = _get(f"/budgets/{bud_id}")
    reserved_before = bud_before["body"].get("reserved_amount", 0)
    experiments.append({"step": "observe_budget_before", "response": bud_before})

    # Create payment for full 15000, approve it, pay it
    pay_resp = _post("/payment-requests", body={
        "contract_id": cid, "milestone_id": ms_id,
        "invoice_id": inv_id, "amount": 15000.0,
    })
    pay_id = pay_resp["body"].get("id", "")
    experiments.append({"step": "setup_payment_created", "response": pay_resp})

    _post(f"/payment-requests/{pay_id}/manager-approve", token=ADMIN_TOKEN)
    _post(f"/payment-requests/{pay_id}/finance-approve", token=FINANCE_TOKEN)

    # Pay (this reduces reserved by 15000)
    idem_key = f"va-nr-{uuid.uuid4().hex[:16]}"
    pay_exec = _post(f"/payment-requests/{pay_id}/pay", token=FINANCE_TOKEN,
                     headers={"Idempotency-Key": idem_key})
    experiments.append({"step": "execute_payment", "response": pay_exec})

    # Read budget after pay
    bud_after = _get(f"/budgets/{bud_id}")
    reserved_after = bud_after["body"].get("reserved_amount", 0)
    experiments.append({"step": "observe_budget_after_pay", "response": bud_after})

    # Check if reserved went negative
    oracle_result = "PASS"
    violation_triggered = False
    if reserved_after < 0:
        oracle_result = "VIOLATION"
        violation_triggered = True

    pay_succeeded = pay_exec["status_code"] == 200

    return {
        "target_id": "ONV-009",
        "rule_id": "rule:src_f6008c42314fa17d:130",
        "expression_type": "LTE",
        "experiments": experiments,
        "experiment_count": len(experiments),
        "control_result": "N/A",
        "oracle_result": oracle_result,
        "violation_triggered": violation_triggered,
        "evidence": {
            "reserved_before": reserved_before,
            "reserved_after": reserved_after,
            "payment_amount": 15000.0,
            "pay_succeeded": pay_succeeded,
            "reserved_went_negative": reserved_after < 0,
        },
        "finding": {
            "root_cause": "no_reserved_amount_non_negative_check",
            "mechanism": "invariant_non_negative_violation",
            "operation": "POST /payment-requests/{id}/pay",
        } if violation_triggered else None,
    }


# ─── Main Runner ──────────────────────────────────────────────────────────────

def run_small_scale():
    """Execute all 9 target experiments."""
    print(f"{'='*70}")
    print(f"  VIOLATION ACTIVATION - TARGETED SMALL SCALE")
    print(f"  Run ID: {RUN_ID}")
    print(f"  Max Experiments: {MAX_EXPERIMENTS}")
    print(f"{'='*70}")
    print()

    # Check server is up
    health = _get("/../")
    if health["status_code"] == 0:
        print("[ERROR] Mock server not reachable at", BASE_URL)
        print("Start it with: python projects/contractflow_c/mock_server.py")
        sys.exit(1)

    print("[1/3] Discovering seed data...")
    seed = discover_seed_data()
    print(f"  Contracts: {len(seed['contracts'])}")
    print(f"  Budgets: {len(seed['budgets'])}")
    for cid, ms in seed["milestones"].items():
        print(f"  Milestones[{cid[:8]}]: {len(ms)}")
    print()

    # Execute all targets
    executors = [
        ("ONV-001", exec_onv001_version_conflict),
        ("ONV-002", exec_onv002_milestone_sum),
        ("ONV-003", exec_onv003_budget_reserve),
        ("ONV-004", exec_onv004_cancel_budget_release),
        ("ONV-005", exec_onv005_cancel_cascade_payment),
        ("ONV-006", exec_onv006_temporal_invoice),
        ("ONV-007", exec_onv007_payment_limit),
        ("ONV-008", exec_onv008_pay_cancelled_contract),
        ("ONV-009", exec_onv009_reserved_non_negative),
    ]

    print("[2/3] Executing violation experiments...")
    results = []
    total_experiments = 0
    for target_id, executor in executors:
        print(f"\n  [{target_id}] Executing...")
        try:
            result = executor(seed)
            total_experiments += result.get("experiment_count", 0)
            results.append(result)
            status = result.get("oracle_result", "?")
            vt = result.get("violation_triggered", False)
            icon = "VIOLATION" if vt else ("PASS" if status == "PASS" else status)
            print(f"  [{target_id}] Result: {icon}")
        except Exception as e:
            print(f"  [{target_id}] ERROR: {e}")
            results.append({"target_id": target_id, "oracle_result": "ERROR", "error": str(e)})

        if total_experiments >= MAX_EXPERIMENTS:
            print(f"\n  [WARN] Reached max experiments ({MAX_EXPERIMENTS})")
            break

    # Summary
    print(f"\n{'='*70}")
    print(f"  RESULTS SUMMARY")
    print(f"{'='*70}")
    violations = sum(1 for r in results if r.get("violation_triggered"))
    passes = sum(1 for r in results if r.get("oracle_result") == "PASS" and not r.get("violation_triggered"))
    blocked = sum(1 for r in results if r.get("oracle_result") == "BLOCKED")
    errors = sum(1 for r in results if r.get("oracle_result") == "ERROR")

    print(f"\n  Total targets: {len(results)}")
    print(f"  Total experiments: {total_experiments}")
    print(f"  VIOLATION_TRIGGERED: {violations}")
    print(f"  TRUE_PASS: {passes}")
    print(f"  BLOCKED: {blocked}")
    print(f"  ERROR: {errors}")
    print()

    print(f"  {'Target':<10} {'Type':<14} {'Oracle':<12} {'Violation':<10}")
    print(f"  {'-'*50}")
    for r in results:
        tid = r.get("target_id", "?")
        etype = r.get("expression_type", "?")
        oracle = r.get("oracle_result", "?")
        vt = "YES" if r.get("violation_triggered") else "no"
        print(f"  {tid:<10} {etype:<14} {oracle:<12} {vt:<10}")

    # Findings
    findings = [r.get("finding") for r in results if r.get("finding")]
    if findings:
        print(f"\n  NEW FINDINGS ({len(findings)}):")
        for i, f in enumerate(findings, 1):
            print(f"    {i}. [{f['mechanism']}] {f['root_cause']}")
            print(f"       Operation: {f['operation']}")

    # Save results
    output = {
        "schema_version": "qualibug.violation-activation-small-scale.v1",
        "run_id": RUN_ID,
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_url": BASE_URL,
        "total_experiments": total_experiments,
        "max_experiments": MAX_EXPERIMENTS,
        "summary": {
            "total_targets": len(results),
            "violation_triggered": violations,
            "true_pass": passes,
            "blocked": blocked,
            "errors": errors,
            "findings_count": len(findings),
        },
        "results": results,
        "findings": findings,
    }

    out_path = Path("project_c_violation_activation_formal_results.json")
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n  Results saved: {out_path}")
    print(f"\n{'='*70}")

    return output


if __name__ == "__main__":
    run_small_scale()

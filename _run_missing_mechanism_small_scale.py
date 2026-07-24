"""Missing Mechanism Planning — Targeted Small Scale Execution.

Executes the 5 new mechanism experiments (UNIQUENESS_VIOLATION,
FIELD_INVARIANT_VIOLATION, PRECONDITION_VIOLATION, AUTHORIZATION_MATRIX,
TENANT_ISOLATION_MATRIX) against the ContractFlow mock server.

Run ID: PROJECT_C_MISSING_MECHANISM_SMALL_SCALE_V1
Max experiments: 30 (SPEC §31)
"""
from __future__ import annotations

import json
import sys
import time
import uuid
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from ai_test_asset_center.deep_experiment_planner import plan_deep_experiments

BASE_URL = "http://127.0.0.1:8000/api/v1"
MAX_EXPERIMENTS = 30
RUN_ID = "PROJECT_C_MISSING_MECHANISM_SMALL_SCALE_V1"

# ─── Tokens ────────────────────────────────────────────────────────────────────
TOKENS = {
    "acme_admin": "acme-admin-token",
    "acme_legal": "acme-legal-token",
    "acme_finance": "acme-finance-token",
    "acme_requester": "acme-requester-token",
    "acme_manager": "acme-manager-token",
    "acme_auditor": "acme-auditor-token",
    "acme_vendor": "acme-vendor-token",
    "globex_admin": "globex-admin-token",
    "globex_requester": "globex-requester-token",
    "globex_finance": "globex-finance-token",
}

# ─── HTTP Client ───────────────────────────────────────────────────────────────

def _request(method: str, path: str, *, token: str = "acme-admin-token",
             body: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
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


# ─── Behavior IR (minimal, source-derived) ─────────────────────────────────────

def build_behavior_ir() -> dict:
    """Build minimal Behavior IR from test_accounts.json and openapi routes."""
    actors = [
        {"id": "acme_admin", "role": "admin", "tenant": "acme",
         "credential_secret_ref": "acme-admin-token"},
        {"id": "acme_legal", "role": "legal", "tenant": "acme",
         "credential_secret_ref": "acme-legal-token"},
        {"id": "acme_finance", "role": "finance", "tenant": "acme",
         "credential_secret_ref": "acme-finance-token"},
        {"id": "acme_requester", "role": "requester", "tenant": "acme",
         "credential_secret_ref": "acme-requester-token"},
        {"id": "acme_manager", "role": "project_manager", "tenant": "acme",
         "credential_secret_ref": "acme-manager-token"},
        {"id": "acme_auditor", "role": "auditor", "tenant": "acme",
         "credential_secret_ref": "acme-auditor-token"},
        {"id": "acme_vendor", "role": "vendor", "tenant": "acme",
         "credential_secret_ref": "acme-vendor-token"},
        {"id": "globex_admin", "role": "admin", "tenant": "globex",
         "credential_secret_ref": "globex-admin-token"},
        {"id": "globex_requester", "role": "requester", "tenant": "globex",
         "credential_secret_ref": "globex-requester-token"},
        {"id": "globex_finance", "role": "finance", "tenant": "globex",
         "credential_secret_ref": "globex-finance-token"},
    ]

    operations = [
        {"id": "op_create_invoice", "method": "POST", "path": "/invoices",
         "service": "contractflow",
         "request_example": {
             "contract_id": "", "invoice_no": "", "subtotal": 1000.0,
             "tax_amount": 100.0, "issue_date": "2026-06-01", "vendor_id": "",
         }},
        {"id": "op_create_payment", "method": "POST", "path": "/payment-requests",
         "service": "contractflow",
         "request_example": {
             "contract_id": "", "milestone_id": "", "invoice_id": "",
             "amount": 5000.0,
         }},
        {"id": "op_legal_approve", "method": "POST",
         "path": "/contracts/{contract_id}/legal-approve",
         "service": "contractflow", "request_example": {}},
        {"id": "op_get_contract", "method": "GET",
         "path": "/contracts/{contract_id}",
         "service": "contractflow", "request_example": {}},
        {"id": "op_get_invoice", "method": "GET", "path": "/invoices",
         "service": "contractflow", "request_example": {}},
    ]

    invariants = [
        {"id": "BR-INV-001", "rule_type": "UNIQUENESS",
         "entity_ref": "invoice",
         "description": "同一供应商发票号唯一",
         "expression": {"unique_fields": ["vendor_id", "invoice_no"],
                        "field": "invoice_no", "scope": "vendor"}},
        {"id": "BR-INV-002", "rule_type": "FIELD_INVARIANT",
         "entity_ref": "invoice",
         "description": "发票金额和税额不得为负",
         "expression": {"field": "subtotal", "constraint": "non_negative",
                        "fields": ["subtotal", "tax_amount"]}},
        {"id": "BR-PAY-001", "rule_type": "PRECONDITION",
         "entity_ref": "payment_request",
         "description": "付款必须关联ACTIVE合同",
         "expression": {"required_state": "ACTIVE", "entity": "contract",
                        "wrong_state": "APPROVED",
                        "precondition_state": "ACTIVE"}},
        {"id": "BR-SEC-003", "rule_type": "AUTHORIZATION",
         "entity_ref": "contract",
         "description": "只有legal可完成法务批准",
         "expression": {"authorized_role": "legal", "action": "legal-approve"}},
        {"id": "BR-SEC-001", "rule_type": "TENANT_ISOLATION",
         "entity_ref": "contract",
         "description": "所有业务实体禁止跨租户访问",
         "expression": {"scope": "all_entities", "isolation": "tenant"}},
    ]

    return {
        "actors": actors,
        "operations": operations,
        "invariants": invariants,
        "states": [],
        "relations": [],
    }


# ─── Obligations ───────────────────────────────────────────────────────────────

def build_obligations() -> list[dict]:
    """Build obligations for the 5 target rules."""
    return [
        {
            "obligation_id": "obl_BR-INV-001",
            "risk_family": "uniqueness",
            "property": {
                "invariant_ref": "BR-INV-001",
                "operation_ref": "op_create_invoice",
                "actor_ref": "acme_admin",
                "expression": {
                    "rule_type": "UNIQUENESS",
                    "unique_fields": ["vendor_id", "invoice_no"],
                    "field": "invoice_no",
                    "scope": "vendor",
                },
            },
            "source_refs": [{"rule_id": "BR-INV-001", "source": "BUSINESS_RULES.md"}],
        },
        {
            "obligation_id": "obl_BR-INV-002",
            "risk_family": "field_invariant",
            "property": {
                "invariant_ref": "BR-INV-002",
                "operation_ref": "op_create_invoice",
                "actor_ref": "acme_admin",
                "expression": {
                    "rule_type": "FIELD_INVARIANT",
                    "field": "subtotal",
                    "constraint": "non_negative",
                    "fields": ["subtotal", "tax_amount"],
                },
            },
            "source_refs": [{"rule_id": "BR-INV-002", "source": "BUSINESS_RULES.md"}],
        },
        {
            "obligation_id": "obl_BR-PAY-001",
            "risk_family": "precondition",
            "property": {
                "invariant_ref": "BR-PAY-001",
                "operation_ref": "op_create_payment",
                "actor_ref": "acme_admin",
                "expression": {
                    "rule_type": "PRECONDITION",
                    "required_state": "ACTIVE",
                    "entity": "contract",
                    "wrong_state": "APPROVED",
                    "precondition_state": "ACTIVE",
                },
            },
            "source_refs": [{"rule_id": "BR-PAY-001", "source": "BUSINESS_RULES.md"}],
        },
        {
            "obligation_id": "obl_BR-SEC-003",
            "risk_family": "authorization",
            "property": {
                "invariant_ref": "BR-SEC-003",
                "operation_ref": "op_legal_approve",
                "actor_ref": "acme_legal",
                "expression": {
                    "rule_type": "AUTHORIZATION",
                    "authorized_role": "legal",
                    "action": "legal-approve",
                },
            },
            "source_refs": [{"rule_id": "BR-SEC-003", "source": "BUSINESS_RULES.md"}],
        },
        {
            "obligation_id": "obl_BR-SEC-001",
            "risk_family": "isolation",
            "property": {
                "invariant_ref": "BR-SEC-001",
                "operation_ref": "op_get_contract",
                "actor_ref": "acme_admin",
                "expression": {
                    "rule_type": "TENANT_ISOLATION",
                    "scope": "all_entities",
                    "isolation": "tenant",
                },
            },
            "source_refs": [{"rule_id": "BR-SEC-001", "source": "BUSINESS_RULES.md"}],
        },
    ]


# ─── Seed Data Discovery ───────────────────────────────────────────────────────

def discover_seed_data() -> dict:
    """Discover seed data from the running server."""
    result = {"contracts": [], "invoices": [], "vendors": [], "contract_active": None,
              "contract_approved": None, "milestone_accepted": None}

    # Get contracts
    resp = _get("/contracts")
    contracts = resp["body"]
    if isinstance(contracts, dict):
        contracts = contracts.get("items") or contracts.get("data") or []
    if isinstance(contracts, list):
        result["contracts"] = contracts
        for c in contracts:
            if c.get("status") == "ACTIVE":
                result["contract_active"] = c
            elif c.get("status") == "APPROVED":
                result["contract_approved"] = c

    # Get invoices
    resp = _get("/invoices")
    invoices = resp["body"]
    if isinstance(invoices, dict):
        invoices = invoices.get("items") or invoices.get("data") or []
    if isinstance(invoices, list):
        result["invoices"] = invoices

    # Get vendors
    resp = _get("/vendors")
    vendors = resp["body"]
    if isinstance(vendors, dict):
        vendors = vendors.get("items") or vendors.get("data") or []
    if isinstance(vendors, list):
        result["vendors"] = vendors

    # Get milestones for first contract
    if contracts:
        cid = contracts[0]["id"]
        resp = _get(f"/contracts/{cid}/milestones")
        milestones = resp["body"]
        if isinstance(milestones, dict):
            milestones = milestones.get("items") or milestones.get("data") or []
        if isinstance(milestones, list):
            for m in milestones:
                if m.get("status") == "ACCEPTED":
                    result["milestone_accepted"] = m
                    break

    return result


# ─── Experiment Executor ───────────────────────────────────────────────────────

def execute_experiment(exp: dict, seed: dict) -> dict:
    """Execute a single deep experiment against the mock server.

    Returns execution receipt with control/treatment results.
    """
    mechanism = exp.get("mechanism", "")
    rule_id = exp.get("rule_id", "")
    obligation_id = exp.get("obligation_id", "")

    receipt = {
        "experiment_id": exp.get("experiment_id"),
        "obligation_id": obligation_id,
        "rule_id": rule_id,
        "mechanism": mechanism,
        "control_result": None,
        "treatment_results": [],
        "verdict": "INDETERMINATE",
        "bug_detected": False,
        "details": {},
    }

    # Dispatch to mechanism-specific executor
    if mechanism == "UNIQUENESS_VIOLATION":
        _exec_uniqueness(receipt, seed)
    elif mechanism == "FIELD_INVARIANT_VIOLATION":
        _exec_field_invariant(receipt, seed)
    elif mechanism == "PRECONDITION_VIOLATION":
        _exec_precondition(receipt, seed)
    elif mechanism == "AUTHORIZATION_MATRIX":
        _exec_authorization(receipt, seed)
    elif mechanism == "TENANT_ISOLATION_MATRIX":
        _exec_tenant_isolation(receipt, seed)
    else:
        receipt["verdict"] = "MECHANISM_NOT_EXECUTABLE"
        receipt["details"]["reason"] = f"No executor for {mechanism}"

    return receipt


def _exec_uniqueness(receipt: dict, seed: dict):
    """BR-INV-001: Same vendor + same invoice_no should be rejected on 2nd create."""
    contracts = seed.get("contracts") or []
    if not contracts:
        receipt["verdict"] = "BLOCKED"
        receipt["details"]["reason"] = "no_contracts_available"
        return

    cid = contracts[0]["id"]
    vendor_id = contracts[0].get("vendor_id", "")
    unique_inv_no = f"UNIQ-TEST-{uuid.uuid4().hex[:8]}"

    # Control: first create should succeed
    body1 = {
        "contract_id": cid, "invoice_no": unique_inv_no,
        "subtotal": 1000.0, "tax_amount": 100.0,
        "issue_date": "2026-06-15", "vendor_id": vendor_id,
    }
    r1 = _post("/invoices", body1)
    receipt["control_result"] = {"status_code": r1["status_code"], "body": r1["body"]}

    if r1["status_code"] not in (200, 201):
        receipt["verdict"] = "CONTROL_FAILED"
        receipt["details"]["reason"] = f"control_create_failed_{r1['status_code']}"
        return

    # Violation: same vendor + same invoice_no → should be rejected (409)
    body2 = dict(body1)  # exact same vendor_id + invoice_no
    r2 = _post("/invoices", body2)
    receipt["treatment_results"].append({
        "mutation_type": "uniqueness_duplicate_create",
        "status_code": r2["status_code"],
        "body": r2["body"],
        "expected": "rejected (409)",
    })

    # Evaluate: if duplicate accepted (201), bug detected
    if r2["status_code"] in (200, 201):
        receipt["verdict"] = "VIOLATION_NOT_REJECTED"
        receipt["bug_detected"] = True
        receipt["details"] = {
            "bug_type": "UNIQUENESS_NOT_ENFORCED",
            "description": f"Duplicate invoice_no={unique_inv_no} accepted (status {r2['status_code']})",
            "rule": "BR-INV-001",
        }
    elif r2["status_code"] == 409:
        receipt["verdict"] = "PROPERTY_HELD"
        receipt["details"] = {"description": "Duplicate correctly rejected with 409"}
    else:
        receipt["verdict"] = "INDETERMINATE"
        receipt["details"] = {"status_code": r2["status_code"]}


def _exec_field_invariant(receipt: dict, seed: dict):
    """BR-INV-002: Negative subtotal/tax_amount should be rejected."""
    contracts = seed.get("contracts") or []
    if not contracts:
        receipt["verdict"] = "BLOCKED"
        receipt["details"]["reason"] = "no_contracts_available"
        return

    cid = contracts[0]["id"]
    vendor_id = contracts[0].get("vendor_id", "")

    # Control: valid positive amounts
    body_ok = {
        "contract_id": cid, "invoice_no": f"FINV-OK-{uuid.uuid4().hex[:8]}",
        "subtotal": 500.0, "tax_amount": 50.0,
        "issue_date": "2026-06-15", "vendor_id": vendor_id,
    }
    r_ok = _post("/invoices", body_ok)
    receipt["control_result"] = {"status_code": r_ok["status_code"], "body": r_ok["body"]}

    if r_ok["status_code"] not in (200, 201):
        receipt["verdict"] = "CONTROL_FAILED"
        receipt["details"]["reason"] = f"control_create_failed_{r_ok['status_code']}"
        return

    # Violation 1: negative subtotal
    body_neg = {
        "contract_id": cid, "invoice_no": f"FINV-NEG-{uuid.uuid4().hex[:8]}",
        "subtotal": -100.0, "tax_amount": 50.0,
        "issue_date": "2026-06-15", "vendor_id": vendor_id,
    }
    r_neg = _post("/invoices", body_neg)
    receipt["treatment_results"].append({
        "mutation_type": "field_invariant_negative_subtotal",
        "status_code": r_neg["status_code"],
        "body": r_neg["body"],
        "expected": "rejected (422)",
    })

    # Violation 2: negative tax_amount
    body_neg_tax = {
        "contract_id": cid, "invoice_no": f"FINV-NEGTAX-{uuid.uuid4().hex[:8]}",
        "subtotal": 500.0, "tax_amount": -50.0,
        "issue_date": "2026-06-15", "vendor_id": vendor_id,
    }
    r_neg_tax = _post("/invoices", body_neg_tax)
    receipt["treatment_results"].append({
        "mutation_type": "field_invariant_negative_tax",
        "status_code": r_neg_tax["status_code"],
        "body": r_neg_tax["body"],
        "expected": "rejected (422)",
    })

    # Evaluate
    neg_accepted = r_neg["status_code"] in (200, 201)
    neg_tax_accepted = r_neg_tax["status_code"] in (200, 201)

    if neg_accepted or neg_tax_accepted:
        receipt["verdict"] = "VIOLATION_NOT_REJECTED"
        receipt["bug_detected"] = True
        accepted_fields = []
        if neg_accepted:
            accepted_fields.append("subtotal=-100")
        if neg_tax_accepted:
            accepted_fields.append("tax_amount=-50")
        receipt["details"] = {
            "bug_type": "FIELD_INVARIANT_NOT_ENFORCED",
            "description": f"Negative values accepted: {', '.join(accepted_fields)}",
            "rule": "BR-INV-002",
        }
    else:
        receipt["verdict"] = "PROPERTY_HELD"
        receipt["details"] = {"description": "Negative amounts correctly rejected"}


def _exec_precondition(receipt: dict, seed: dict):
    """BR-PAY-001: Payment must reference ACTIVE contract.
    Also tests: payment execution on cancelled contract (CF-STATE-004 pattern).
    """
    contract_approved = seed.get("contract_approved")
    contract_active = seed.get("contract_active")
    milestone = seed.get("milestone_accepted")
    invoices = seed.get("invoices") or []

    if not contract_approved and not contract_active:
        receipt["verdict"] = "BLOCKED"
        receipt["details"]["reason"] = "no_suitable_contracts"
        return

    # Find an invoice for the test
    invoice = invoices[0] if invoices else None

    # Control: payment with ACTIVE contract should succeed
    if contract_active and milestone and invoice:
        body_ctrl = {
            "contract_id": contract_active["id"],
            "milestone_id": milestone["id"],
            "invoice_id": invoice["id"],
            "amount": 1000.0,
        }
        r_ctrl = _post("/payment-requests", body_ctrl)
        receipt["control_result"] = {"status_code": r_ctrl["status_code"], "body": r_ctrl["body"]}
    else:
        receipt["control_result"] = {"status_code": 0, "body": {},
                                     "note": "no_active_contract_for_control"}

    # Violation 1: payment with APPROVED (not ACTIVE) contract
    if contract_approved and milestone and invoice:
        body_viol = {
            "contract_id": contract_approved["id"],
            "milestone_id": milestone["id"],
            "invoice_id": invoice["id"],
            "amount": 1000.0,
        }
        r_viol = _post("/payment-requests", body_viol)
        receipt["treatment_results"].append({
            "mutation_type": "precondition_violated",
            "status_code": r_viol["status_code"],
            "body": r_viol["body"],
            "expected": "rejected (409)",
            "contract_status": contract_approved.get("status"),
        })

        if r_viol["status_code"] in (200, 201):
            receipt["verdict"] = "VIOLATION_NOT_REJECTED"
            receipt["bug_detected"] = True
            receipt["details"] = {
                "bug_type": "PRECONDITION_NOT_ENFORCED",
                "description": f"Payment created with contract status={contract_approved.get('status')} (not ACTIVE)",
                "rule": "BR-PAY-001",
            }
        elif r_viol["status_code"] == 409:
            receipt["verdict"] = "PROPERTY_HELD"
            receipt["details"] = {"description": "Non-ACTIVE contract payment correctly rejected"}
        else:
            receipt["verdict"] = "INDETERMINATE"
            receipt["details"] = {"status_code": r_viol["status_code"]}
    else:
        receipt["verdict"] = "BLOCKED"
        receipt["details"]["reason"] = "no_approved_contract_or_milestone_for_violation"

    # Violation 2: Execute payment on CANCELLED contract (CF-STATE-004 pattern)
    # This tests whether _execute_payment checks contract status
    _exec_payment_on_cancelled_contract(receipt, seed)


def _exec_payment_on_cancelled_contract(receipt: dict, seed: dict):
    """Test: execute payment after contract is cancelled.

    Creates a fresh contract, activates it, creates payment,
    gets it to FINANCE_APPROVED, cancels contract, then executes payment.
    """
    # Get budget for fresh contract
    r_budgets = _get("/budgets")
    budgets = r_budgets["body"]
    if isinstance(budgets, dict):
        budgets = budgets.get("items") or budgets.get("data") or []
    if not isinstance(budgets, list) or not budgets:
        receipt["treatment_results"].append({
            "mutation_type": "payment_on_cancelled_contract",
            "status_code": 0, "note": "no_budget",
        })
        return
    budget = budgets[0]
    budget_id = budget["id"]
    available = budget.get("available_amount", 0)

    # Get reference data
    r_depts = _get("/reference/departments")
    depts = r_depts["body"]
    if isinstance(depts, dict):
        depts = depts.get("items") or depts.get("data") or []
    dept_id = depts[0]["id"] if isinstance(depts, list) and depts else ""
    r_vendors = _get("/reference/vendors")
    vendors = r_vendors["body"]
    if isinstance(vendors, dict):
        vendors = vendors.get("items") or vendors.get("data") or []
    vendor_id = vendors[0]["id"] if isinstance(vendors, list) and vendors else ""

    # Use small amount to fit in budget
    amount = min(5000.0, available * 0.1) if available > 0 else 5000.0
    suffix = uuid.uuid4().hex[:6]

    # 1. Create contract
    r_c = _post("/contracts", {
        "contract_no": f"CANCEL-TEST-{suffix}",
        "title": f"Cancel Test {suffix}",
        "department_id": dept_id, "vendor_id": vendor_id,
        "budget_id": budget_id, "total_amount": amount,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    if r_c["status_code"] not in (200, 201):
        receipt["treatment_results"].append({
            "mutation_type": "payment_on_cancelled_contract",
            "status_code": r_c["status_code"],
            "note": f"contract_create_failed: {r_c['body']}",
        })
        return
    cid = r_c["body"]["id"]

    # 2. Add milestone
    _post(f"/contracts/{cid}/milestones", {
        "name": "M1", "amount": amount, "due_date": "2026-06-30",
    })

    # 3. Submit -> Approve -> Activate
    _post(f"/contracts/{cid}/submit")
    _post(f"/contracts/{cid}/legal-approve", token=TOKENS["acme_legal"])
    r_act = _post(f"/contracts/{cid}/activate")
    if r_act["status_code"] not in (200, 201):
        receipt["treatment_results"].append({
            "mutation_type": "payment_on_cancelled_contract",
            "status_code": r_act["status_code"],
            "note": f"activate_failed: {r_act['body']}",
        })
        return

    # 4. Get accepted milestone (submit + accept it)
    r_ms = _get(f"/contracts/{cid}/milestones")
    ms_list = r_ms["body"] if isinstance(r_ms["body"], list) else []
    if not ms_list:
        receipt["treatment_results"].append({
            "mutation_type": "payment_on_cancelled_contract",
            "status_code": 0, "note": "no_milestones",
        })
        return
    mid = ms_list[0]["id"]
    _post(f"/milestones/{mid}/submit", {"evidence_url": "http://test/evidence.pdf"})
    r_accept = _post(f"/milestones/{mid}/accept", {"accepted_amount": amount})
    if r_accept["status_code"] not in (200, 201):
        receipt["treatment_results"].append({
            "mutation_type": "payment_on_cancelled_contract",
            "status_code": r_accept["status_code"],
            "note": f"milestone_accept_failed: {r_accept['body']}",
        })
        return

    # 5. Create invoice
    r_inv = _post("/invoices", {
        "contract_id": cid, "invoice_no": f"CANCEL-INV-{suffix}",
        "subtotal": amount, "tax_amount": 0.0,
        "issue_date": "2026-06-01", "vendor_id": vendor_id,
    })
    if r_inv["status_code"] not in (200, 201):
        receipt["treatment_results"].append({
            "mutation_type": "payment_on_cancelled_contract",
            "status_code": r_inv["status_code"],
            "note": f"invoice_create_failed: {r_inv['body']}",
        })
        return
    invoice_id = r_inv["body"]["id"]

    # 6. Create payment
    pay_amount = amount * 0.5
    r_pay = _post("/payment-requests", {
        "contract_id": cid, "milestone_id": mid,
        "invoice_id": invoice_id, "amount": pay_amount,
    })
    if r_pay["status_code"] not in (200, 201):
        receipt["treatment_results"].append({
            "mutation_type": "payment_on_cancelled_contract",
            "status_code": r_pay["status_code"],
            "note": f"payment_create_failed: {r_pay['body']}",
        })
        return
    pay_id = r_pay["body"]["id"]

    # 7. Manager approve + Finance approve
    _post(f"/payment-requests/{pay_id}/manager-approve")
    r_fin = _post(f"/payment-requests/{pay_id}/finance-approve", token=TOKENS["acme_finance"])
    if r_fin["status_code"] not in (200, 201):
        receipt["treatment_results"].append({
            "mutation_type": "payment_on_cancelled_contract",
            "status_code": r_fin["status_code"],
            "note": f"finance_approve_failed: {r_fin['body']}",
        })
        return

    # 8. Cancel the contract
    r_cancel = _post(f"/contracts/{cid}/cancel")
    if r_cancel["status_code"] not in (200, 201):
        receipt["treatment_results"].append({
            "mutation_type": "payment_on_cancelled_contract",
            "status_code": r_cancel["status_code"],
            "note": f"cancel_failed: {r_cancel['body']}",
        })
        return

    # 9. Execute payment on CANCELLED contract
    idem_key = f"cancel-test-{uuid.uuid4().hex[:16]}"
    url = f"{BASE_URL}/payment-requests/{pay_id}/pay"
    req = urllib.request.Request(url, data=json.dumps({}).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {TOKENS['acme_finance']}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Idempotency-Key", idem_key)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            exec_status = resp.status
            exec_body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        exec_status = e.code
        exec_body = json.loads(e.read().decode())
    except Exception as e:
        exec_status = 0
        exec_body = {"error": str(e)}

    receipt["treatment_results"].append({
        "mutation_type": "payment_execution_on_cancelled_contract",
        "status_code": exec_status,
        "body": exec_body,
        "expected": "rejected (409) because contract is CANCELLED",
        "contract_status_after_cancel": "CANCELLED",
    })

    if exec_status in (200, 201):
        receipt["verdict"] = "VIOLATION_NOT_REJECTED"
        receipt["bug_detected"] = True
        receipt["details"] = {
            "bug_type": "PRECONDITION_NOT_ENFORCED_ON_EXECUTION",
            "description": f"Payment executed on CANCELLED contract (status={exec_status}). "
                           f"_execute_payment does not check contract.status.",
            "rule": "BR-PAY-001 / BR-CON-011",
        }
    elif exec_status == 409:
        if receipt["verdict"] != "VIOLATION_NOT_REJECTED":
            receipt["verdict"] = "PROPERTY_HELD"
        receipt["details"]["cancel_test"] = "Payment on cancelled contract correctly rejected"


def _exec_authorization(receipt: dict, seed: dict):
    """BR-SEC-003: Only legal role can do legal-approve."""
    # Get budget and reference data for contract creation
    r_budgets = _get("/budgets")
    budgets = r_budgets["body"]
    if isinstance(budgets, dict):
        budgets = budgets.get("items") or budgets.get("data") or []
    budget_id = budgets[0]["id"] if isinstance(budgets, list) and budgets else ""

    r_depts = _get("/reference/departments")
    depts = r_depts["body"]
    if isinstance(depts, dict):
        depts = depts.get("items") or depts.get("data") or []
    dept_id = depts[0]["id"] if isinstance(depts, list) and depts else ""

    r_vendors = _get("/reference/vendors")
    vendors = r_vendors["body"]
    if isinstance(vendors, dict):
        vendors = vendors.get("items") or vendors.get("data") or []
    vendor_id = vendors[0]["id"] if isinstance(vendors, list) and vendors else ""

    if not budget_id:
        receipt["verdict"] = "BLOCKED"
        receipt["details"]["reason"] = "no_budget_available"
        return

    # Create contract and advance to LEGAL_REVIEW
    suffix = uuid.uuid4().hex[:6]
    create_body = {
        "contract_no": f"AUTHZ-TEST-{suffix}",
        "title": f"Authz Test {suffix}",
        "department_id": dept_id,
        "vendor_id": vendor_id,
        "budget_id": budget_id,
        "total_amount": 10000.0,
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
    }
    r_create = _post("/contracts", create_body)
    if r_create["status_code"] not in (200, 201):
        receipt["verdict"] = "BLOCKED"
        receipt["details"]["reason"] = f"contract_create_failed_{r_create['status_code']}: {r_create['body']}"
        return

    cid = r_create["body"]["id"]

    # Add milestone (required for submit)
    r_ms = _post(f"/contracts/{cid}/milestones", {
        "name": "M1", "amount": 10000.0, "due_date": "2026-06-30",
    })
    if r_ms["status_code"] not in (200, 201):
        receipt["verdict"] = "BLOCKED"
        receipt["details"]["reason"] = f"milestone_create_failed_{r_ms['status_code']}"
        return

    # Submit to LEGAL_REVIEW
    r_submit = _post(f"/contracts/{cid}/submit")
    if r_submit["status_code"] not in (200, 201):
        receipt["verdict"] = "BLOCKED"
        receipt["details"]["reason"] = f"submit_failed_{r_submit['status_code']}: {r_submit['body']}"
        return

    # Control: legal role approves -> should succeed
    r_legal = _post(f"/contracts/{cid}/legal-approve", token=TOKENS["acme_legal"])
    receipt["control_result"] = {"status_code": r_legal["status_code"], "body": r_legal["body"]}

    if r_legal["status_code"] not in (200, 201):
        receipt["verdict"] = "CONTROL_FAILED"
        receipt["details"]["reason"] = f"legal_approve_failed_{r_legal['status_code']}"
        return

    # Create second contract for violation test
    suffix2 = uuid.uuid4().hex[:6]
    create_body2 = dict(create_body)
    create_body2["contract_no"] = f"AUTHZ-VIOL-{suffix2}"
    create_body2["title"] = f"Authz Violation {suffix2}"
    r_create2 = _post("/contracts", create_body2)
    if r_create2["status_code"] not in (200, 201):
        receipt["verdict"] = "BLOCKED"
        receipt["details"]["reason"] = "cannot_create_second_contract"
        return

    cid2 = r_create2["body"]["id"]
    _post(f"/contracts/{cid2}/milestones", {"name": "M1", "amount": 10000.0, "due_date": "2026-06-30"})
    _post(f"/contracts/{cid2}/submit")

    # Violation: non-legal roles try to approve (INCLUDING admin per BR-SEC-003)
    unauthorized_roles = [
        ("acme_admin", "admin"),  # BUG: admin should NOT be able to approve per BR-SEC-003
        ("acme_finance", "finance"),
        ("acme_requester", "requester"),
    ]

    for token_key, role_name in unauthorized_roles:
        r_unauth = _post(f"/contracts/{cid2}/legal-approve", token=TOKENS[token_key])
        receipt["treatment_results"].append({
            "mutation_type": f"authorization_unauthorized_{role_name}",
            "role": role_name,
            "status_code": r_unauth["status_code"],
            "body": r_unauth["body"],
            "expected": "rejected (403)",
        })

    # Evaluate: if any unauthorized role succeeds -> bug
    bugs = [t for t in receipt["treatment_results"] if t["status_code"] in (200, 201)]
    if bugs:
        receipt["verdict"] = "VIOLATION_NOT_REJECTED"
        receipt["bug_detected"] = True
        roles_ok = [t["role"] for t in bugs]
        receipt["details"] = {
            "bug_type": "AUTHORIZATION_NOT_ENFORCED",
            "description": f"Non-legal roles approved: {', '.join(roles_ok)}. Rule says only legal can approve.",
            "rule": "BR-SEC-003",
        }
    else:
        receipt["verdict"] = "PROPERTY_HELD"
        receipt["details"] = {"description": "All unauthorized roles correctly rejected"}


def _exec_tenant_isolation(receipt: dict, seed: dict):
    """BR-SEC-001: Cross-tenant access must be rejected."""
    # Get acme contracts
    r_acme = _get("/contracts", token=TOKENS["acme_admin"])
    acme_contracts = r_acme["body"]
    if isinstance(acme_contracts, dict):
        acme_contracts = acme_contracts.get("items") or acme_contracts.get("data") or []

    if not acme_contracts:
        receipt["verdict"] = "BLOCKED"
        receipt["details"]["reason"] = "no_acme_contracts"
        return

    acme_cid = acme_contracts[0]["id"]

    # Control: acme admin accesses acme contract → should succeed
    r_ctrl = _get(f"/contracts/{acme_cid}", token=TOKENS["acme_admin"])
    receipt["control_result"] = {"status_code": r_ctrl["status_code"], "body": r_ctrl["body"]}

    if r_ctrl["status_code"] != 200:
        receipt["verdict"] = "CONTROL_FAILED"
        receipt["details"]["reason"] = f"acme_access_failed_{r_ctrl['status_code']}"
        return

    # Violation: globex admin tries to access acme contract → should be rejected (404/403)
    r_cross = _get(f"/contracts/{acme_cid}", token=TOKENS["globex_admin"])
    receipt["treatment_results"].append({
        "mutation_type": "tenant_cross_access",
        "actor": "globex_admin",
        "status_code": r_cross["status_code"],
        "body": r_cross["body"],
        "expected": "rejected (403/404)",
    })

    # Also try globex requester
    r_cross2 = _get(f"/contracts/{acme_cid}", token=TOKENS["globex_requester"])
    receipt["treatment_results"].append({
        "mutation_type": "tenant_cross_access_requester",
        "actor": "globex_requester",
        "status_code": r_cross2["status_code"],
        "body": r_cross2["body"],
        "expected": "rejected (403/404)",
    })

    # Evaluate: if cross-tenant access returns 200 with data → bug
    cross_ok = [t for t in receipt["treatment_results"] if t["status_code"] == 200]
    if cross_ok:
        receipt["verdict"] = "VIOLATION_NOT_REJECTED"
        receipt["bug_detected"] = True
        receipt["details"] = {
            "bug_type": "TENANT_ISOLATION_NOT_ENFORCED",
            "description": f"Cross-tenant access succeeded for: {[t['actor'] for t in cross_ok]}",
            "rule": "BR-SEC-001",
        }
    else:
        receipt["verdict"] = "PROPERTY_HELD"
        receipt["details"] = {"description": "Cross-tenant access correctly rejected"}


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"{'='*70}")
    print(f"  Missing Mechanism Planning — Small Scale Execution")
    print(f"  Run ID: {RUN_ID}")
    print(f"  Max experiments: {MAX_EXPERIMENTS}")
    print(f"  Target: {BASE_URL}")
    print(f"{'='*70}")

    # 1. Health check
    print("\n[1/5] Health check...")
    try:
        r = _get("/contracts")
        if r["error"]:
            print(f"  FATAL: Server not reachable: {r['error']}")
            print("  Start mock server: python projects/contractflow_c/mock_server.py")
            sys.exit(1)
        print(f"  Server OK (status={r['status_code']})")
    except Exception as e:
        print(f"  FATAL: {e}")
        sys.exit(1)

    # 2. Build inputs
    print("\n[2/5] Building Behavior IR and Obligations...")
    behavior_ir = build_behavior_ir()
    obligations = build_obligations()
    print(f"  Actors: {len(behavior_ir['actors'])}")
    print(f"  Operations: {len(behavior_ir['operations'])}")
    print(f"  Invariants: {len(behavior_ir['invariants'])}")
    print(f"  Obligations: {len(obligations)}")

    # 3. Run Planner
    print("\n[3/5] Running Deep Experiment Planner...")
    plan_result = plan_deep_experiments(
        obligations=obligations,
        experiments_by_obligation={},  # No existing experiments
        behavior_ir=behavior_ir,
        budget=MAX_EXPERIMENTS,
    )
    experiments = plan_result["deep_experiments"]
    print(f"  Planned: {plan_result['planned_count']}")
    print(f"  Skipped: {plan_result['skipped_count']}")
    print(f"  Mechanism counts: {json.dumps(plan_result['mechanism_counts'], indent=4)}")

    if not experiments:
        print("\n  FATAL: No experiments planned. Check planner output.")
        sys.exit(1)

    # 4. Discover seed data
    print("\n[4/5] Discovering seed data...")
    seed = discover_seed_data()
    print(f"  Contracts: {len(seed['contracts'])}")
    print(f"  Invoices: {len(seed['invoices'])}")
    print(f"  Active contract: {'YES' if seed['contract_active'] else 'NO'}")
    print(f"  Approved contract: {'YES' if seed['contract_approved'] else 'NO'}")
    print(f"  Accepted milestone: {'YES' if seed['milestone_accepted'] else 'NO'}")

    # 5. Execute experiments
    print(f"\n[5/5] Executing {len(experiments)} experiments...")
    receipts = []
    bugs_found = []

    for i, exp in enumerate(experiments[:MAX_EXPERIMENTS]):
        mechanism = exp.get("mechanism", "?")
        rule_id = exp.get("rule_id", "?")
        print(f"\n  [{i+1}/{len(experiments)}] {rule_id} / {mechanism}")

        receipt = execute_experiment(exp, seed)
        receipts.append(receipt)

        verdict = receipt["verdict"]
        bug = receipt["bug_detected"]
        icon = "[BUG]" if bug else ("[OK]" if verdict == "PROPERTY_HELD" else "[--]")
        print(f"    {icon} Verdict: {verdict}")
        if bug:
            bugs_found.append(receipt)
            print(f"    [BUG] {receipt['details'].get('description', '')}")

    # ─── Summary ───
    print(f"\n{'='*70}")
    print(f"  EXECUTION SUMMARY")
    print(f"{'='*70}")
    print(f"  Total experiments: {len(receipts)}")
    print(f"  Bugs detected: {len(bugs_found)}")
    print(f"  Property held: {sum(1 for r in receipts if r['verdict'] == 'PROPERTY_HELD')}")
    print(f"  Blocked: {sum(1 for r in receipts if r['verdict'] == 'BLOCKED')}")
    print(f"  Control failed: {sum(1 for r in receipts if r['verdict'] == 'CONTROL_FAILED')}")
    print(f"  Indeterminate: {sum(1 for r in receipts if r['verdict'] == 'INDETERMINATE')}")

    if bugs_found:
        print(f"\n  BUGS FOUND:")
        for b in bugs_found:
            print(f"    [BUG] [{b['rule_id']}] {b['mechanism']}: {b['details'].get('description', '')}")

    # Save results
    output = {
        "run_id": RUN_ID,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "target": BASE_URL,
        "max_experiments": MAX_EXPERIMENTS,
        "planner_output": {
            "planned_count": plan_result["planned_count"],
            "mechanism_counts": plan_result["mechanism_counts"],
        },
        "execution_receipts": receipts,
        "bugs_detected": [
            {
                "rule_id": b["rule_id"],
                "mechanism": b["mechanism"],
                "bug_type": b["details"].get("bug_type"),
                "description": b["details"].get("description"),
            }
            for b in bugs_found
        ],
        "summary": {
            "total": len(receipts),
            "bugs_detected": len(bugs_found),
            "property_held": sum(1 for r in receipts if r["verdict"] == "PROPERTY_HELD"),
            "blocked": sum(1 for r in receipts if r["verdict"] == "BLOCKED"),
            "control_failed": sum(1 for r in receipts if r["verdict"] == "CONTROL_FAILED"),
        },
    }

    out_path = Path("_missing_mechanism_small_scale_result.json")
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Results saved: {out_path}")

    # SPEC §31 gate: ≥4 plans, ≥3 deep executions, ≥2 new deep TP
    print(f"\n  SPEC §31 GATE CHECK:")
    print(f"    Plans generated: {plan_result['planned_count']} (need ≥4): {'PASS' if plan_result['planned_count'] >= 4 else 'FAIL'}")
    executed_count = sum(1 for r in receipts if r["verdict"] not in ("BLOCKED", "MECHANISM_NOT_EXECUTABLE"))
    print(f"    Deep executions: {executed_count} (need ≥3): {'PASS' if executed_count >= 3 else 'FAIL'}")
    print(f"    New deep TP: {len(bugs_found)} (need ≥2): {'PASS' if len(bugs_found) >= 2 else 'FAIL'}")

    return 0 if len(bugs_found) >= 2 else 1


if __name__ == "__main__":
    sys.exit(main())

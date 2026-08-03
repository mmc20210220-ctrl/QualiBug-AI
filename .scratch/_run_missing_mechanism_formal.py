"""Missing Mechanism Planning — Formal Run (≤100 experiments).

Extends the Small Scale to ALL business rules from BUSINESS_RULES.md.
Run ID: PROJECT_C_MISSING_MECHANISM_FORMAL_V1
"""
from __future__ import annotations

import json
import sys
import uuid
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
from ai_test_asset_center.deep_experiment_planner import plan_deep_experiments

BASE_URL = "http://127.0.0.1:8000/api/v1"
MAX_EXPERIMENTS = 100
RUN_ID = "PROJECT_C_MISSING_MECHANISM_FORMAL_V1"

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
             body: dict | None = None, headers: dict | None = None) -> dict:
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
            return {"status_code": resp.status,
                    "body": json.loads(resp_body) if resp_body else {}, "error": None}
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode()
        try:
            parsed = json.loads(resp_body)
        except json.JSONDecodeError:
            parsed = {"raw": resp_body}
        return {"status_code": e.code, "body": parsed, "error": None}
    except Exception as e:
        return {"status_code": 0, "body": {}, "error": str(e)}

def _get(path, **kw): return _request("GET", path, **kw)
def _post(path, body=None, **kw): return _request("POST", path, body=body, **kw)
def _patch(path, body=None, **kw): return _request("PATCH", path, body=body, **kw)


# ─── Full Behavior IR ──────────────────────────────────────────────────────────

def build_full_behavior_ir() -> dict:
    actors = [
        {"id": "acme_admin", "role": "admin", "tenant": "acme", "credential_secret_ref": "acme-admin-token"},
        {"id": "acme_legal", "role": "legal", "tenant": "acme", "credential_secret_ref": "acme-legal-token"},
        {"id": "acme_finance", "role": "finance", "tenant": "acme", "credential_secret_ref": "acme-finance-token"},
        {"id": "acme_requester", "role": "requester", "tenant": "acme", "credential_secret_ref": "acme-requester-token"},
        {"id": "acme_manager", "role": "project_manager", "tenant": "acme", "credential_secret_ref": "acme-manager-token"},
        {"id": "acme_auditor", "role": "auditor", "tenant": "acme", "credential_secret_ref": "acme-auditor-token"},
        {"id": "acme_vendor", "role": "vendor", "tenant": "acme", "credential_secret_ref": "acme-vendor-token"},
        {"id": "globex_admin", "role": "admin", "tenant": "globex", "credential_secret_ref": "globex-admin-token"},
        {"id": "globex_requester", "role": "requester", "tenant": "globex", "credential_secret_ref": "globex-requester-token"},
        {"id": "globex_finance", "role": "finance", "tenant": "globex", "credential_secret_ref": "globex-finance-token"},
    ]
    operations = [
        {"id": "op_create_contract", "method": "POST", "path": "/contracts", "service": "contractflow",
         "request_example": {"contract_no": "", "title": "", "department_id": "", "vendor_id": "",
                             "budget_id": "", "total_amount": 10000, "start_date": "2026-01-01", "end_date": "2026-12-31"}},
        {"id": "op_update_contract", "method": "PATCH", "path": "/contracts/{contract_id}", "service": "contractflow",
         "request_example": {"title": "updated"}},
        {"id": "op_submit_contract", "method": "POST", "path": "/contracts/{contract_id}/submit", "service": "contractflow",
         "request_example": {}},
        {"id": "op_legal_approve", "method": "POST", "path": "/contracts/{contract_id}/legal-approve", "service": "contractflow",
         "request_example": {}},
        {"id": "op_activate_contract", "method": "POST", "path": "/contracts/{contract_id}/activate", "service": "contractflow",
         "request_example": {}},
        {"id": "op_cancel_contract", "method": "POST", "path": "/contracts/{contract_id}/cancel", "service": "contractflow",
         "request_example": {}},
        {"id": "op_complete_contract", "method": "POST", "path": "/contracts/{contract_id}/complete", "service": "contractflow",
         "request_example": {}},
        {"id": "op_create_milestone", "method": "POST", "path": "/contracts/{contract_id}/milestones", "service": "contractflow",
         "request_example": {"name": "", "amount": 1000, "due_date": "2026-06-30"}},
        {"id": "op_accept_milestone", "method": "POST", "path": "/milestones/{milestone_id}/accept", "service": "contractflow",
         "request_example": {"accepted_amount": 1000}},
        {"id": "op_create_invoice", "method": "POST", "path": "/invoices", "service": "contractflow",
         "request_example": {"contract_id": "", "invoice_no": "", "subtotal": 1000, "tax_amount": 100, "issue_date": "2026-06-01"}},
        {"id": "op_create_payment", "method": "POST", "path": "/payment-requests", "service": "contractflow",
         "request_example": {"contract_id": "", "milestone_id": "", "invoice_id": "", "amount": 5000}},
        {"id": "op_finance_approve", "method": "POST", "path": "/payment-requests/{id}/finance-approve", "service": "contractflow",
         "request_example": {}},
        {"id": "op_execute_payment", "method": "POST", "path": "/payment-requests/{id}/pay", "service": "contractflow",
         "request_example": {}},
        {"id": "op_get_contract", "method": "GET", "path": "/contracts/{contract_id}", "service": "contractflow",
         "request_example": {}},
    ]
    # All 36 invariants from BUSINESS_RULES.md
    invariants = [
        {"id": "BR-CON-001", "rule_type": "FIELD_INVARIANT", "entity_ref": "contract",
         "description": "合同总金额必须大于0", "expression": {"field": "total_amount", "constraint": "positive"}},
        {"id": "BR-CON-002", "rule_type": "TEMPORAL", "entity_ref": "contract",
         "description": "合同开始日期早于结束日期", "expression": {"start_date": "start_date", "end_date": "end_date"}},
        {"id": "BR-CON-003", "rule_type": "UNIQUENESS", "entity_ref": "contract",
         "description": "同一租户合同编号唯一", "expression": {"unique_fields": ["contract_no"], "scope": "tenant"}},
        {"id": "BR-CON-004", "rule_type": "PRECONDITION", "entity_ref": "contract",
         "description": "合同提交前至少有一个里程碑", "expression": {"precondition": "at_least_one_milestone"}},
        {"id": "BR-CON-005", "rule_type": "CUMULATIVE", "entity_ref": "milestone",
         "description": "里程碑金额之和等于合同金额", "expression": {"field": "amount", "limit_field": "total_amount"}},
        {"id": "BR-CON-006", "rule_type": "STATE_TRANSITION", "entity_ref": "contract",
         "description": "只有LEGAL_REVIEW可法务批准为APPROVED", "expression": {"target_state": "APPROVED", "from_state": "LEGAL_REVIEW"}},
        {"id": "BR-CON-007", "rule_type": "STATE_TRANSITION", "entity_ref": "contract",
         "description": "只有APPROVED可激活为ACTIVE", "expression": {"target_state": "ACTIVE", "from_state": "APPROVED"}},
        {"id": "BR-CON-008", "rule_type": "CAUSAL_POSTCONDITION", "entity_ref": "budget",
         "description": "激活合同后预算available减少合同额reserved增加合同额", "expression": {"effect": "budget_reservation"}},
        {"id": "BR-CON-009", "rule_type": "CUMULATIVE", "entity_ref": "budget",
         "description": "预算total=available+reserved+spent", "expression": {"field": "total_amount", "limit_field": "total_amount"}},
        {"id": "BR-CON-010", "rule_type": "CAUSAL_POSTCONDITION", "entity_ref": "budget",
         "description": "取消合同释放未支付预算预留", "expression": {"effect": "budget_release"}},
        {"id": "BR-CON-011", "rule_type": "STATE_TRANSITION", "entity_ref": "contract",
         "description": "CANCELLED合同不能重新激活", "expression": {"target_state": "ACTIVE", "forbidden_from": "CANCELLED"}},
        {"id": "BR-MIL-001", "rule_type": "TEMPORAL", "entity_ref": "milestone",
         "description": "里程碑到期日在合同周期内", "expression": {"date_field": "due_date", "bounds": {"start": "contract.start_date", "end": "contract.end_date"}}},
        {"id": "BR-MIL-002", "rule_type": "STATE_TRANSITION", "entity_ref": "milestone",
         "description": "只有PENDING或REJECTED可提交为SUBMITTED", "expression": {"target_state": "SUBMITTED"}},
        {"id": "BR-MIL-003", "rule_type": "STATE_TRANSITION", "entity_ref": "milestone",
         "description": "只有SUBMITTED可验收为ACCEPTED", "expression": {"target_state": "ACCEPTED"}},
        {"id": "BR-MIL-004", "rule_type": "IDEMPOTENCY", "entity_ref": "milestone",
         "description": "ACCEPTED里程碑重复验收不得生成第二条验收记录", "expression": {"action": "accept"}},
        {"id": "BR-MIL-005", "rule_type": "BOUNDARY", "entity_ref": "milestone",
         "description": "验收金额不得超过里程碑金额", "expression": {"field": "accepted_amount", "limit": "milestone.amount"}},
        {"id": "BR-INV-001", "rule_type": "UNIQUENESS", "entity_ref": "invoice",
         "description": "同一供应商发票号唯一", "expression": {"unique_fields": ["vendor_id", "invoice_no"], "scope": "vendor"}},
        {"id": "BR-INV-002", "rule_type": "FIELD_INVARIANT", "entity_ref": "invoice",
         "description": "发票金额和税额不得为负", "expression": {"field": "subtotal", "constraint": "non_negative", "fields": ["subtotal", "tax_amount"]}},
        {"id": "BR-INV-003", "rule_type": "CUMULATIVE", "entity_ref": "invoice",
         "description": "发票含税金额=未税金额+税额", "expression": {"field": "total_amount", "components": ["subtotal", "tax_amount"]}},
        {"id": "BR-INV-004", "rule_type": "TEMPORAL", "entity_ref": "invoice",
         "description": "发票日期不得晚于付款申请日期", "expression": {"date_field": "issue_date"}},
        {"id": "BR-PAY-001", "rule_type": "PRECONDITION", "entity_ref": "payment_request",
         "description": "付款必须关联ACTIVE合同", "expression": {"required_state": "ACTIVE", "entity": "contract", "wrong_state": "APPROVED"}},
        {"id": "BR-PAY-002", "rule_type": "PRECONDITION", "entity_ref": "payment_request",
         "description": "付款必须关联ACCEPTED里程碑", "expression": {"required_state": "ACCEPTED", "entity": "milestone"}},
        {"id": "BR-PAY-003", "rule_type": "BOUNDARY", "entity_ref": "payment_request",
         "description": "付款金额不超过里程碑剩余可付金额", "expression": {"field": "amount", "limit": "milestone.remaining"}},
        {"id": "BR-PAY-004", "rule_type": "BOUNDARY", "entity_ref": "payment_request",
         "description": "合同累计付款不超过合同总金额", "expression": {"field": "amount", "limit": "contract.total_amount"}},
        {"id": "BR-PAY-005", "rule_type": "BOUNDARY", "entity_ref": "payment_request",
         "description": "发票累计付款不超过发票含税金额", "expression": {"field": "amount", "limit": "invoice.total_amount"}},
        {"id": "BR-PAY-006", "rule_type": "STATE_TRANSITION", "entity_ref": "payment_request",
         "description": "只有MANAGER_APPROVED可财务批准", "expression": {"target_state": "FINANCE_APPROVED"}},
        {"id": "BR-PAY-007", "rule_type": "STATE_TRANSITION", "entity_ref": "payment_request",
         "description": "只有FINANCE_APPROVED可执行付款", "expression": {"target_state": "PAID"}},
        {"id": "BR-PAY-008", "rule_type": "IDEMPOTENCY", "entity_ref": "payment_request",
         "description": "同一幂等键重复付款只产生一次资金变化", "expression": {"action": "pay", "key": "idempotency_key"}},
        {"id": "BR-PAY-009", "rule_type": "CAUSAL_POSTCONDITION", "entity_ref": "budget",
         "description": "付款后reserved减少spent增加contract.paid增加三者变化量相等", "expression": {"effect": "budget_spend"}},
        {"id": "BR-PAY-010", "rule_type": "PRECONDITION", "entity_ref": "payment_request",
         "description": "PAID付款必须存在对应合同里程碑发票且租户一致", "expression": {"precondition": "cross_entity_consistency"}},
        {"id": "BR-COM-001", "rule_type": "PRECONDITION", "entity_ref": "contract",
         "description": "所有里程碑验收并足额付款后才能完成合同", "expression": {"precondition": "all_milestones_accepted_and_paid"}},
        {"id": "BR-SEC-001", "rule_type": "TENANT_ISOLATION", "entity_ref": "contract",
         "description": "所有业务实体禁止跨租户访问", "expression": {"scope": "all_entities", "isolation": "tenant"}},
        {"id": "BR-SEC-002", "rule_type": "DATA_VISIBILITY", "entity_ref": "contract",
         "description": "vendor不得看到internal_notes和预算信息", "expression": {"hidden_fields": ["internal_notes", "budget_id"]}},
        {"id": "BR-SEC-003", "rule_type": "AUTHORIZATION", "entity_ref": "contract",
         "description": "只有legal可完成法务批准", "expression": {"authorized_role": "legal", "action": "legal-approve"}},
        {"id": "BR-SEC-004", "rule_type": "AUTHORIZATION", "entity_ref": "payment_request",
         "description": "只有finance可执行付款", "expression": {"authorized_role": "finance", "action": "pay"}},
        {"id": "BR-CC-001", "rule_type": "STATE_TRANSITION", "entity_ref": "contract",
         "description": "合同更新version不一致时返回409", "expression": {"target_state": "updated", "optimistic_lock": True}},
    ]
    return {"actors": actors, "operations": operations, "invariants": invariants, "states": [], "relations": []}


def build_full_obligations(ir: dict) -> list[dict]:
    """Build obligations for ALL invariants."""
    obligations = []
    # Map invariants to their primary operation
    inv_to_op = {
        "BR-CON-001": "op_create_contract", "BR-CON-002": "op_create_contract",
        "BR-CON-003": "op_create_contract", "BR-CON-004": "op_submit_contract",
        "BR-CON-005": "op_submit_contract", "BR-CON-006": "op_legal_approve",
        "BR-CON-007": "op_activate_contract", "BR-CON-008": "op_activate_contract",
        "BR-CON-009": "op_activate_contract", "BR-CON-010": "op_cancel_contract",
        "BR-CON-011": "op_activate_contract", "BR-MIL-001": "op_create_milestone",
        "BR-MIL-002": "op_accept_milestone", "BR-MIL-003": "op_accept_milestone",
        "BR-MIL-004": "op_accept_milestone", "BR-MIL-005": "op_accept_milestone",
        "BR-INV-001": "op_create_invoice", "BR-INV-002": "op_create_invoice",
        "BR-INV-003": "op_create_invoice", "BR-INV-004": "op_create_invoice",
        "BR-PAY-001": "op_create_payment", "BR-PAY-002": "op_create_payment",
        "BR-PAY-003": "op_create_payment", "BR-PAY-004": "op_create_payment",
        "BR-PAY-005": "op_create_payment", "BR-PAY-006": "op_finance_approve",
        "BR-PAY-007": "op_execute_payment", "BR-PAY-008": "op_execute_payment",
        "BR-PAY-009": "op_execute_payment", "BR-PAY-010": "op_execute_payment",
        "BR-COM-001": "op_complete_contract", "BR-SEC-001": "op_get_contract",
        "BR-SEC-002": "op_get_contract", "BR-SEC-003": "op_legal_approve",
        "BR-SEC-004": "op_execute_payment", "BR-CC-001": "op_update_contract",
    }
    for inv in ir["invariants"]:
        inv_id = inv["id"]
        op_ref = inv_to_op.get(inv_id, "op_get_contract")
        obligations.append({
            "obligation_id": f"obl_{inv_id}",
            "risk_family": inv["rule_type"].lower(),
            "property": {
                "invariant_ref": inv_id,
                "operation_ref": op_ref,
                "actor_ref": "acme_admin",
                "expression": inv.get("expression", {}),
            },
            "source_refs": [{"rule_id": inv_id, "source": "BUSINESS_RULES.md"}],
        })
    return obligations


# ─── Executor (reuses Small Scale patterns) ────────────────────────────────────

def execute_formal_experiment(exp: dict) -> dict:
    """Execute experiment with mechanism-specific logic."""
    mechanism = exp.get("mechanism", "")
    rule_id = exp.get("rule_id", "")
    receipt = {
        "experiment_id": exp.get("experiment_id"),
        "obligation_id": exp.get("obligation_id"),
        "rule_id": rule_id,
        "mechanism": mechanism,
        "verdict": "INDETERMINATE",
        "bug_detected": False,
        "details": {},
    }

    if mechanism == "AUTHORIZATION_MATRIX":
        _exec_authz_formal(receipt, exp)
    elif mechanism == "PRECONDITION_VIOLATION":
        _exec_precond_formal(receipt, exp)
    elif mechanism == "UNIQUENESS_VIOLATION":
        _exec_uniqueness_formal(receipt, exp)
    elif mechanism == "FIELD_INVARIANT_VIOLATION":
        _exec_field_inv_formal(receipt, exp)
    elif mechanism == "TENANT_ISOLATION_MATRIX":
        _exec_tenant_formal(receipt, exp)
    else:
        receipt["verdict"] = "MECHANISM_NOT_EXECUTABLE"
        receipt["details"] = {"reason": f"No formal executor for {mechanism}"}
    return receipt


def _get_ref_data():
    """Get budget, dept, vendor for contract creation."""
    r = _get("/budgets")
    budgets = r["body"] if isinstance(r["body"], list) else []
    r2 = _get("/reference/departments")
    depts = r2["body"] if isinstance(r2["body"], list) else []
    r3 = _get("/reference/vendors")
    vendors = r3["body"] if isinstance(r3["body"], list) else []
    return (budgets[0]["id"] if budgets else "",
            depts[0]["id"] if depts else "",
            vendors[0]["id"] if vendors else "")


def _exec_authz_formal(receipt: dict, exp: dict):
    """Test authorization: authorized role vs unauthorized roles."""
    budget_id, dept_id, vendor_id = _get_ref_data()
    if not budget_id:
        receipt["verdict"] = "BLOCKED"
        receipt["details"] = {"reason": "no_budget"}
        return

    suffix = uuid.uuid4().hex[:6]
    r = _post("/contracts", {
        "contract_no": f"FAUTHZ-{suffix}", "title": f"Formal Authz {suffix}",
        "department_id": dept_id, "vendor_id": vendor_id, "budget_id": budget_id,
        "total_amount": 10000.0, "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    if r["status_code"] not in (200, 201):
        receipt["verdict"] = "BLOCKED"
        receipt["details"] = {"reason": f"create_failed_{r['status_code']}"}
        return
    cid = r["body"]["id"]
    _post(f"/contracts/{cid}/milestones", {"name": "M1", "amount": 10000.0, "due_date": "2026-06-30"})
    _post(f"/contracts/{cid}/submit")

    # Test: admin tries to legal-approve (should be rejected per BR-SEC-003)
    r_admin = _post(f"/contracts/{cid}/legal-approve", token=TOKENS["acme_admin"])
    # Test: legal approves (control)
    r_legal = _post(f"/contracts/{cid}/legal-approve", token=TOKENS["acme_legal"])

    if r_legal["status_code"] in (200, 201) and r_admin["status_code"] in (200, 201):
        receipt["verdict"] = "VIOLATION_NOT_REJECTED"
        receipt["bug_detected"] = True
        receipt["details"] = {
            "bug_type": "AUTHORIZATION_BYPASS",
            "description": "admin can legal-approve (should be legal-only)",
            "rule": receipt["rule_id"],
        }
    elif r_legal["status_code"] in (200, 201) and r_admin["status_code"] == 403:
        receipt["verdict"] = "PROPERTY_HELD"
        receipt["details"] = {"description": "admin correctly rejected"}
    else:
        receipt["verdict"] = "INDETERMINATE"
        receipt["details"] = {"legal_status": r_legal["status_code"], "admin_status": r_admin["status_code"]}


def _exec_precond_formal(receipt: dict, exp: dict):
    """Test precondition: cancelled contract payment execution."""
    budget_id, dept_id, vendor_id = _get_ref_data()
    if not budget_id:
        receipt["verdict"] = "BLOCKED"
        receipt["details"] = {"reason": "no_budget"}
        return

    suffix = uuid.uuid4().hex[:6]
    amount = 5000.0
    # Create + activate contract
    r = _post("/contracts", {
        "contract_no": f"FPRE-{suffix}", "title": f"Formal Precond {suffix}",
        "department_id": dept_id, "vendor_id": vendor_id, "budget_id": budget_id,
        "total_amount": amount, "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    if r["status_code"] not in (200, 201):
        receipt["verdict"] = "BLOCKED"
        receipt["details"] = {"reason": f"create_failed_{r['status_code']}"}
        return
    cid = r["body"]["id"]
    _post(f"/contracts/{cid}/milestones", {"name": "M1", "amount": amount, "due_date": "2026-06-30"})
    _post(f"/contracts/{cid}/submit")
    _post(f"/contracts/{cid}/legal-approve", token=TOKENS["acme_legal"])
    r_act = _post(f"/contracts/{cid}/activate")
    if r_act["status_code"] not in (200, 201):
        receipt["verdict"] = "BLOCKED"
        receipt["details"] = {"reason": f"activate_failed_{r_act['status_code']}: {r_act['body']}"}
        return

    # Accept milestone
    r_ms = _get(f"/contracts/{cid}/milestones")
    ms_list = r_ms["body"] if isinstance(r_ms["body"], list) else []
    if not ms_list:
        receipt["verdict"] = "BLOCKED"
        receipt["details"] = {"reason": "no_milestones"}
        return
    mid = ms_list[0]["id"]
    _post(f"/milestones/{mid}/submit", {"evidence_url": "http://test/ev.pdf"})
    _post(f"/milestones/{mid}/accept", {"accepted_amount": amount})

    # Create invoice + payment
    r_inv = _post("/invoices", {"contract_id": cid, "invoice_no": f"FINV-{suffix}",
                                "subtotal": amount, "tax_amount": 0, "issue_date": "2026-06-01", "vendor_id": vendor_id})
    if r_inv["status_code"] not in (200, 201):
        receipt["verdict"] = "BLOCKED"
        receipt["details"] = {"reason": "invoice_failed"}
        return
    inv_id = r_inv["body"]["id"]
    pay_amt = amount * 0.5
    r_pay = _post("/payment-requests", {"contract_id": cid, "milestone_id": mid, "invoice_id": inv_id, "amount": pay_amt})
    if r_pay["status_code"] not in (200, 201):
        receipt["verdict"] = "BLOCKED"
        receipt["details"] = {"reason": f"payment_failed: {r_pay['body']}"}
        return
    pay_id = r_pay["body"]["id"]
    _post(f"/payment-requests/{pay_id}/manager-approve")
    _post(f"/payment-requests/{pay_id}/finance-approve", token=TOKENS["acme_finance"])

    # Cancel contract then execute payment
    _post(f"/contracts/{cid}/cancel")
    idem_key = f"formal-{uuid.uuid4().hex[:16]}"
    r_exec = _request("POST", f"/payment-requests/{pay_id}/pay",
                      token=TOKENS["acme_finance"], body={},
                      headers={"Idempotency-Key": idem_key})

    if r_exec["status_code"] in (200, 201):
        receipt["verdict"] = "VIOLATION_NOT_REJECTED"
        receipt["bug_detected"] = True
        receipt["details"] = {
            "bug_type": "PRECONDITION_NOT_ENFORCED_ON_EXECUTION",
            "description": "Payment executed on CANCELLED contract",
            "rule": receipt["rule_id"],
        }
    elif r_exec["status_code"] == 409:
        receipt["verdict"] = "PROPERTY_HELD"
        receipt["details"] = {"description": "Payment on cancelled contract rejected"}
    else:
        receipt["verdict"] = "INDETERMINATE"
        receipt["details"] = {"status": r_exec["status_code"], "body": r_exec["body"]}


def _exec_uniqueness_formal(receipt: dict, exp: dict):
    """Test uniqueness constraint."""
    r_contracts = _get("/contracts")
    contracts = r_contracts["body"] if isinstance(r_contracts["body"], list) else []
    if not contracts:
        receipt["verdict"] = "BLOCKED"
        receipt["details"] = {"reason": "no_contracts"}
        return
    cid = contracts[0]["id"]
    vid = contracts[0].get("vendor_id", "")
    inv_no = f"FUNIQ-{uuid.uuid4().hex[:8]}"
    r1 = _post("/invoices", {"contract_id": cid, "invoice_no": inv_no, "subtotal": 100, "tax_amount": 10, "issue_date": "2026-06-01", "vendor_id": vid})
    if r1["status_code"] not in (200, 201):
        receipt["verdict"] = "CONTROL_FAILED"
        receipt["details"] = {"reason": f"first_create_{r1['status_code']}"}
        return
    r2 = _post("/invoices", {"contract_id": cid, "invoice_no": inv_no, "subtotal": 200, "tax_amount": 20, "issue_date": "2026-06-02", "vendor_id": vid})
    if r2["status_code"] in (200, 201):
        receipt["verdict"] = "VIOLATION_NOT_REJECTED"
        receipt["bug_detected"] = True
        receipt["details"] = {"bug_type": "UNIQUENESS_NOT_ENFORCED", "description": f"Duplicate invoice_no={inv_no} accepted"}
    else:
        receipt["verdict"] = "PROPERTY_HELD"
        receipt["details"] = {"description": "Duplicate correctly rejected"}


def _exec_field_inv_formal(receipt: dict, exp: dict):
    """Test field invariant (non-negative)."""
    r_contracts = _get("/contracts")
    contracts = r_contracts["body"] if isinstance(r_contracts["body"], list) else []
    if not contracts:
        receipt["verdict"] = "BLOCKED"
        receipt["details"] = {"reason": "no_contracts"}
        return
    cid = contracts[0]["id"]
    vid = contracts[0].get("vendor_id", "")
    r = _post("/invoices", {"contract_id": cid, "invoice_no": f"FNEG-{uuid.uuid4().hex[:8]}",
                            "subtotal": -100, "tax_amount": 50, "issue_date": "2026-06-01", "vendor_id": vid})
    if r["status_code"] in (200, 201):
        receipt["verdict"] = "VIOLATION_NOT_REJECTED"
        receipt["bug_detected"] = True
        receipt["details"] = {"bug_type": "NEGATIVE_AMOUNT_ACCEPTED", "description": "subtotal=-100 accepted"}
    else:
        receipt["verdict"] = "PROPERTY_HELD"
        receipt["details"] = {"description": "Negative amount rejected"}


def _exec_tenant_formal(receipt: dict, exp: dict):
    """Test tenant isolation."""
    r = _get("/contracts", token=TOKENS["acme_admin"])
    contracts = r["body"] if isinstance(r["body"], list) else []
    if not contracts:
        receipt["verdict"] = "BLOCKED"
        receipt["details"] = {"reason": "no_contracts"}
        return
    cid = contracts[0]["id"]
    r_cross = _get(f"/contracts/{cid}", token=TOKENS["globex_admin"])
    if r_cross["status_code"] == 200:
        receipt["verdict"] = "VIOLATION_NOT_REJECTED"
        receipt["bug_detected"] = True
        receipt["details"] = {"bug_type": "TENANT_ISOLATION_BREACH", "description": "Cross-tenant read succeeded"}
    else:
        receipt["verdict"] = "PROPERTY_HELD"
        receipt["details"] = {"description": "Cross-tenant access rejected"}


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"{'='*70}")
    print(f"  Missing Mechanism Planning - FORMAL RUN")
    print(f"  Run ID: {RUN_ID}")
    print(f"  Max experiments: {MAX_EXPERIMENTS}")
    print(f"{'='*70}")

    # Health check
    r = _get("/contracts")
    if r["error"] or r["status_code"] != 200:
        print("  FATAL: Server not reachable")
        return 1

    # Build full IR and obligations
    ir = build_full_behavior_ir()
    obligations = build_full_obligations(ir)
    print(f"\n  Invariants: {len(ir['invariants'])}")
    print(f"  Obligations: {len(obligations)}")

    # Plan
    plan_result = plan_deep_experiments(
        obligations=obligations,
        experiments_by_obligation={},
        behavior_ir=ir,
        budget=MAX_EXPERIMENTS,
    )
    experiments = plan_result["deep_experiments"]
    print(f"  Planned: {plan_result['planned_count']}")
    print(f"  Mechanisms: {json.dumps(plan_result['mechanism_counts'], indent=2)}")

    # Execute
    print(f"\n  Executing {len(experiments)} experiments...")
    receipts = []
    bugs = []
    for i, exp in enumerate(experiments):
        receipt = execute_formal_experiment(exp)
        receipts.append(receipt)
        if receipt["bug_detected"]:
            bugs.append(receipt)
        status = "[BUG]" if receipt["bug_detected"] else f"[{receipt['verdict'][:4]}]"
        print(f"    [{i+1}/{len(experiments)}] {receipt['rule_id']:12s} {receipt['mechanism']:30s} {status}")

    # Summary
    print(f"\n{'='*70}")
    print(f"  FORMAL RUN SUMMARY")
    print(f"{'='*70}")
    print(f"  Total: {len(receipts)}")
    print(f"  Bugs: {len(bugs)}")
    print(f"  PROPERTY_HELD: {sum(1 for r in receipts if r['verdict']=='PROPERTY_HELD')}")
    print(f"  BLOCKED: {sum(1 for r in receipts if r['verdict']=='BLOCKED')}")
    print(f"  NOT_EXECUTABLE: {sum(1 for r in receipts if r['verdict']=='MECHANISM_NOT_EXECUTABLE')}")

    if bugs:
        print(f"\n  BUGS DETECTED:")
        for b in bugs:
            print(f"    [{b['rule_id']}] {b['details'].get('description','')}")

    # Save
    output = {
        "run_id": RUN_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "planner": {"planned": plan_result["planned_count"], "mechanisms": plan_result["mechanism_counts"]},
        "receipts": receipts,
        "bugs": [{"rule_id": b["rule_id"], "mechanism": b["mechanism"], **b["details"]} for b in bugs],
        "summary": {"total": len(receipts), "bugs": len(bugs),
                    "held": sum(1 for r in receipts if r["verdict"]=="PROPERTY_HELD"),
                    "blocked": sum(1 for r in receipts if r["verdict"]=="BLOCKED")},
    }
    Path("_missing_mechanism_formal_result.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Saved: _missing_mechanism_formal_result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

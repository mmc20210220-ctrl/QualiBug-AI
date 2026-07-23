"""ContractFlow Project C Mock Server - Test Target Infrastructure.

Implements the ContractFlow API per openapi.yaml with in-memory storage.
This is the SYSTEM UNDER TEST, not QualiBug product code.
Port: 8000 (as declared in test_accounts.json)
"""
from __future__ import annotations

import uuid
import time
from datetime import date, datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import re
from urllib.parse import urlparse, parse_qs

# ── In-Memory Database ──
DB: dict = {
    "users": {},
    "contracts": {},
    "milestones": {},
    "invoices": {},
    "payment_requests": {},
    "budgets": {},
    "audit_logs": [],
    "departments": {},
    "vendors": {},
}

TENANTS = {
    "acme": {"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "acme.test")), "name": "Acme Corp"},
    "globex": {"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "globex.test")), "name": "Globex Inc"},
}

ACCOUNTS = [
    {"role": "admin", "tenant": "acme", "token": "acme-admin-token", "email": "admin@acme.test", "name": "Acme Admin"},
    {"role": "legal", "tenant": "acme", "token": "acme-legal-token", "email": "legal@acme.test", "name": "Acme Legal"},
    {"role": "finance", "tenant": "acme", "token": "acme-finance-token", "email": "finance@acme.test", "name": "Acme Finance"},
    {"role": "requester", "tenant": "acme", "token": "acme-requester-token", "email": "requester@acme.test", "name": "Acme Requester"},
    {"role": "project_manager", "tenant": "acme", "token": "acme-manager-token", "email": "manager@acme.test", "name": "Acme Manager"},
    {"role": "auditor", "tenant": "acme", "token": "acme-auditor-token", "email": "auditor@acme.test", "name": "Acme Auditor"},
    {"role": "vendor", "tenant": "acme", "token": "acme-vendor-token", "email": "vendor@vendor.test", "name": "Vendor User"},
    {"role": "admin", "tenant": "globex", "token": "globex-admin-token", "email": "admin@globex.test", "name": "Globex Admin"},
    {"role": "requester", "tenant": "globex", "token": "globex-requester-token", "email": "requester@globex.test", "name": "Globex Requester"},
    {"role": "finance", "tenant": "globex", "token": "globex-finance-token", "email": "finance@globex.test", "name": "Globex Finance"},
]

TOKEN_MAP = {}
for acc in ACCOUNTS:
    uid = str(uuid.uuid5(uuid.NAMESPACE_DNS, acc["email"]))
    tid = TENANTS[acc["tenant"]]["id"]
    user = {"id": uid, "tenant_id": tid, "email": acc["email"], "full_name": acc["name"],
            "role": acc["role"], "department_id": None, "tenant_name": acc["tenant"]}
    DB["users"][uid] = user
    TOKEN_MAP[acc["token"]] = user

# Seed reference data per tenant
for tname, tinfo in TENANTS.items():
    tid = tinfo["id"]
    dept_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"dept-{tname}"))
    vendor_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"vendor-{tname}"))
    budget_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"budget-{tname}"))
    DB["departments"][dept_id] = {"id": dept_id, "tenant_id": tid, "name": f"{tname} Engineering"}
    DB["vendors"][vendor_id] = {"id": vendor_id, "tenant_id": tid, "name": f"{tname} Vendor Co"}
    DB["budgets"][budget_id] = {
        "id": budget_id, "tenant_id": tid, "department_id": dept_id,
        "fiscal_year": 2026, "total_amount": 1000000.0,
        "available_amount": 1000000.0, "reserved_amount": 0.0,
        "spent_amount": 0.0, "version": 1,
    }

# Idempotency store
IDEMPOTENCY_STORE: dict[str, dict] = {}


def _now_iso():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(tenant_id, actor_id, entity_type, entity_id, action, before=None, after=None):
    DB["audit_logs"].append({
        "id": str(uuid.uuid4()), "tenant_id": tenant_id, "actor_id": actor_id,
        "entity_type": entity_type, "entity_id": entity_id, "action": action,
        "before_data": before, "after_data": after, "correlation_id": None,
        "created_at": _now_iso(),
    })


class ContractFlowHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default logging

    def _send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _auth(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
            user = TOKEN_MAP.get(token)
            if user:
                return user
        return None

    def _path_parts(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        # Strip /api/v1 prefix
        if path.startswith("/api/v1"):
            path = path[7:] or "/"
        return path, parse_qs(parsed.query)

    # ── HTTP Methods ──
    def do_GET(self):
        user = self._auth()
        path, query = self._path_parts()
        if not user:
            self._send_json(401, {"error": "Unauthorized"})
            return
        self._route_get(user, path, query)

    def do_POST(self):
        path, query = self._path_parts()
        body = self._read_body()
        # Login doesn't require auth
        if path == "/auth/login":
            self._handle_login(body)
            return
        user = self._auth()
        if not user:
            self._send_json(401, {"error": "Unauthorized"})
            return
        self._route_post(user, path, body)

    def do_PATCH(self):
        user = self._auth()
        path, _ = self._path_parts()
        body = self._read_body()
        if not user:
            self._send_json(401, {"error": "Unauthorized"})
            return
        self._route_patch(user, path, body)

    # ── Route Dispatch ──
    def _route_get(self, user, path, query):
        tid = user["tenant_id"]
        if path == "/auth/me":
            self._send_json(200, user)
        elif path == "/contracts":
            items = [c for c in DB["contracts"].values() if c["tenant_id"] == tid]
            self._send_json(200, items)
        elif re.match(r"^/contracts/[^/]+$", path):
            cid = path.split("/")[2]
            contract = DB["contracts"].get(cid)
            if not contract or contract["tenant_id"] != tid:
                self._send_json(404, {"error": "Not found"})
                return
            if user["role"] == "vendor":
                c = dict(contract)
                c.pop("internal_notes", None)
                c.pop("budget_id", None)
                self._send_json(200, c)
            else:
                self._send_json(200, contract)
        elif re.match(r"^/contracts/[^/]+/milestones$", path):
            cid = path.split("/")[2]
            items = [m for m in DB["milestones"].values() if m["contract_id"] == cid and m["tenant_id"] == tid]
            self._send_json(200, items)
        elif re.match(r"^/contracts/[^/]+/summary$", path):
            cid = path.split("/")[2]
            contract = DB["contracts"].get(cid)
            if not contract or contract["tenant_id"] != tid:
                self._send_json(404, {"error": "Not found"})
                return
            milestones = [m for m in DB["milestones"].values() if m["contract_id"] == cid]
            payments = [p for p in DB["payment_requests"].values() if p["contract_id"] == cid and p["status"] == "PAID"]
            self._send_json(200, {
                "contract_id": cid, "total_amount": contract["total_amount"],
                "milestone_total": sum(m["amount"] for m in milestones),
                "paid_total": sum(p["amount"] for p in payments),
                "paid_amount": contract["paid_amount"],
            })
        elif re.match(r"^/contracts/[^/]+/vendor-view$", path):
            cid = path.split("/")[2]
            contract = DB["contracts"].get(cid)
            if not contract or contract["tenant_id"] != tid:
                self._send_json(404, {"error": "Not found"})
                return
            c = {k: v for k, v in contract.items() if k not in ("internal_notes", "budget_id")}
            self._send_json(200, c)
        elif re.match(r"^/invoices/[^/]+$", path):
            iid = path.split("/")[2]
            inv = DB["invoices"].get(iid)
            if not inv or inv["tenant_id"] != tid:
                self._send_json(404, {"error": "Not found"})
                return
            self._send_json(200, inv)
        elif path == "/payment-requests":
            items = [p for p in DB["payment_requests"].values() if p["tenant_id"] == tid]
            self._send_json(200, items)
        elif re.match(r"^/payment-requests/[^/]+$", path):
            pid = path.split("/")[2]
            pr = DB["payment_requests"].get(pid)
            if not pr or pr["tenant_id"] != tid:
                self._send_json(404, {"error": "Not found"})
                return
            self._send_json(200, pr)
        elif path == "/budgets":
            items = [b for b in DB["budgets"].values() if b["tenant_id"] == tid]
            self._send_json(200, items)
        elif re.match(r"^/budgets/[^/]+$", path):
            bid = path.split("/")[2]
            budget = DB["budgets"].get(bid)
            if not budget or budget["tenant_id"] != tid:
                self._send_json(404, {"error": "Not found"})
                return
            self._send_json(200, budget)
        elif path == "/audit-logs":
            items = [a for a in DB["audit_logs"] if a["tenant_id"] == tid]
            self._send_json(200, items[-100:])
        elif path == "/reference/departments":
            items = [d for d in DB["departments"].values() if d["tenant_id"] == tid]
            self._send_json(200, items)
        elif path == "/reference/vendors":
            items = [v for v in DB["vendors"].values() if v["tenant_id"] == tid]
            self._send_json(200, items)
        else:
            self._send_json(404, {"error": "Not found"})

    def _route_post(self, user, path, body):
        tid = user["tenant_id"]
        uid = user["id"]
        # Contracts
        if path == "/contracts":
            self._create_contract(user, body)
        elif re.match(r"^/contracts/[^/]+/submit$", path):
            self._transition_contract(path.split("/")[2], "DRAFT", "LEGAL_REVIEW", user, check_submit=True)
        elif re.match(r"^/contracts/[^/]+/legal-approve$", path):
            if user["role"] not in ("legal", "admin"):
                self._send_json(403, {"error": "Only legal/admin can approve"})
                return
            self._transition_contract(path.split("/")[2], "LEGAL_REVIEW", "APPROVED", user)
        elif re.match(r"^/contracts/[^/]+/legal-reject$", path):
            if user["role"] not in ("legal", "admin"):
                self._send_json(403, {"error": "Only legal/admin can reject"})
                return
            cid = path.split("/")[2]
            c = DB["contracts"].get(cid)
            if not c or c["tenant_id"] != tid:
                self._send_json(404, {"error": "Not found"})
                return
            c["status"] = "REJECTED"
            c["rejection_reason"] = body.get("reason", "")
            c["updated_at"] = _now_iso()
            self._send_json(200, c)
        elif re.match(r"^/contracts/[^/]+/return-to-draft$", path):
            self._transition_contract(path.split("/")[2], "REJECTED", "DRAFT", user)
        elif re.match(r"^/contracts/[^/]+/activate$", path):
            self._activate_contract(path.split("/")[2], user)
        elif re.match(r"^/contracts/[^/]+/cancel$", path):
            self._cancel_contract(path.split("/")[2], user)
        elif re.match(r"^/contracts/[^/]+/complete$", path):
            self._complete_contract(path.split("/")[2], user)
        # Milestones
        elif re.match(r"^/contracts/[^/]+/milestones$", path):
            self._create_milestone(user, path.split("/")[2], body)
        elif re.match(r"^/milestones/[^/]+/submit$", path):
            self._submit_milestone(path.split("/")[2], user, body)
        elif re.match(r"^/milestones/[^/]+/accept$", path):
            self._accept_milestone(path.split("/")[2], user, body)
        elif re.match(r"^/milestones/[^/]+/reject$", path):
            self._reject_milestone(path.split("/")[2], user, body)
        # Invoices
        elif path == "/invoices":
            self._create_invoice(user, body)
        # Payment requests
        elif path == "/payment-requests":
            self._create_payment(user, body)
        elif re.match(r"^/payment-requests/[^/]+/manager-approve$", path):
            self._transition_payment(path.split("/")[2], "DRAFT", "MANAGER_APPROVED", user)
        elif re.match(r"^/payment-requests/[^/]+/finance-approve$", path):
            if user["role"] not in ("finance", "admin"):
                self._send_json(403, {"error": "Only finance can approve"})
                return
            self._transition_payment(path.split("/")[2], "MANAGER_APPROVED", "FINANCE_APPROVED", user)
        elif re.match(r"^/payment-requests/[^/]+/pay$", path):
            self._execute_payment(path.split("/")[2], user)
        elif re.match(r"^/payment-requests/[^/]+/reject$", path):
            pid = path.split("/")[2]
            pr = DB["payment_requests"].get(pid)
            if not pr or pr["tenant_id"] != tid:
                self._send_json(404, {"error": "Not found"})
                return
            pr["status"] = "REJECTED"
            pr["rejection_reason"] = body.get("reason", "")
            self._send_json(200, pr)
        else:
            self._send_json(404, {"error": "Not found"})

    def _route_patch(self, user, path, body):
        tid = user["tenant_id"]
        if re.match(r"^/contracts/[^/]+$", path):
            cid = path.split("/")[2]
            c = DB["contracts"].get(cid)
            if not c or c["tenant_id"] != tid:
                self._send_json(404, {"error": "Not found"})
                return
            if c["status"] != "DRAFT":
                self._send_json(409, {"error": "Only DRAFT contracts can be modified"})
                return
            # Optimistic locking
            if_match = self.headers.get("If-Match-Version")
            if if_match is not None and int(if_match) != c["version"]:
                self._send_json(409, {"error": "Version conflict"})
                return
            for field in ("title", "total_amount", "start_date", "end_date", "internal_notes"):
                if field in body:
                    c[field] = body[field]
            c["version"] += 1
            c["updated_at"] = _now_iso()
            _audit(tid, user["id"], "contract", cid, "update", after=c)
            self._send_json(200, c)
        else:
            self._send_json(404, {"error": "Not found"})

    # ── Handlers ──
    def _handle_login(self, body):
        email = body.get("email", "")
        password = body.get("password", "")
        for acc in ACCOUNTS:
            if acc["email"] == email:
                user = TOKEN_MAP[acc["token"]]
                self._send_json(200, {
                    "token": acc["token"], "user_id": user["id"],
                    "tenant_id": user["tenant_id"], "role": user["role"],
                    "full_name": user["full_name"],
                })
                return
        self._send_json(401, {"error": "Invalid credentials"})

    def _create_contract(self, user, body):
        tid = user["tenant_id"]
        required = ["contract_no", "title", "department_id", "vendor_id", "budget_id", "total_amount", "start_date", "end_date"]
        for f in required:
            if not body.get(f):
                self._send_json(422, {"error": f"Missing required field: {f}"})
                return
        if body["total_amount"] <= 0:
            self._send_json(422, {"error": "total_amount must be > 0"})
            return
        if body["start_date"] >= body["end_date"]:
            self._send_json(422, {"error": "start_date must be before end_date"})
            return
        # Uniqueness check
        for c in DB["contracts"].values():
            if c["tenant_id"] == tid and c["contract_no"] == body["contract_no"]:
                self._send_json(409, {"error": "Contract number already exists"})
                return
        # Budget must exist
        if body["budget_id"] not in DB["budgets"]:
            self._send_json(422, {"error": "Budget not found"})
            return
        cid = str(uuid.uuid4())
        contract = {
            "id": cid, "tenant_id": tid, "owner_id": user["id"],
            "contract_no": body["contract_no"], "title": body["title"],
            "department_id": body["department_id"], "vendor_id": body["vendor_id"],
            "budget_id": body["budget_id"], "total_amount": body["total_amount"],
            "currency": body.get("currency", "CNY"),
            "start_date": body["start_date"], "end_date": body["end_date"],
            "internal_notes": body.get("internal_notes"),
            "paid_amount": 0.0, "status": "DRAFT", "version": 1,
            "rejection_reason": None,
            "created_at": _now_iso(), "updated_at": _now_iso(),
        }
        DB["contracts"][cid] = contract
        _audit(tid, user["id"], "contract", cid, "create", after=contract)
        self._send_json(201, contract)

    def _transition_contract(self, cid, from_status, to_status, user, check_submit=False):
        tid = user["tenant_id"]
        c = DB["contracts"].get(cid)
        if not c or c["tenant_id"] != tid:
            self._send_json(404, {"error": "Not found"})
            return
        if c["status"] != from_status:
            self._send_json(409, {"error": f"Contract must be {from_status}, got {c['status']}"})
            return
        if check_submit:
            milestones = [m for m in DB["milestones"].values() if m["contract_id"] == cid]
            if not milestones:
                self._send_json(409, {"error": "At least one milestone required before submit"})
                return
            ms_sum = sum(m["amount"] for m in milestones)
            if abs(ms_sum - c["total_amount"]) > 0.01:
                self._send_json(409, {"error": f"Milestone sum {ms_sum} != contract total {c['total_amount']}"})
                return
        c["status"] = to_status
        c["version"] += 1
        c["updated_at"] = _now_iso()
        _audit(tid, user["id"], "contract", cid, f"transition_to_{to_status}", after=c)
        self._send_json(200, c)

    def _activate_contract(self, cid, user):
        tid = user["tenant_id"]
        c = DB["contracts"].get(cid)
        if not c or c["tenant_id"] != tid:
            self._send_json(404, {"error": "Not found"})
            return
        if c["status"] != "APPROVED":
            self._send_json(409, {"error": f"Contract must be APPROVED, got {c['status']}"})
            return
        budget = DB["budgets"].get(c["budget_id"])
        if not budget:
            self._send_json(409, {"error": "Budget not found"})
            return
        if budget["available_amount"] < c["total_amount"]:
            self._send_json(409, {"error": "Insufficient budget"})
            return
        # BR-CON-008: Budget reservation
        budget["available_amount"] -= c["total_amount"]
        budget["reserved_amount"] += c["total_amount"]
        budget["version"] += 1
        c["status"] = "ACTIVE"
        c["version"] += 1
        c["updated_at"] = _now_iso()
        _audit(tid, user["id"], "contract", cid, "activate", after=c)
        _audit(tid, user["id"], "budget", budget["id"], "reserve", after=budget)
        self._send_json(200, c)

    def _cancel_contract(self, cid, user):
        tid = user["tenant_id"]
        c = DB["contracts"].get(cid)
        if not c or c["tenant_id"] != tid:
            self._send_json(404, {"error": "Not found"})
            return
        if c["status"] in ("COMPLETED", "CANCELLED"):
            self._send_json(409, {"error": f"Cannot cancel {c['status']} contract"})
            return
        # BR-CON-010: Release unpaid reservation
        budget = DB["budgets"].get(c["budget_id"])
        if budget and c["status"] == "ACTIVE":
            unpaid = c["total_amount"] - c["paid_amount"]
            budget["reserved_amount"] -= unpaid
            budget["available_amount"] += unpaid
            budget["version"] += 1
        c["status"] = "CANCELLED"
        c["version"] += 1
        c["updated_at"] = _now_iso()
        _audit(tid, user["id"], "contract", cid, "cancel", after=c)
        self._send_json(200, c)

    def _complete_contract(self, cid, user):
        tid = user["tenant_id"]
        c = DB["contracts"].get(cid)
        if not c or c["tenant_id"] != tid:
            self._send_json(404, {"error": "Not found"})
            return
        if c["status"] != "ACTIVE":
            self._send_json(409, {"error": "Contract must be ACTIVE"})
            return
        milestones = [m for m in DB["milestones"].values() if m["contract_id"] == cid]
        if not all(m["status"] == "ACCEPTED" for m in milestones):
            self._send_json(409, {"error": "All milestones must be ACCEPTED"})
            return
        if c["paid_amount"] < c["total_amount"] - 0.01:
            self._send_json(409, {"error": "Contract not fully paid"})
            return
        active_payments = [p for p in DB["payment_requests"].values()
                          if p["contract_id"] == cid and p["status"] not in ("PAID", "REJECTED")]
        if active_payments:
            self._send_json(409, {"error": "Pending payments exist"})
            return
        c["status"] = "COMPLETED"
        c["version"] += 1
        c["updated_at"] = _now_iso()
        _audit(tid, user["id"], "contract", cid, "complete", after=c)
        self._send_json(200, c)

    def _create_milestone(self, user, cid, body):
        tid = user["tenant_id"]
        c = DB["contracts"].get(cid)
        if not c or c["tenant_id"] != tid:
            self._send_json(404, {"error": "Contract not found"})
            return
        if c["status"] not in ("DRAFT", "LEGAL_REVIEW", "REJECTED"):
            self._send_json(409, {"error": "Cannot add milestones to non-draft contract"})
            return
        for f in ("name", "amount", "due_date"):
            if not body.get(f):
                self._send_json(422, {"error": f"Missing: {f}"})
                return
        if body["amount"] <= 0:
            self._send_json(422, {"error": "amount must be > 0"})
            return
        mid = str(uuid.uuid4())
        milestone = {
            "id": mid, "tenant_id": tid, "contract_id": cid,
            "name": body["name"], "amount": body["amount"],
            "due_date": body["due_date"], "accepted_amount": 0.0,
            "status": "PENDING", "submission_version": 0,
            "evidence_url": None, "version": 1,
        }
        DB["milestones"][mid] = milestone
        _audit(tid, user["id"], "milestone", mid, "create", after=milestone)
        self._send_json(201, milestone)

    def _submit_milestone(self, mid, user, body):
        tid = user["tenant_id"]
        m = DB["milestones"].get(mid)
        if not m or m["tenant_id"] != tid:
            self._send_json(404, {"error": "Not found"})
            return
        if m["status"] not in ("PENDING", "REJECTED"):
            self._send_json(409, {"error": f"Cannot submit from {m['status']}"})
            return
        if not body.get("evidence_url"):
            self._send_json(422, {"error": "evidence_url required"})
            return
        m["status"] = "SUBMITTED"
        m["evidence_url"] = body["evidence_url"]
        m["submission_version"] += 1
        m["version"] += 1
        _audit(tid, user["id"], "milestone", mid, "submit", after=m)
        self._send_json(200, m)

    def _accept_milestone(self, mid, user, body):
        tid = user["tenant_id"]
        m = DB["milestones"].get(mid)
        if not m or m["tenant_id"] != tid:
            self._send_json(404, {"error": "Not found"})
            return
        if m["status"] != "SUBMITTED":
            self._send_json(409, {"error": f"Cannot accept from {m['status']}"})
            return
        accepted_amount = body.get("accepted_amount", m["amount"])
        if accepted_amount > m["amount"]:
            self._send_json(422, {"error": "accepted_amount exceeds milestone amount"})
            return
        m["status"] = "ACCEPTED"
        m["accepted_amount"] = accepted_amount
        m["version"] += 1
        _audit(tid, user["id"], "milestone", mid, "accept", after=m)
        self._send_json(200, m)

    def _reject_milestone(self, mid, user, body):
        tid = user["tenant_id"]
        m = DB["milestones"].get(mid)
        if not m or m["tenant_id"] != tid:
            self._send_json(404, {"error": "Not found"})
            return
        if m["status"] != "SUBMITTED":
            self._send_json(409, {"error": f"Cannot reject from {m['status']}"})
            return
        m["status"] = "REJECTED"
        m["version"] += 1
        _audit(tid, user["id"], "milestone", mid, "reject", after=m)
        self._send_json(200, m)

    def _create_invoice(self, user, body):
        tid = user["tenant_id"]
        for f in ("contract_id", "invoice_no", "subtotal", "tax_amount", "issue_date"):
            if f not in body:
                self._send_json(422, {"error": f"Missing: {f}"})
                return
        if body["subtotal"] < 0 or body["tax_amount"] < 0:
            self._send_json(422, {"error": "Amounts must be non-negative"})
            return
        # Uniqueness
        for inv in DB["invoices"].values():
            if inv["tenant_id"] == tid and inv["invoice_no"] == body["invoice_no"]:
                self._send_json(409, {"error": "Invoice number already exists"})
                return
        iid = str(uuid.uuid4())
        invoice = {
            "id": iid, "tenant_id": tid, "contract_id": body["contract_id"],
            "invoice_no": body["invoice_no"], "subtotal": body["subtotal"],
            "tax_amount": body["tax_amount"],
            "total_amount": body["subtotal"] + body["tax_amount"],
            "issue_date": body["issue_date"], "status": "VALID",
            "vendor_id": body.get("vendor_id", ""), "created_by": user["id"],
        }
        DB["invoices"][iid] = invoice
        _audit(tid, user["id"], "invoice", iid, "create", after=invoice)
        self._send_json(201, invoice)

    def _create_payment(self, user, body):
        tid = user["tenant_id"]
        for f in ("contract_id", "milestone_id", "invoice_id", "amount"):
            if f not in body:
                self._send_json(422, {"error": f"Missing: {f}"})
                return
        if body["amount"] <= 0:
            self._send_json(422, {"error": "amount must be > 0"})
            return
        contract = DB["contracts"].get(body["contract_id"])
        if not contract or contract["tenant_id"] != tid:
            self._send_json(404, {"error": "Contract not found"})
            return
        if contract["status"] != "ACTIVE":
            self._send_json(409, {"error": "Contract must be ACTIVE"})
            return
        milestone = DB["milestones"].get(body["milestone_id"])
        if not milestone or milestone["status"] != "ACCEPTED":
            self._send_json(409, {"error": "Milestone must be ACCEPTED"})
            return
        invoice = DB["invoices"].get(body["invoice_id"])
        if not invoice or invoice["status"] != "VALID":
            self._send_json(409, {"error": "Invoice must be VALID"})
            return
        # BR-PAY-004: Cumulative payment limit
        if contract["paid_amount"] + body["amount"] > contract["total_amount"] + 0.01:
            self._send_json(409, {"error": "Payment exceeds contract total"})
            return
        # BR-PAY-003: Milestone remaining
        ms_paid = sum(p["amount"] for p in DB["payment_requests"].values()
                      if p["milestone_id"] == body["milestone_id"] and p["status"] in ("DRAFT", "MANAGER_APPROVED", "FINANCE_APPROVED", "PAID"))
        if ms_paid + body["amount"] > milestone["accepted_amount"] + 0.01:
            self._send_json(409, {"error": "Payment exceeds milestone remaining"})
            return
        pid = str(uuid.uuid4())
        payment = {
            "id": pid, "tenant_id": tid, "contract_id": body["contract_id"],
            "milestone_id": body["milestone_id"], "invoice_id": body["invoice_id"],
            "amount": body["amount"], "status": "DRAFT",
            "requested_by": user["id"], "idempotency_key": None,
            "manager_approved_by": None, "finance_approved_by": None,
            "paid_at": None, "rejection_reason": None, "version": 1,
        }
        DB["payment_requests"][pid] = payment
        _audit(tid, user["id"], "payment_request", pid, "create", after=payment)
        self._send_json(201, payment)

    def _transition_payment(self, pid, from_status, to_status, user):
        tid = user["tenant_id"]
        pr = DB["payment_requests"].get(pid)
        if not pr or pr["tenant_id"] != tid:
            self._send_json(404, {"error": "Not found"})
            return
        if pr["status"] != from_status:
            self._send_json(409, {"error": f"Payment must be {from_status}, got {pr['status']}"})
            return
        pr["status"] = to_status
        if to_status == "MANAGER_APPROVED":
            pr["manager_approved_by"] = user["id"]
        elif to_status == "FINANCE_APPROVED":
            pr["finance_approved_by"] = user["id"]
        pr["version"] += 1
        _audit(tid, user["id"], "payment_request", pid, f"transition_to_{to_status}", after=pr)
        self._send_json(200, pr)

    def _execute_payment(self, pid, user):
        tid = user["tenant_id"]
        if user["role"] not in ("finance", "admin"):
            self._send_json(403, {"error": "Only finance can execute payment"})
            return
        pr = DB["payment_requests"].get(pid)
        if not pr or pr["tenant_id"] != tid:
            self._send_json(404, {"error": "Not found"})
            return
        if pr["status"] != "FINANCE_APPROVED":
            self._send_json(409, {"error": f"Payment must be FINANCE_APPROVED, got {pr['status']}"})
            return
        # Idempotency
        idem_key = self.headers.get("Idempotency-Key", "")
        if not idem_key or len(idem_key) < 8:
            self._send_json(422, {"error": "Idempotency-Key header required (min 8 chars)"})
            return
        if idem_key in IDEMPOTENCY_STORE:
            self._send_json(200, IDEMPOTENCY_STORE[idem_key])
            return
        # BR-PAY-009: Budget conservation
        contract = DB["contracts"].get(pr["contract_id"])
        budget = DB["budgets"].get(contract["budget_id"]) if contract else None
        if budget:
            budget["reserved_amount"] -= pr["amount"]
            budget["spent_amount"] += pr["amount"]
            budget["version"] += 1
        if contract:
            contract["paid_amount"] += pr["amount"]
            contract["version"] += 1
        pr["status"] = "PAID"
        pr["paid_at"] = _now_iso()
        pr["idempotency_key"] = idem_key
        pr["version"] += 1
        IDEMPOTENCY_STORE[idem_key] = pr
        _audit(tid, user["id"], "payment_request", pid, "pay", after=pr)
        if budget:
            _audit(tid, user["id"], "budget", budget["id"], "spend", after=budget)
        self._send_json(200, pr)


def run_server(port=8000):
    server = HTTPServer(("0.0.0.0", port), ContractFlowHandler)
    print(f"ContractFlow Mock Server running on http://localhost:{port}/api/v1")
    print(f"Tenants: {list(TENANTS.keys())}")
    print(f"Accounts: {len(ACCOUNTS)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    run_server()

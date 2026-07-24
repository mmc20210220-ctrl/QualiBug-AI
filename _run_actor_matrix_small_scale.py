"""Actor Matrix Expansion — Targeted Small Scale Execution.

PROJECT_D_ACTOR_MATRIX_SMALL_SCALE_V1
Target rules: 6 (BR-TENANT-001, BR-AUTH-014, BR-AUTH-012, BR-TENANT-006, BR-AUTH-011, BR-AUTH-015)
Experiment limit: <=32

Tests the actor_matrix_planning module against Project D mock server.
Validates that discriminating actor pairs are generated and executed correctly.
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

BASE_URL = "http://127.0.0.1:8002"
MAX_EXPERIMENTS = 32
RUN_ID = "PROJECT_D_ACTOR_MATRIX_SMALL_SCALE_V1"

# ─── Tokens (from TEST_ACCOUNTS.md) ────────────────────────────────────────────
TOKENS = {
    # ACME tenant
    "alice": "customer-alice-token",   # CUSTOMER, acme
    "bob": "customer-bob-token",       # CUSTOMER, acme
    "dave": "agent-dave-token",        # AGENT, acme
    "eve": "agent-eve-token",          # AGENT, acme
    "grace": "supervisor-grace-token", # SUPERVISOR, acme
    "ivan": "admin-ivan-token",        # ADMIN, acme
    # Globex tenant
    "carol": "customer-carol-token",   # CUSTOMER, globex
    "frank": "agent-frank-token",      # AGENT, globex
    "henry": "supervisor-henry-token", # SUPERVISOR, globex
    "judy": "admin-judy-token",        # ADMIN, globex
}

# ─── HTTP Client ───────────────────────────────────────────────────────────────

def _http(method: str, path: str, token: str = "", body: dict | None = None) -> dict:
    """Execute HTTP request against mock server."""
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"status_code": resp.status, "body": json.loads(resp.read().decode()), "error": None}
    except urllib.error.HTTPError as e:
        try:
            return {"status_code": e.code, "body": json.loads(e.read().decode()), "error": None}
        except Exception:
            return {"status_code": e.code, "body": {}, "error": None}
    except Exception as e:
        return {"status_code": 0, "body": {}, "error": str(e)}


def _get(path: str, token: str = "") -> dict:
    return _http("GET", path, token)


def _post(path: str, token: str = "", body: dict | None = None) -> dict:
    return _http("POST", path, token, body)


def _put(path: str, token: str = "", body: dict | None = None) -> dict:
    return _http("PUT", path, token, body)


def _delete(path: str, token: str = "") -> dict:
    return _http("DELETE", path, token)


# ─── Behavior IR Builder ───────────────────────────────────────────────────────

def build_behavior_ir() -> dict:
    """Build Behavior IR from Project D input files."""
    return {
        "actors": [
            # ACME tenant
            {"id": "actor_alice", "role": "customer", "tenant": "acme", "credential_secret_ref": "secret_ref:test_accounts:alice", "status": "active"},
            {"id": "actor_bob", "role": "customer", "tenant": "acme", "credential_secret_ref": "secret_ref:test_accounts:bob", "status": "active"},
            {"id": "actor_dave", "role": "agent", "tenant": "acme", "credential_secret_ref": "secret_ref:test_accounts:dave", "status": "active"},
            {"id": "actor_eve", "role": "agent", "tenant": "acme", "credential_secret_ref": "secret_ref:test_accounts:eve", "status": "active"},
            {"id": "actor_grace", "role": "supervisor", "tenant": "acme", "credential_secret_ref": "secret_ref:test_accounts:grace", "status": "active"},
            {"id": "actor_ivan", "role": "admin", "tenant": "acme", "credential_secret_ref": "secret_ref:test_accounts:ivan", "status": "active"},
            # Globex tenant
            {"id": "actor_carol", "role": "customer", "tenant": "globex", "credential_secret_ref": "secret_ref:test_accounts:carol", "status": "active"},
            {"id": "actor_frank", "role": "agent", "tenant": "globex", "credential_secret_ref": "secret_ref:test_accounts:frank", "status": "active"},
            {"id": "actor_henry", "role": "supervisor", "tenant": "globex", "credential_secret_ref": "secret_ref:test_accounts:henry", "status": "active"},
            {"id": "actor_judy", "role": "admin", "tenant": "globex", "credential_secret_ref": "secret_ref:test_accounts:judy", "status": "active"},
        ],
        "operations": [
            {"id": "get_ticket", "method": "GET", "path": "/tickets/{id}"},
            {"id": "reopen_ticket", "method": "POST", "path": "/tickets/{id}/reopen"},
            {"id": "update_ticket", "method": "PUT", "path": "/tickets/{id}"},
            {"id": "list_customers", "method": "GET", "path": "/customers"},
            {"id": "update_customer", "method": "PUT", "path": "/customers/{id}"},
            {"id": "delete_attachment", "method": "DELETE", "path": "/tickets/{id}/attachments/{attachmentId}"},
        ],
        "relations": [
            {"relation_type": "belongs_to", "from_ref": "Ticket", "to_ref": "Tenant", "field": "tenant"},
            {"relation_type": "owned_by", "from_ref": "Ticket", "to_ref": "Customer", "field": "customer_id"},
            {"relation_type": "belongs_to", "from_ref": "Customer", "to_ref": "Tenant", "field": "tenant"},
            {"relation_type": "uploaded_by", "from_ref": "Attachment", "to_ref": "User", "field": "uploaded_by"},
        ],
        "invariants": [
            {"id": "inv_tenant_001", "rule_type": "TENANT_ISOLATION", "entity_ref": "Ticket", "description": "Ticket belongs to tenant, cross-tenant read forbidden"},
            {"id": "inv_auth_014", "rule_type": "AUTHORIZATION", "entity_ref": "Ticket", "description": "reopen_ticket requires owner (customer_id)"},
            {"id": "inv_auth_012", "rule_type": "AUTHORIZATION", "entity_ref": "Ticket", "description": "update_ticket requires owner or assigned agent"},
            {"id": "inv_tenant_006", "rule_type": "TENANT_ISOLATION", "entity_ref": "Customer", "description": "Customer list must be filtered by tenant"},
            {"id": "inv_auth_011", "rule_type": "AUTHORIZATION", "entity_ref": "Customer", "description": "update_customer requires admin or self"},
            {"id": "inv_auth_015", "rule_type": "AUTHORIZATION", "entity_ref": "Attachment", "description": "delete_attachment requires uploader or supervisor/admin"},
        ],
        "states": [],
    }


def build_obligations() -> list[dict]:
    """Build obligations for the 6 target rules."""
    return [
        # TSLA-BUG-002: Cross-tenant ticket read
        {
            "obligation_id": "obl_tenant_001",
            "risk_family": "isolation",
            "property": {
                "invariant_ref": "inv_tenant_001",
                "operation_ref": "get_ticket",
                "expression": {
                    "rule_type": "TENANT_ISOLATION",
                    "description": "Resources must be scoped to tenant. Cross-tenant access forbidden.",
                    "tenant_field": "tenant",
                },
            },
            "source_refs": [{"source_id": "BR-TENANT-001"}],
        },
        # TSLA-BUG-020: Non-owner ticket reopen
        {
            "obligation_id": "obl_auth_014",
            "risk_family": "authorization",
            "property": {
                "invariant_ref": "inv_auth_014",
                "operation_ref": "reopen_ticket",
                "expression": {
                    "rule_type": "AUTHORIZATION",
                    "description": "Only ticket owner can reopen. customer_id ownership check required.",
                    "owner_field": "customer_id",
                },
            },
            "source_refs": [{"source_id": "BR-AUTH-014"}],
        },
        # TSLA-BUG-030: Non-owner ticket update
        {
            "obligation_id": "obl_auth_012",
            "risk_family": "authorization",
            "property": {
                "invariant_ref": "inv_auth_012",
                "operation_ref": "update_ticket",
                "expression": {
                    "rule_type": "AUTHORIZATION",
                    "description": "Only ticket owner or assigned agent can update.",
                    "owner_field": "customer_id",
                },
            },
            "source_refs": [{"source_id": "BR-AUTH-012"}],
        },
        # TSLA-BUG-037: Cross-tenant customer list
        {
            "obligation_id": "obl_tenant_006",
            "risk_family": "isolation",
            "property": {
                "invariant_ref": "inv_tenant_006",
                "operation_ref": "list_customers",
                "expression": {
                    "rule_type": "TENANT_ISOLATION",
                    "description": "Customer list must be filtered by tenant.",
                    "tenant_field": "tenant",
                },
            },
            "source_refs": [{"source_id": "BR-TENANT-006"}],
        },
        # TSLA-BUG-040: Non-admin customer update
        {
            "obligation_id": "obl_auth_011",
            "risk_family": "authorization",
            "property": {
                "invariant_ref": "inv_auth_011",
                "operation_ref": "update_customer",
                "expression": {
                    "rule_type": "AUTHORIZATION",
                    "description": "Only admin or self can update customer.",
                    "owner_field": "id",
                },
            },
            "source_refs": [{"source_id": "BR-AUTH-011"}],
        },
        # TSLA-BUG-042: Non-uploader attachment delete
        {
            "obligation_id": "obl_auth_015",
            "risk_family": "authorization",
            "property": {
                "invariant_ref": "inv_auth_015",
                "operation_ref": "delete_attachment",
                "expression": {
                    "rule_type": "AUTHORIZATION",
                    "description": "Only uploader or supervisor/admin can delete attachment.",
                    "owner_field": "uploaded_by",
                },
            },
            "source_refs": [{"source_id": "BR-AUTH-015"}],
        },
    ]


# ─── Seed Data Discovery ───────────────────────────────────────────────────────

def discover_seed_data() -> dict:
    """Discover seed data from mock server."""
    seed = {
        "acme_tickets": [],
        "globex_tickets": [],
        "acme_customers": [],
        "globex_customers": [],
        "attachments": [],
    }
    
    # Get tickets as admin (sees all)
    r = _get("/tickets", TOKENS["ivan"])
    if r["status_code"] == 200:
        for t in r["body"].get("tickets", []):
            if t.get("tenant") == "acme":
                seed["acme_tickets"].append(t)
            else:
                seed["globex_tickets"].append(t)
    
    # Get customers as admin
    r = _get("/customers", TOKENS["ivan"])
    if r["status_code"] == 200:
        for c in r["body"].get("customers", []):
            if c.get("tenant") == "acme":
                seed["acme_customers"].append(c)
            else:
                seed["globex_customers"].append(c)
    
    return seed


# ─── Experiment Execution ──────────────────────────────────────────────────────

def execute_actor_matrix_experiment(exp: dict, seed: dict) -> dict:
    """Execute a single actor matrix experiment."""
    mechanism = exp.get("mechanism", "")
    rule_id = exp.get("rule_id", "")
    obligation_id = exp.get("obligation_id", "")
    actor_matrix = exp.get("actor_matrix_result", {})
    treatment_plan = exp.get("treatment_plan", [])
    
    receipt = {
        "experiment_id": exp.get("experiment_id"),
        "obligation_id": obligation_id,
        "rule_id": rule_id,
        "mechanism": mechanism,
        "actor_matrix_status": actor_matrix.get("status") if isinstance(actor_matrix, dict) else "N/A",
        "control_result": None,
        "treatment_results": [],
        "verdict": "INDETERMINATE",
        "bug_detected": False,
        "details": {},
    }
    
    # Dispatch to mechanism-specific executor
    if "TENANT_ISOLATION" in mechanism:
        _exec_tenant_isolation(receipt, exp, seed)
    elif "AUTHORIZATION" in mechanism:
        _exec_authorization(receipt, exp, seed)
    else:
        receipt["verdict"] = "MECHANISM_NOT_EXECUTABLE"
        receipt["details"]["reason"] = f"No executor for {mechanism}"
    
    return receipt


def _exec_tenant_isolation(receipt: dict, exp: dict, seed: dict):
    """Execute tenant isolation experiment."""
    treatment_plan = exp.get("treatment_plan", [])
    obligation_id = exp.get("obligation_id", "")
    
    # Determine target based on obligation
    if "tenant_001" in obligation_id:
        # TSLA-BUG-002: GET /tickets/{id} cross-tenant
        target_ticket = seed["acme_tickets"][0] if seed["acme_tickets"] else None
        if not target_ticket:
            receipt["verdict"] = "BLOCKED_NO_SEED"
            return
        
        ticket_id = target_ticket["id"]
        
        # Control: ACME user reads ACME ticket (should succeed)
        control_token = TOKENS["alice"]  # ACME customer
        r = _get(f"/tickets/{ticket_id}", control_token)
        receipt["control_result"] = {
            "actor": "alice",
            "tenant": "acme",
            "status_code": r["status_code"],
            "expected": 200,
            "passed": r["status_code"] == 200,
        }
        
        # Treatment: Globex user reads ACME ticket (should fail with 403)
        for step in treatment_plan:
            mutation = step.get("mutation", {})
            actor_ref = mutation.get("actor_ref", "")
            dim = mutation.get("dimension_under_test", "")
            
            # Map actor_ref to token
            token = _resolve_actor_token(actor_ref)
            if not token:
                continue
            
            r = _get(f"/tickets/{ticket_id}", token)
            treatment_result = {
                "step_id": step.get("step_id"),
                "actor_ref": actor_ref,
                "dimension": dim,
                "status_code": r["status_code"],
                "expected": 403,  # Should be forbidden
                "actual_forbidden": r["status_code"] in (401, 403),
                "bug_detected": r["status_code"] == 200,  # Bug if access granted
            }
            receipt["treatment_results"].append(treatment_result)
            
            if treatment_result["bug_detected"]:
                receipt["bug_detected"] = True
                receipt["verdict"] = "PROPERTY_VIOLATED"
                receipt["details"]["violation"] = f"Cross-tenant read allowed: {actor_ref} accessed {ticket_id}"
        
        if not receipt["bug_detected"] and receipt["treatment_results"]:
            receipt["verdict"] = "PROPERTY_HELD"
    
    elif "tenant_006" in obligation_id:
        # TSLA-BUG-037: GET /customers cross-tenant list
        # Control: ACME admin lists customers (should see only ACME)
        control_token = TOKENS["ivan"]  # ACME admin
        r = _get("/customers", control_token)
        acme_count = len([c for c in r["body"].get("customers", []) if c.get("tenant") == "acme"])
        globex_count = len([c for c in r["body"].get("customers", []) if c.get("tenant") == "globex"])
        
        receipt["control_result"] = {
            "actor": "ivan",
            "tenant": "acme",
            "status_code": r["status_code"],
            "acme_customers": acme_count,
            "globex_customers": globex_count,
            "expected_isolated": globex_count == 0,
        }
        
        # Treatment: Check if cross-tenant data leaks
        for step in treatment_plan:
            mutation = step.get("mutation", {})
            actor_ref = mutation.get("actor_ref", "")
            dim = mutation.get("dimension_under_test", "")
            
            token = _resolve_actor_token(actor_ref)
            if not token:
                continue
            
            r = _get("/customers", token)
            customers = r["body"].get("customers", [])
            cross_tenant = [c for c in customers if c.get("tenant") != "acme"]
            
            treatment_result = {
                "step_id": step.get("step_id"),
                "actor_ref": actor_ref,
                "dimension": dim,
                "status_code": r["status_code"],
                "total_customers": len(customers),
                "cross_tenant_leaked": len(cross_tenant),
                "bug_detected": len(cross_tenant) > 0,
            }
            receipt["treatment_results"].append(treatment_result)
            
            if treatment_result["bug_detected"]:
                receipt["bug_detected"] = True
                receipt["verdict"] = "PROPERTY_VIOLATED"
                receipt["details"]["violation"] = f"Cross-tenant customer list leak: {len(cross_tenant)} records"
        
        if not receipt["bug_detected"] and receipt["treatment_results"]:
            receipt["verdict"] = "PROPERTY_HELD"


def _exec_authorization(receipt: dict, exp: dict, seed: dict):
    """Execute authorization experiment."""
    treatment_plan = exp.get("treatment_plan", [])
    obligation_id = exp.get("obligation_id", "")
    
    if "auth_014" in obligation_id:
        # TSLA-BUG-020: POST /tickets/{id}/reopen - owner only
        # Find a RESOLVED or CLOSED ticket owned by alice
        target_ticket = None
        for t in seed["acme_tickets"]:
            if t.get("customer_id") == "cust-001" and t.get("status") in ("RESOLVED", "CLOSED"):
                target_ticket = t
                break
        
        # If no resolved ticket, use any ticket and try to reopen
        if not target_ticket and seed["acme_tickets"]:
            target_ticket = seed["acme_tickets"][0]
        
        if not target_ticket:
            receipt["verdict"] = "BLOCKED_NO_SEED"
            return
        
        ticket_id = target_ticket["id"]
        owner_id = target_ticket.get("customer_id")
        
        # Control: Owner reopens (should succeed if status allows)
        control_token = TOKENS["alice"] if owner_id == "cust-001" else TOKENS["bob"]
        r = _post(f"/tickets/{ticket_id}/reopen", control_token, {"reason": "Test reopen by owner"})
        receipt["control_result"] = {
            "actor": "alice" if owner_id == "cust-001" else "bob",
            "owner_id": owner_id,
            "status_code": r["status_code"],
            "note": "Control may fail due to state, but authorization should pass",
        }
        
        # Treatment: Non-owner tries to reopen (should fail with 403)
        for step in treatment_plan:
            mutation = step.get("mutation", {})
            actor_ref = mutation.get("actor_ref", "")
            dim = mutation.get("dimension_under_test", "")
            
            token = _resolve_actor_token(actor_ref)
            if not token:
                continue
            
            # Skip if this is the owner
            if actor_ref == "actor_alice" and owner_id == "cust-001":
                continue
            if actor_ref == "actor_bob" and owner_id == "cust-002":
                continue
            
            r = _post(f"/tickets/{ticket_id}/reopen", token, {"reason": "Test reopen by non-owner"})
            
            # Bug if non-owner can reopen (403 expected, 200 = bug)
            bug = r["status_code"] == 200
            treatment_result = {
                "step_id": step.get("step_id"),
                "actor_ref": actor_ref,
                "dimension": dim,
                "status_code": r["status_code"],
                "expected": 403,
                "bug_detected": bug,
            }
            receipt["treatment_results"].append(treatment_result)
            
            if bug:
                receipt["bug_detected"] = True
                receipt["verdict"] = "PROPERTY_VIOLATED"
                receipt["details"]["violation"] = f"Non-owner reopen allowed: {actor_ref}"
        
        if not receipt["bug_detected"] and receipt["treatment_results"]:
            receipt["verdict"] = "PROPERTY_HELD"
    
    elif "auth_012" in obligation_id:
        # TSLA-BUG-030: PUT /tickets/{id} - owner/agent only
        target_ticket = seed["acme_tickets"][0] if seed["acme_tickets"] else None
        if not target_ticket:
            receipt["verdict"] = "BLOCKED_NO_SEED"
            return
        
        ticket_id = target_ticket["id"]
        
        # Control: Owner updates (should succeed)
        control_token = TOKENS["alice"]
        r = _put(f"/tickets/{ticket_id}", control_token, {"title": "Updated by owner"})
        receipt["control_result"] = {
            "actor": "alice",
            "status_code": r["status_code"],
            "expected": 200,
            "passed": r["status_code"] == 200,
        }
        
        # Treatment: Non-owner updates (should fail)
        for step in treatment_plan:
            mutation = step.get("mutation", {})
            actor_ref = mutation.get("actor_ref", "")
            dim = mutation.get("dimension_under_test", "")
            
            token = _resolve_actor_token(actor_ref)
            if not token:
                continue
            
            # Skip owner
            if actor_ref in ("actor_alice", "actor_bob"):
                continue
            
            r = _put(f"/tickets/{ticket_id}", token, {"title": "Updated by non-owner"})
            bug = r["status_code"] == 200
            treatment_result = {
                "step_id": step.get("step_id"),
                "actor_ref": actor_ref,
                "dimension": dim,
                "status_code": r["status_code"],
                "expected": 403,
                "bug_detected": bug,
            }
            receipt["treatment_results"].append(treatment_result)
            
            if bug:
                receipt["bug_detected"] = True
                receipt["verdict"] = "PROPERTY_VIOLATED"
                receipt["details"]["violation"] = f"Non-owner update allowed: {actor_ref}"
        
        if not receipt["bug_detected"] and receipt["treatment_results"]:
            receipt["verdict"] = "PROPERTY_HELD"
    
    elif "auth_011" in obligation_id:
        # TSLA-BUG-040: PUT /customers/{id} - admin/self only
        target_customer = seed["acme_customers"][0] if seed["acme_customers"] else None
        if not target_customer:
            receipt["verdict"] = "BLOCKED_NO_SEED"
            return
        
        customer_id = target_customer["id"]
        
        # Control: Admin updates (should succeed)
        control_token = TOKENS["ivan"]
        r = _put(f"/customers/{customer_id}", control_token, {"name": "Updated by admin"})
        receipt["control_result"] = {
            "actor": "ivan",
            "status_code": r["status_code"],
            "expected": 200,
            "passed": r["status_code"] == 200,
        }
        
        # Treatment: Non-admin non-self updates (should fail)
        for step in treatment_plan:
            mutation = step.get("mutation", {})
            actor_ref = mutation.get("actor_ref", "")
            dim = mutation.get("dimension_under_test", "")
            
            token = _resolve_actor_token(actor_ref)
            if not token:
                continue
            
            # Skip admin
            if actor_ref in ("actor_ivan", "actor_judy"):
                continue
            
            r = _put(f"/customers/{customer_id}", token, {"name": "Updated by non-admin"})
            bug = r["status_code"] == 200
            treatment_result = {
                "step_id": step.get("step_id"),
                "actor_ref": actor_ref,
                "dimension": dim,
                "status_code": r["status_code"],
                "expected": 403,
                "bug_detected": bug,
            }
            receipt["treatment_results"].append(treatment_result)
            
            if bug:
                receipt["bug_detected"] = True
                receipt["verdict"] = "PROPERTY_VIOLATED"
                receipt["details"]["violation"] = f"Non-admin customer update allowed: {actor_ref}"
        
        if not receipt["bug_detected"] and receipt["treatment_results"]:
            receipt["verdict"] = "PROPERTY_HELD"
    
    elif "auth_015" in obligation_id:
        # TSLA-BUG-042: DELETE /tickets/{id}/attachments/{id} - uploader only
        # First create an attachment as alice
        target_ticket = seed["acme_tickets"][0] if seed["acme_tickets"] else None
        if not target_ticket:
            receipt["verdict"] = "BLOCKED_NO_SEED"
            return
        
        ticket_id = target_ticket["id"]
        
        # Create attachment as alice
        r = _post(f"/tickets/{ticket_id}/attachments", TOKENS["alice"], {"filename": "test.txt", "size": 100})
        if r["status_code"] != 201:
            receipt["verdict"] = "BLOCKED_SETUP_FAILED"
            return
        
        attachment_id = r["body"].get("id")
        
        # Control: Uploader deletes (should succeed)
        # First create another attachment for control
        r = _post(f"/tickets/{ticket_id}/attachments", TOKENS["alice"], {"filename": "control.txt", "size": 100})
        control_attachment_id = r["body"].get("id") if r["status_code"] == 201 else None
        
        if control_attachment_id:
            r = _delete(f"/tickets/{ticket_id}/attachments/{control_attachment_id}", TOKENS["alice"])
            receipt["control_result"] = {
                "actor": "alice",
                "status_code": r["status_code"],
                "expected": 200,
                "passed": r["status_code"] == 200,
            }
        
        # Treatment: Non-uploader deletes (should fail)
        for step in treatment_plan:
            mutation = step.get("mutation", {})
            actor_ref = mutation.get("actor_ref", "")
            dim = mutation.get("dimension_under_test", "")
            
            token = _resolve_actor_token(actor_ref)
            if not token:
                continue
            
            # Skip uploader (alice)
            if actor_ref == "actor_alice":
                continue
            
            r = _delete(f"/tickets/{ticket_id}/attachments/{attachment_id}", token)
            bug = r["status_code"] == 200
            treatment_result = {
                "step_id": step.get("step_id"),
                "actor_ref": actor_ref,
                "dimension": dim,
                "status_code": r["status_code"],
                "expected": 403,
                "bug_detected": bug,
            }
            receipt["treatment_results"].append(treatment_result)
            
            if bug:
                receipt["bug_detected"] = True
                receipt["verdict"] = "PROPERTY_VIOLATED"
                receipt["details"]["violation"] = f"Non-uploader delete allowed: {actor_ref}"
        
        if not receipt["bug_detected"] and receipt["treatment_results"]:
            receipt["verdict"] = "PROPERTY_HELD"


def _resolve_actor_token(actor_ref: str) -> str:
    """Map actor_ref to token."""
    mapping = {
        "actor_alice": TOKENS["alice"],
        "actor_bob": TOKENS["bob"],
        "actor_dave": TOKENS["dave"],
        "actor_eve": TOKENS["eve"],
        "actor_grace": TOKENS["grace"],
        "actor_ivan": TOKENS["ivan"],
        "actor_carol": TOKENS["carol"],
        "actor_frank": TOKENS["frank"],
        "actor_henry": TOKENS["henry"],
        "actor_judy": TOKENS["judy"],
    }
    return mapping.get(actor_ref, "")


# ─── Main ──────────────────────────────────────────────────────────────────────

def run_small_scale():
    """Execute targeted small scale run."""
    print(f"{'='*70}")
    print(f"  ACTOR MATRIX EXPANSION - TARGETED SMALL SCALE")
    print(f"  Run ID: {RUN_ID}")
    print(f"  Max Experiments: {MAX_EXPERIMENTS}")
    print(f"  Target: {BASE_URL}")
    print(f"{'='*70}")
    
    # 1. Health check
    print("\n[1/5] Health check...")
    r = _get("/health")
    if r["status_code"] != 200:
        print(f"  FATAL: Server not reachable at {BASE_URL}")
        print("  Start mock server: python projects/ticketsla_d/mock_server.py 8002")
        sys.exit(1)
    print(f"  Server OK (status={r['status_code']})")
    
    # 2. Build inputs
    print("\n[2/5] Building Behavior IR and Obligations...")
    behavior_ir = build_behavior_ir()
    obligations = build_obligations()
    print(f"  Actors: {len(behavior_ir['actors'])}")
    print(f"  Operations: {len(behavior_ir['operations'])}")
    print(f"  Invariants: {len(behavior_ir['invariants'])}")
    print(f"  Obligations: {len(obligations)}")
    
    # 3. Run Planner
    print("\n[3/5] Running Deep Experiment Planner with Actor Matrix...")
    plan_result = plan_deep_experiments(
        obligations=obligations,
        experiments_by_obligation={},
        behavior_ir=behavior_ir,
        budget=MAX_EXPERIMENTS,
    )
    experiments = plan_result["deep_experiments"]
    print(f"  Planned: {plan_result['planned_count']}")
    print(f"  Skipped: {plan_result['skipped_count']}")
    print(f"  Mechanism counts: {json.dumps(plan_result['mechanism_counts'], indent=4)}")
    
    # Show actor matrix details
    for exp in experiments:
        am = exp.get("actor_matrix_result", {})
        if isinstance(am, dict) and am.get("status") == "COMPLETE":
            print(f"\n  Experiment: {exp['experiment_id']}")
            print(f"    Rule: {exp['rule_id']}")
            print(f"    Mechanism: {exp['mechanism']}")
            print(f"    Actor Matrix: {am.get('status')}")
            print(f"    Candidates: {len(am.get('candidates', []))}")
            print(f"    Pairs: {len(am.get('discriminating_pairs', []))}")
            for pair in am.get("discriminating_pairs", []):
                ctrl = pair.get("control_actor", {})
                viol = pair.get("violation_actor", {})
                print(f"      Pair: {ctrl.get('actor_id')}({ctrl.get('relation_type')}) vs {viol.get('actor_id')}({viol.get('relation_type')}) dim={pair.get('dimension_under_test')} quality={pair.get('discrimination_quality')}")
    
    if not experiments:
        print("\n  FATAL: No experiments planned.")
        sys.exit(1)
    
    # 4. Discover seed data
    print("\n[4/5] Discovering seed data...")
    seed = discover_seed_data()
    print(f"  ACME tickets: {len(seed['acme_tickets'])}")
    print(f"  Globex tickets: {len(seed['globex_tickets'])}")
    print(f"  ACME customers: {len(seed['acme_customers'])}")
    print(f"  Globex customers: {len(seed['globex_customers'])}")
    
    # 5. Execute experiments
    print(f"\n[5/5] Executing {len(experiments)} experiments...")
    receipts = []
    bugs_found = []
    
    for i, exp in enumerate(experiments[:MAX_EXPERIMENTS]):
        mechanism = exp.get("mechanism", "?")
        rule_id = exp.get("rule_id", "?")
        print(f"\n  [{i+1}/{len(experiments)}] {rule_id} / {mechanism}")
        
        receipt = execute_actor_matrix_experiment(exp, seed)
        receipts.append(receipt)
        
        verdict = receipt["verdict"]
        bug = receipt["bug_detected"]
        icon = "[BUG]" if bug else ("[OK]" if verdict == "PROPERTY_HELD" else "[--]")
        print(f"    {icon} Verdict: {verdict}")
        if bug:
            bugs_found.append(receipt)
            print(f"    Violation: {receipt['details'].get('violation', 'N/A')}")
    
    # Summary
    print(f"\n{'='*70}")
    print(f"  RESULTS SUMMARY")
    print(f"{'='*70}")
    print(f"  Total experiments: {len(receipts)}")
    print(f"  Bugs detected: {len(bugs_found)}")
    print(f"  Property held: {sum(1 for r in receipts if r['verdict'] == 'PROPERTY_HELD')}")
    print(f"  Property violated: {sum(1 for r in receipts if r['verdict'] == 'PROPERTY_VIOLATED')}")
    print(f"  Blocked/Indeterminate: {sum(1 for r in receipts if r['verdict'] not in ('PROPERTY_HELD', 'PROPERTY_VIOLATED'))}")
    
    if bugs_found:
        print(f"\n  BUGS FOUND:")
        for b in bugs_found:
            print(f"    - {b['rule_id']}: {b['details'].get('violation', 'N/A')}")
    
    # Save results
    output = {
        "run_id": RUN_ID,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "target": BASE_URL,
        "max_experiments": MAX_EXPERIMENTS,
        "planned_count": plan_result["planned_count"],
        "executed_count": len(receipts),
        "bugs_detected": len(bugs_found),
        "receipts": receipts,
        "actor_matrix_summary": {
            exp["experiment_id"]: {
                "status": exp.get("actor_matrix_result", {}).get("status") if isinstance(exp.get("actor_matrix_result"), dict) else "N/A",
                "candidates": len(exp.get("actor_matrix_result", {}).get("candidates", [])) if isinstance(exp.get("actor_matrix_result"), dict) else 0,
                "pairs": len(exp.get("actor_matrix_result", {}).get("discriminating_pairs", [])) if isinstance(exp.get("actor_matrix_result"), dict) else 0,
            }
            for exp in experiments
        },
    }
    
    output_path = Path(__file__).parent / "actor_matrix_small_scale_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  Results saved to: {output_path}")
    
    # Success criteria: At least 4 of 6 target bugs detected
    # Map invariant IDs to rule IDs
    inv_to_rule = {
        "inv_tenant_001": "BR-TENANT-001",
        "inv_auth_014": "BR-AUTH-014",
        "inv_auth_012": "BR-AUTH-012",
        "inv_tenant_006": "BR-TENANT-006",
        "inv_auth_011": "BR-AUTH-011",
        "inv_auth_015": "BR-AUTH-015",
    }
    target_rules = set(inv_to_rule.values())
    detected_rules = {inv_to_rule.get(b["rule_id"], b["rule_id"]) for b in bugs_found}
    detected_count = len(detected_rules & target_rules)
        
    print(f"\n  Target bugs detected: {detected_count}/6")
    print(f"  Detected rules: {sorted(detected_rules)}")
        
    if detected_count >= 4:
        print(f"\n  [PASS] SMALL SCALE PASSED (>=4/6 targets detected)")
        return True
    else:
        print(f"\n  [FAIL] SMALL SCALE FAILED (<4/6 targets detected)")
        return False


if __name__ == "__main__":
    success = run_small_scale()
    sys.exit(0 if success else 1)

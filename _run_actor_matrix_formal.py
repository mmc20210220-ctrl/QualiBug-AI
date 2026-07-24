"""Actor Matrix Expansion — Formal Run Execution.

PROJECT_D_ACTOR_MATRIX_FORMAL_V1
Target rules: 6 (BR-TENANT-001, BR-AUTH-014, BR-AUTH-012, BR-TENANT-006, BR-AUTH-011, BR-AUTH-015)
Experiment limit: <=80

Formal validation of actor_matrix_planning module against Project D mock server.
Expanded coverage with multiple actor pairs per rule.
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
MAX_EXPERIMENTS = 80
RUN_ID = "PROJECT_D_ACTOR_MATRIX_FORMAL_V1"

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

ACTOR_TOKEN_MAP = {
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

# ─── HTTP Client ───────────────────────────────────────────────────────────────

def _http(method: str, path: str, token: str = "", body: dict | None = None) -> dict:
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
    return {
        "actors": [
            {"id": "actor_alice", "role": "customer", "tenant": "acme", "credential_secret_ref": "secret_ref:test_accounts:alice", "status": "active"},
            {"id": "actor_bob", "role": "customer", "tenant": "acme", "credential_secret_ref": "secret_ref:test_accounts:bob", "status": "active"},
            {"id": "actor_dave", "role": "agent", "tenant": "acme", "credential_secret_ref": "secret_ref:test_accounts:dave", "status": "active"},
            {"id": "actor_eve", "role": "agent", "tenant": "acme", "credential_secret_ref": "secret_ref:test_accounts:eve", "status": "active"},
            {"id": "actor_grace", "role": "supervisor", "tenant": "acme", "credential_secret_ref": "secret_ref:test_accounts:grace", "status": "active"},
            {"id": "actor_ivan", "role": "admin", "tenant": "acme", "credential_secret_ref": "secret_ref:test_accounts:ivan", "status": "active"},
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
    return [
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
    seed = {
        "acme_tickets": [],
        "globex_tickets": [],
        "acme_customers": [],
        "globex_customers": [],
    }
    
    r = _get("/tickets", TOKENS["ivan"])
    if r["status_code"] == 200:
        for t in r["body"].get("tickets", []):
            if t.get("tenant") == "acme":
                seed["acme_tickets"].append(t)
            else:
                seed["globex_tickets"].append(t)
    
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
    mechanism = exp.get("mechanism", "")
    rule_id = exp.get("rule_id", "")
    obligation_id = exp.get("obligation_id", "")
    actor_matrix = exp.get("actor_matrix_result", {})
    
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
    
    if "TENANT_ISOLATION" in mechanism:
        _exec_tenant_isolation(receipt, exp, seed)
    elif "AUTHORIZATION" in mechanism:
        _exec_authorization(receipt, exp, seed)
    else:
        receipt["verdict"] = "MECHANISM_NOT_EXECUTABLE"
    
    return receipt


def _exec_tenant_isolation(receipt: dict, exp: dict, seed: dict):
    treatment_plan = exp.get("treatment_plan", [])
    obligation_id = exp.get("obligation_id", "")
    
    if "tenant_001" in obligation_id:
        target_ticket = seed["acme_tickets"][0] if seed["acme_tickets"] else None
        if not target_ticket:
            receipt["verdict"] = "BLOCKED_NO_SEED"
            return
        
        ticket_id = target_ticket["id"]
        
        # Control: ACME user reads ACME ticket
        r = _get(f"/tickets/{ticket_id}", TOKENS["alice"])
        receipt["control_result"] = {
            "actor": "alice", "tenant": "acme",
            "status_code": r["status_code"], "expected": 200,
            "passed": r["status_code"] == 200,
        }
        
        # Treatment: Cross-tenant actors
        for step in treatment_plan:
            mutation = step.get("mutation", {})
            actor_ref = mutation.get("actor_ref", "")
            token = ACTOR_TOKEN_MAP.get(actor_ref, "")
            if not token:
                continue
            
            r = _get(f"/tickets/{ticket_id}", token)
            bug = r["status_code"] == 200
            receipt["treatment_results"].append({
                "step_id": step.get("step_id"),
                "actor_ref": actor_ref,
                "dimension": mutation.get("dimension_under_test", ""),
                "status_code": r["status_code"],
                "expected": 403,
                "bug_detected": bug,
            })
            
            if bug:
                receipt["bug_detected"] = True
                receipt["verdict"] = "PROPERTY_VIOLATED"
                receipt["details"]["violation"] = f"Cross-tenant read: {actor_ref}"
        
        if not receipt["bug_detected"] and receipt["treatment_results"]:
            receipt["verdict"] = "PROPERTY_HELD"
    
    elif "tenant_006" in obligation_id:
        # Control: ACME admin lists customers
        r = _get("/customers", TOKENS["ivan"])
        customers = r["body"].get("customers", [])
        globex_leaked = len([c for c in customers if c.get("tenant") == "globex"])
        
        receipt["control_result"] = {
            "actor": "ivan", "tenant": "acme",
            "status_code": r["status_code"],
            "total": len(customers),
            "cross_tenant_leaked": globex_leaked,
        }
        
        for step in treatment_plan:
            mutation = step.get("mutation", {})
            actor_ref = mutation.get("actor_ref", "")
            token = ACTOR_TOKEN_MAP.get(actor_ref, "")
            if not token:
                continue
            
            r = _get("/customers", token)
            customers = r["body"].get("customers", [])
            cross_tenant = [c for c in customers if c.get("tenant") != "acme"]
            bug = len(cross_tenant) > 0
            
            receipt["treatment_results"].append({
                "step_id": step.get("step_id"),
                "actor_ref": actor_ref,
                "dimension": mutation.get("dimension_under_test", ""),
                "status_code": r["status_code"],
                "cross_tenant_leaked": len(cross_tenant),
                "bug_detected": bug,
            })
            
            if bug:
                receipt["bug_detected"] = True
                receipt["verdict"] = "PROPERTY_VIOLATED"
                receipt["details"]["violation"] = f"Cross-tenant list leak: {len(cross_tenant)}"
        
        if not receipt["bug_detected"] and receipt["treatment_results"]:
            receipt["verdict"] = "PROPERTY_HELD"


def _exec_authorization(receipt: dict, exp: dict, seed: dict):
    treatment_plan = exp.get("treatment_plan", [])
    obligation_id = exp.get("obligation_id", "")
    
    if "auth_014" in obligation_id:
        target_ticket = seed["acme_tickets"][0] if seed["acme_tickets"] else None
        if not target_ticket:
            receipt["verdict"] = "BLOCKED_NO_SEED"
            return
        
        ticket_id = target_ticket["id"]
        owner_id = target_ticket.get("customer_id")
        
        # Control: Owner reopens
        control_token = TOKENS["alice"] if owner_id == "cust-001" else TOKENS["bob"]
        r = _post(f"/tickets/{ticket_id}/reopen", control_token, {"reason": "Owner reopen"})
        receipt["control_result"] = {"actor": "owner", "status_code": r["status_code"]}
        
        for step in treatment_plan:
            mutation = step.get("mutation", {})
            actor_ref = mutation.get("actor_ref", "")
            token = ACTOR_TOKEN_MAP.get(actor_ref, "")
            if not token:
                continue
            
            # Skip owner
            if (actor_ref == "actor_alice" and owner_id == "cust-001") or \
               (actor_ref == "actor_bob" and owner_id == "cust-002"):
                continue
            
            r = _post(f"/tickets/{ticket_id}/reopen", token, {"reason": "Non-owner"})
            bug = r["status_code"] == 200
            
            receipt["treatment_results"].append({
                "step_id": step.get("step_id"),
                "actor_ref": actor_ref,
                "dimension": mutation.get("dimension_under_test", ""),
                "status_code": r["status_code"],
                "bug_detected": bug,
            })
            
            if bug:
                receipt["bug_detected"] = True
                receipt["verdict"] = "PROPERTY_VIOLATED"
                receipt["details"]["violation"] = f"Non-owner reopen: {actor_ref}"
        
        if not receipt["bug_detected"] and receipt["treatment_results"]:
            receipt["verdict"] = "PROPERTY_HELD"
    
    elif "auth_012" in obligation_id:
        target_ticket = seed["acme_tickets"][0] if seed["acme_tickets"] else None
        if not target_ticket:
            receipt["verdict"] = "BLOCKED_NO_SEED"
            return
        
        ticket_id = target_ticket["id"]
        
        r = _put(f"/tickets/{ticket_id}", TOKENS["alice"], {"title": "Owner update"})
        receipt["control_result"] = {"actor": "alice", "status_code": r["status_code"]}
        
        for step in treatment_plan:
            mutation = step.get("mutation", {})
            actor_ref = mutation.get("actor_ref", "")
            token = ACTOR_TOKEN_MAP.get(actor_ref, "")
            if not token or actor_ref in ("actor_alice", "actor_bob"):
                continue
            
            r = _put(f"/tickets/{ticket_id}", token, {"title": "Non-owner"})
            bug = r["status_code"] == 200
            
            receipt["treatment_results"].append({
                "step_id": step.get("step_id"),
                "actor_ref": actor_ref,
                "dimension": mutation.get("dimension_under_test", ""),
                "status_code": r["status_code"],
                "bug_detected": bug,
            })
            
            if bug:
                receipt["bug_detected"] = True
                receipt["verdict"] = "PROPERTY_VIOLATED"
                receipt["details"]["violation"] = f"Non-owner update: {actor_ref}"
        
        if not receipt["bug_detected"] and receipt["treatment_results"]:
            receipt["verdict"] = "PROPERTY_HELD"
    
    elif "auth_011" in obligation_id:
        target_customer = seed["acme_customers"][0] if seed["acme_customers"] else None
        if not target_customer:
            receipt["verdict"] = "BLOCKED_NO_SEED"
            return
        
        customer_id = target_customer["id"]
        
        r = _put(f"/customers/{customer_id}", TOKENS["ivan"], {"name": "Admin update"})
        receipt["control_result"] = {"actor": "ivan", "status_code": r["status_code"]}
        
        for step in treatment_plan:
            mutation = step.get("mutation", {})
            actor_ref = mutation.get("actor_ref", "")
            token = ACTOR_TOKEN_MAP.get(actor_ref, "")
            if not token or actor_ref in ("actor_ivan", "actor_judy"):
                continue
            
            r = _put(f"/customers/{customer_id}", token, {"name": "Non-admin"})
            bug = r["status_code"] == 200
            
            receipt["treatment_results"].append({
                "step_id": step.get("step_id"),
                "actor_ref": actor_ref,
                "dimension": mutation.get("dimension_under_test", ""),
                "status_code": r["status_code"],
                "bug_detected": bug,
            })
            
            if bug:
                receipt["bug_detected"] = True
                receipt["verdict"] = "PROPERTY_VIOLATED"
                receipt["details"]["violation"] = f"Non-admin update: {actor_ref}"
        
        if not receipt["bug_detected"] and receipt["treatment_results"]:
            receipt["verdict"] = "PROPERTY_HELD"
    
    elif "auth_015" in obligation_id:
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
        
        # Control: Uploader deletes
        r2 = _post(f"/tickets/{ticket_id}/attachments", TOKENS["alice"], {"filename": "ctrl.txt", "size": 100})
        if r2["status_code"] == 201:
            ctrl_id = r2["body"].get("id")
            r = _delete(f"/tickets/{ticket_id}/attachments/{ctrl_id}", TOKENS["alice"])
            receipt["control_result"] = {"actor": "alice", "status_code": r["status_code"]}
        
        for step in treatment_plan:
            mutation = step.get("mutation", {})
            actor_ref = mutation.get("actor_ref", "")
            token = ACTOR_TOKEN_MAP.get(actor_ref, "")
            if not token or actor_ref == "actor_alice":
                continue
            
            r = _delete(f"/tickets/{ticket_id}/attachments/{attachment_id}", token)
            bug = r["status_code"] == 200
            
            receipt["treatment_results"].append({
                "step_id": step.get("step_id"),
                "actor_ref": actor_ref,
                "dimension": mutation.get("dimension_under_test", ""),
                "status_code": r["status_code"],
                "bug_detected": bug,
            })
            
            if bug:
                receipt["bug_detected"] = True
                receipt["verdict"] = "PROPERTY_VIOLATED"
                receipt["details"]["violation"] = f"Non-uploader delete: {actor_ref}"
        
        if not receipt["bug_detected"] and receipt["treatment_results"]:
            receipt["verdict"] = "PROPERTY_HELD"


# ─── Main ──────────────────────────────────────────────────────────────────────

def run_formal():
    print(f"{'='*70}")
    print(f"  ACTOR MATRIX EXPANSION - FORMAL RUN")
    print(f"  Run ID: {RUN_ID}")
    print(f"  Max Experiments: {MAX_EXPERIMENTS}")
    print(f"  Target: {BASE_URL}")
    print(f"{'='*70}")
    
    # 1. Health check
    print("\n[1/5] Health check...")
    r = _get("/health")
    if r["status_code"] != 200:
        print(f"  FATAL: Server not reachable")
        sys.exit(1)
    print(f"  Server OK")
    
    # 2. Build inputs
    print("\n[2/5] Building Behavior IR and Obligations...")
    behavior_ir = build_behavior_ir()
    obligations = build_obligations()
    print(f"  Actors: {len(behavior_ir['actors'])}, Operations: {len(behavior_ir['operations'])}, Obligations: {len(obligations)}")
    
    # 3. Run Planner
    print("\n[3/5] Running Deep Experiment Planner...")
    plan_result = plan_deep_experiments(
        obligations=obligations,
        experiments_by_obligation={},
        behavior_ir=behavior_ir,
        budget=MAX_EXPERIMENTS,
    )
    experiments = plan_result["deep_experiments"]
    print(f"  Planned: {plan_result['planned_count']}, Skipped: {plan_result['skipped_count']}")
    print(f"  Mechanisms: {json.dumps(plan_result['mechanism_counts'])}")
    
    # Actor matrix summary
    total_pairs = 0
    for exp in experiments:
        am = exp.get("actor_matrix_result", {})
        if isinstance(am, dict) and am.get("status") == "COMPLETE":
            pairs = len(am.get("discriminating_pairs", []))
            total_pairs += pairs
            print(f"  {exp['rule_id']}: {pairs} discriminating pairs")
    
    print(f"  Total discriminating pairs: {total_pairs}")
    
    # 4. Discover seed data
    print("\n[4/5] Discovering seed data...")
    seed = discover_seed_data()
    print(f"  ACME tickets: {len(seed['acme_tickets'])}, Globex tickets: {len(seed['globex_tickets'])}")
    
    # 5. Execute experiments
    print(f"\n[5/5] Executing {len(experiments)} experiments...")
    receipts = []
    bugs_found = []
    
    for i, exp in enumerate(experiments[:MAX_EXPERIMENTS]):
        receipt = execute_actor_matrix_experiment(exp, seed)
        receipts.append(receipt)
        
        icon = "[BUG]" if receipt["bug_detected"] else ("[OK]" if receipt["verdict"] == "PROPERTY_HELD" else "[--]")
        print(f"  [{i+1}/{len(experiments)}] {exp['rule_id']}: {icon} {receipt['verdict']}")
        
        if receipt["bug_detected"]:
            bugs_found.append(receipt)
    
    # Summary
    print(f"\n{'='*70}")
    print(f"  FORMAL RUN RESULTS")
    print(f"{'='*70}")
    print(f"  Total experiments: {len(receipts)}")
    print(f"  Bugs detected: {len(bugs_found)}")
    print(f"  Property held: {sum(1 for r in receipts if r['verdict'] == 'PROPERTY_HELD')}")
    print(f"  Property violated: {sum(1 for r in receipts if r['verdict'] == 'PROPERTY_VIOLATED')}")
    
    # Map to rule IDs
    inv_to_rule = {
        "inv_tenant_001": "BR-TENANT-001",
        "inv_auth_014": "BR-AUTH-014",
        "inv_auth_012": "BR-AUTH-012",
        "inv_tenant_006": "BR-TENANT-006",
        "inv_auth_011": "BR-AUTH-011",
        "inv_auth_015": "BR-AUTH-015",
    }
    detected_rules = {inv_to_rule.get(b["rule_id"], b["rule_id"]) for b in bugs_found}
    print(f"  Unique rules violated: {len(detected_rules)}/6")
    print(f"  Rules: {sorted(detected_rules)}")
    
    # Save results
    output = {
        "run_id": RUN_ID,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "planned_count": plan_result["planned_count"],
        "executed_count": len(receipts),
        "bugs_detected": len(bugs_found),
        "unique_rules_violated": len(detected_rules),
        "receipts": receipts,
    }
    
    output_path = Path(__file__).parent / "actor_matrix_formal_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  Results saved to: {output_path}")
    
    # Success: all 6 rules detected
    if len(detected_rules) >= 6:
        print(f"\n  [PASS] FORMAL RUN PASSED (6/6 rules detected)")
        return True
    else:
        print(f"\n  [FAIL] FORMAL RUN FAILED ({len(detected_rules)}/6 rules)")
        return False


if __name__ == "__main__":
    success = run_formal()
    sys.exit(0 if success else 1)

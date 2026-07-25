"""MES Invariant Oracles - evaluate real responses against documented constraints.

Oracle judgment is based solely on API_SPEC.md and BUSINESS_RULES.md documented
constraints. No benchmark answers or source code comments are referenced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .mes_client import HttpEvidence


@dataclass
class OracleResult:
    """Result of an Oracle evaluation."""
    oracle_type: str
    verdict: str  # "PASS" or "VIOLATION"
    constraint_ref: str  # Documented constraint reference
    expected_behavior: str
    actual_behavior: str
    evidence_summary: str

    def to_dict(self) -> dict:
        return {
            "oracle_type": self.oracle_type,
            "verdict": self.verdict,
            "constraint_ref": self.constraint_ref,
            "expected_behavior": self.expected_behavior,
            "actual_behavior": self.actual_behavior,
            "evidence_summary": self.evidence_summary,
        }


def evaluate_role_oracle(
    action_ev: HttpEvidence,
    forbidden_roles: list[str],
    actor_role: str,
    resource: str,
    operation: str,
) -> OracleResult:
    """ROLE_ORACLE: Operation should be restricted by role.
    
    API_SPEC documents which roles can perform each operation.
    If a forbidden role succeeds (2xx), it's a violation.
    """
    success = 200 <= action_ev.status_code < 300
    if success and actor_role in forbidden_roles:
        return OracleResult(
            oracle_type="ROLE_ORACLE",
            verdict="VIOLATION",
            constraint_ref=f"API_SPEC: {operation} on {resource} restricted to authorized roles only",
            expected_behavior=f"Role {actor_role} should receive 403 Forbidden for {operation}",
            actual_behavior=f"Role {actor_role} received {action_ev.status_code} (success)",
            evidence_summary=f"{action_ev.method} {action_ev.url} as {actor_role} -> {action_ev.status_code}",
        )
    return OracleResult(
        oracle_type="ROLE_ORACLE",
        verdict="PASS",
        constraint_ref=f"API_SPEC: {operation} on {resource} role restriction",
        expected_behavior=f"Role {actor_role} correctly handled",
        actual_behavior=f"Status {action_ev.status_code}",
        evidence_summary=f"{action_ev.method} {action_ev.url} -> {action_ev.status_code}",
    )


def evaluate_scope_oracle(
    list_ev: HttpEvidence,
    actor_org: str,
    resource: str,
) -> OracleResult:
    """SCOPE_ORACLE: List endpoints should filter by organization.
    
    Multi-tenant system: each org should only see its own data.
    If response contains other org's data, it's a violation.
    """
    if list_ev.status_code != 200:
        return OracleResult(
            oracle_type="SCOPE_ORACLE", verdict="PASS",
            constraint_ref=f"API_SPEC: {resource} org-scoped listing",
            expected_behavior="N/A (non-200)", actual_behavior=f"Status {list_ev.status_code}",
            evidence_summary="Non-200, cannot evaluate scope",
        )

    body = list_ev.response_body
    # Find the list in response (various key names)
    items = []
    for key in ("work_centers", "products", "sales_orders", "inspections",
                "work_orders", "boms", "routings", "production_plans"):
        if key in body:
            items = body[key]
            break

    other_org_items = [it for it in items if it.get("org") and it["org"] != actor_org]
    if other_org_items:
        return OracleResult(
            oracle_type="SCOPE_ORACLE",
            verdict="VIOLATION",
            constraint_ref=f"API_SPEC: {resource} listing must be org-scoped (multi-tenant isolation)",
            expected_behavior=f"Actor from '{actor_org}' should only see {actor_org} records",
            actual_behavior=f"Response contains {len(other_org_items)} records from other orgs",
            evidence_summary=f"GET {list_ev.url} as {actor_org} -> found orgs: {set(it.get('org') for it in items)}",
        )
    return OracleResult(
        oracle_type="SCOPE_ORACLE", verdict="PASS",
        constraint_ref=f"API_SPEC: {resource} org-scoped listing",
        expected_behavior=f"Only {actor_org} records visible",
        actual_behavior=f"All {len(items)} records belong to {actor_org}",
        evidence_summary=f"GET {list_ev.url} -> {len(items)} items, all org={actor_org}",
    )


def evaluate_state_oracle(
    action_ev: HttpEvidence,
    entity_type: str,
    entity_id: str,
    current_state: str,
    attempted_transition: str,
    allowed_from: list[str],
) -> OracleResult:
    """STATE_ORACLE: State transitions must follow documented lifecycle.
    
    If a transition succeeds from a state not in allowed_from, it's a violation.
    """
    success = 200 <= action_ev.status_code < 300
    if success and current_state not in allowed_from:
        return OracleResult(
            oracle_type="STATE_ORACLE",
            verdict="VIOLATION",
            constraint_ref=f"API_SPEC: {entity_type} transition '{attempted_transition}' only from {allowed_from}",
            expected_behavior=f"Cannot {attempted_transition} from state {current_state} (expect 409)",
            actual_behavior=f"Transition succeeded with status {action_ev.status_code}",
            evidence_summary=f"{entity_type}/{entity_id}: {current_state} --{attempted_transition}--> SUCCESS",
        )
    return OracleResult(
        oracle_type="STATE_ORACLE", verdict="PASS",
        constraint_ref=f"API_SPEC: {entity_type} state machine",
        expected_behavior=f"Transition from {current_state} correctly handled",
        actual_behavior=f"Status {action_ev.status_code}",
        evidence_summary=f"{entity_type}/{entity_id} {attempted_transition} -> {action_ev.status_code}",
    )


def evaluate_conservation_oracle(
    action_ev: HttpEvidence,
    resource: str,
    constraint_desc: str,
    violated: bool,
    detail: str = "",
) -> OracleResult:
    """CONSERVATION_ORACLE: Quantity/amount conservation must hold.
    
    E.g., issue qty <= reserved qty, pass+fail == sample_size.
    """
    if violated:
        return OracleResult(
            oracle_type="CONSERVATION_ORACLE",
            verdict="VIOLATION",
            constraint_ref=f"API_SPEC/BUSINESS_RULES: {constraint_desc}",
            expected_behavior=constraint_desc,
            actual_behavior=detail or f"Conservation violated (status {action_ev.status_code})",
            evidence_summary=f"{action_ev.method} {action_ev.url} -> {action_ev.status_code}: {detail}",
        )
    return OracleResult(
        oracle_type="CONSERVATION_ORACLE", verdict="PASS",
        constraint_ref=f"BUSINESS_RULES: {constraint_desc}",
        expected_behavior=constraint_desc,
        actual_behavior="Conservation holds",
        evidence_summary=f"{action_ev.method} {action_ev.url} -> OK",
    )


def evaluate_idempotency_oracle(
    first_ev: HttpEvidence,
    second_ev: HttpEvidence,
    resource: str,
    idempotency_key: str,
) -> OracleResult:
    """IDEMPOTENCY_ORACLE: Duplicate creation with same key should be rejected (409).
    
    API_SPEC documents idempotency keys (e.g., order_ref for sales orders).
    """
    second_success = 200 <= second_ev.status_code < 300
    if second_success:
        return OracleResult(
            oracle_type="IDEMPOTENCY_ORACLE",
            verdict="VIOLATION",
            constraint_ref=f"API_SPEC: {resource} idempotency on {idempotency_key} (duplicate returns 409)",
            expected_behavior=f"Duplicate {idempotency_key} should return 409 Conflict",
            actual_behavior=f"Duplicate accepted with status {second_ev.status_code}",
            evidence_summary=f"First: {first_ev.status_code}, Duplicate: {second_ev.status_code} (should be 409)",
        )
    return OracleResult(
        oracle_type="IDEMPOTENCY_ORACLE", verdict="PASS",
        constraint_ref=f"API_SPEC: {resource} idempotency",
        expected_behavior="Duplicate rejected",
        actual_behavior=f"Status {second_ev.status_code}",
        evidence_summary=f"Duplicate {idempotency_key} -> {second_ev.status_code}",
    )


def evaluate_compensation_oracle(
    trigger_ev: HttpEvidence,
    observe_ev: HttpEvidence,
    resource: str,
    compensation_desc: str,
    compensation_occurred: bool,
) -> OracleResult:
    """COMPENSATION_ORACLE: Cancel/delete must trigger documented compensation.
    
    E.g., cancel work order must release material reservations.
    """
    if not compensation_occurred:
        return OracleResult(
            oracle_type="COMPENSATION_ORACLE",
            verdict="VIOLATION",
            constraint_ref=f"API_SPEC: {compensation_desc}",
            expected_behavior=compensation_desc,
            actual_behavior="Compensation did NOT occur",
            evidence_summary=f"Trigger: {trigger_ev.status_code}, Observe: {observe_ev.response_body}",
        )
    return OracleResult(
        oracle_type="COMPENSATION_ORACLE", verdict="PASS",
        constraint_ref=f"API_SPEC: {compensation_desc}",
        expected_behavior=compensation_desc,
        actual_behavior="Compensation occurred correctly",
        evidence_summary=f"Trigger: {trigger_ev.status_code}, compensation verified",
    )


def evaluate_temporal_oracle(
    action_ev: HttpEvidence,
    resource: str,
    constraint_desc: str,
    violated: bool,
    detail: str = "",
) -> OracleResult:
    """TEMPORAL_ORACLE: Time-based constraints must be enforced.
    
    E.g., planned_start < planned_end, submission before expiry.
    """
    if violated:
        return OracleResult(
            oracle_type="TEMPORAL_ORACLE",
            verdict="VIOLATION",
            constraint_ref=f"API_SPEC/BUSINESS_RULES: {constraint_desc}",
            expected_behavior=constraint_desc,
            actual_behavior=detail or f"Temporal constraint violated (status {action_ev.status_code})",
            evidence_summary=f"{action_ev.method} {action_ev.url} -> {action_ev.status_code}: {detail}",
        )
    return OracleResult(
        oracle_type="TEMPORAL_ORACLE", verdict="PASS",
        constraint_ref=f"BUSINESS_RULES: {constraint_desc}",
        expected_behavior=constraint_desc,
        actual_behavior="Temporal constraint enforced",
        evidence_summary=f"{action_ev.method} {action_ev.url} -> OK",
    )


def evaluate_cross_entity_oracle(
    action_ev: HttpEvidence,
    resource: str,
    precondition_desc: str,
    violated: bool,
    detail: str = "",
) -> OracleResult:
    """CROSS_ENTITY_ORACLE: Cross-entity preconditions must be validated.
    
    E.g., work order requires active BOM+Routing, receipt requires COMPLETED WO.
    """
    if violated:
        return OracleResult(
            oracle_type="CROSS_ENTITY_ORACLE",
            verdict="VIOLATION",
            constraint_ref=f"API_SPEC: {precondition_desc}",
            expected_behavior=precondition_desc,
            actual_behavior=detail or f"Precondition not validated (status {action_ev.status_code})",
            evidence_summary=f"{action_ev.method} {action_ev.url} -> {action_ev.status_code}: {detail}",
        )
    return OracleResult(
        oracle_type="CROSS_ENTITY_ORACLE", verdict="PASS",
        constraint_ref=f"API_SPEC: {precondition_desc}",
        expected_behavior=precondition_desc,
        actual_behavior="Precondition validated correctly",
        evidence_summary=f"{action_ev.method} {action_ev.url} -> OK",
    )


def evaluate_concurrency_oracle(
    action_ev: HttpEvidence,
    resource: str,
    entity_id: str,
    stale_version: int,
) -> OracleResult:
    """CONCURRENCY_ORACLE: Optimistic locking must reject stale versions (409).
    
    API_SPEC: PUT with version field for optimistic locking.
    """
    success = 200 <= action_ev.status_code < 300
    if success:
        return OracleResult(
            oracle_type="CONCURRENCY_ORACLE",
            verdict="VIOLATION",
            constraint_ref=f"API_SPEC: {resource} requires version check (optimistic locking, 409 on mismatch)",
            expected_behavior=f"Update with stale version {stale_version} should return 409",
            actual_behavior=f"Update succeeded with status {action_ev.status_code}",
            evidence_summary=f"PUT {resource}/{entity_id} version={stale_version} -> {action_ev.status_code}",
        )
    return OracleResult(
        oracle_type="CONCURRENCY_ORACLE", verdict="PASS",
        constraint_ref=f"API_SPEC: {resource} optimistic locking",
        expected_behavior="Stale version rejected",
        actual_behavior=f"Status {action_ev.status_code}",
        evidence_summary=f"PUT {resource}/{entity_id} stale version -> {action_ev.status_code}",
    )


def evaluate_batch_oracle(
    batch_ev: HttpEvidence,
    resource: str,
    semantics: str,
    partial_success: bool,
    detail: str = "",
) -> OracleResult:
    """BATCH_ORACLE: Batch operations must be atomic (all-or-nothing).
    
    API_SPEC: bulk-release is all-or-nothing, bulk-issue is atomic.
    """
    if partial_success:
        return OracleResult(
            oracle_type="BATCH_ORACLE",
            verdict="VIOLATION",
            constraint_ref=f"API_SPEC: {resource} batch semantics: {semantics}",
            expected_behavior=f"Batch should be atomic ({semantics})",
            actual_behavior=detail or "Partial success detected (some items succeeded, some failed)",
            evidence_summary=f"POST {batch_ev.url} -> {batch_ev.status_code}: {detail}",
        )
    return OracleResult(
        oracle_type="BATCH_ORACLE", verdict="PASS",
        constraint_ref=f"API_SPEC: {resource} batch atomicity",
        expected_behavior=semantics,
        actual_behavior="Batch atomicity maintained",
        evidence_summary=f"POST {batch_ev.url} -> {batch_ev.status_code}: atomic",
    )

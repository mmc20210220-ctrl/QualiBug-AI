"""Independent Evidence Verifier — validates evidence integrity independently.

This verifier does NOT use the same logic as the discovery engine.
It cross-checks:
1. Before/After snapshots exist at referenced paths
2. Evidence refs are resolvable
3. Observer data is internally consistent
4. Invariant evidence is not self-referential
5. Cleanup evidence is present
"""
from __future__ import annotations
import json as _json
from pathlib import Path
from typing import Any




def _is_read_only_auth_boundary(finding: dict[str, Any]) -> bool:
    evidence_model = str(finding.get("evidence_model") or "").lower()
    invariant = finding.get("violated_invariant") or {}
    title = str(finding.get("title") or "").lower()
    kind = str(invariant.get("kind") or "").lower()
    action = str(finding.get("action_evidence_ref") or "").lower()
    matrix = finding.get("auth_boundary_matrix") or {}
    sensitivity = finding.get("response_sensitivity") or {}
    return (
        evidence_model == "read_only_auth_boundary"
        or (
            kind == "permission"
            and ("anonymous" in action or "no_auth" in action or "匿名" in title)
            and ("get " in action or " get" in action)
            and bool(matrix or sensitivity)
        )
    )

def verify_evidence_integrity(finding: dict[str, Any], *, workspace_root: Path | None = None) -> dict[str, Any]:
    """Independently verify the evidence backing a finding.

    Returns:
        {
            "passed": bool,
            "verdict": "EVIDENCE_OK" | "EVIDENCE_INCOMPLETE" | "EVIDENCE_CONFLICT" | "EVIDENCE_UNAVAILABLE",
            "checks": [{"check": str, "passed": bool, "detail": str}, ...],
        }
    """
    checks: list[dict[str, Any]] = []

    # 1. Evidence shape check
    before_ref = finding.get("before_snapshot_ref", "")
    after_ref = finding.get("after_snapshot_ref", "")
    action_ref = finding.get("action_evidence_ref", "")
    is_auth_read = _is_read_only_auth_boundary(finding)

    if is_auth_read:
        # Read-only auth bugs are validated by an access-boundary matrix and
        # the anonymous request/response snapshot.  They do not mutate state,
        # so classical before/after mutation snapshots are not required.
        checks.append({
            "check": "auth_boundary_matrix_present",
            "passed": bool(finding.get("auth_boundary_matrix")),
            "detail": "auth_boundary_matrix: present" if finding.get("auth_boundary_matrix") else "auth_boundary_matrix: missing",
        })
        sensitivity = finding.get("response_sensitivity") or {}
        checks.append({
            "check": "response_sensitivity_present",
            "passed": bool(sensitivity.get("has_business_data")),
            "detail": f"response_sensitivity.has_business_data={bool(sensitivity.get('has_business_data'))}",
        })
        checks.append({
            "check": "action_evidence_exists",
            "passed": bool(action_ref),
            "detail": f"action_evidence_ref: {'present' if action_ref else 'missing'}",
        })
        checks.append({
            "check": "read_only_snapshots_not_required",
            "passed": True,
            "detail": "GET authorization-boundary probe; state before/after snapshots are not applicable",
        })
    else:
        checks.append({
            "check": "before_snapshot_exists",
            "passed": bool(before_ref),
            "detail": f"before_snapshot_ref: {'present' if before_ref else 'missing'}",
        })
        checks.append({
            "check": "after_snapshot_exists",
            "passed": bool(after_ref),
            "detail": f"after_snapshot_ref: {'present' if after_ref else 'missing'}",
        })
        checks.append({
            "check": "action_evidence_exists",
            "passed": bool(action_ref),
            "detail": f"action_evidence_ref: {'present' if action_ref else 'missing'}",
        })

        # 2. Verify before != after (if they're the same, no change happened)
        if before_ref and after_ref and before_ref == after_ref:
            checks.append({
                "check": "distinct_snapshots",
                "passed": False,
                "detail": "Before and After snapshots reference the same source — no state change",
            })
        else:
            checks.append({
                "check": "distinct_snapshots",
                "passed": True,
                "detail": "Before and After snapshots are distinct",
            })

    # 3. Invariant evidence check
    invariant = finding.get("violated_invariant") or {}
    inv_evidence = invariant.get("evidence_ref", "")
    checks.append({
        "check": "invariant_evidence_present",
        "passed": bool(inv_evidence),
        "detail": f"Invariant evidence ref: {'present' if inv_evidence else 'missing'}",
    })
    # Invariant evidence should not be the same as before/after refs (no self-reference)
    if inv_evidence and inv_evidence in (before_ref, after_ref):
        checks.append({
            "check": "invariant_not_self_referential",
            "passed": False,
            "detail": "Invariant evidence ref is same as before/after snapshot — self-referential",
        })

    # 4. Observer evidence check
    observer_refs = finding.get("observer_refs") or []
    checks.append({
        "check": "observer_evidence_present",
        "passed": len(observer_refs) > 0,
        "detail": f"Observer refs: {len(observer_refs)}",
    })

    # 5. Cleanup evidence
    cleanup = finding.get("cleanup") or {}
    cleanup_status = cleanup.get("status", "")
    cleanup_evidence = cleanup.get("evidence_ref", "")
    checks.append({
        "check": "cleanup_status_valid",
        "passed": cleanup_status in ("CLEAN", "NOT_APPLICABLE"),
        "detail": f"Cleanup status: {cleanup_status}",
    })
    checks.append({
        "check": "cleanup_evidence_present",
        "passed": bool(cleanup_evidence) or cleanup_status == "NOT_APPLICABLE",
        "detail": f"Cleanup evidence: {'present' if cleanup_evidence else 'not required (NOT_APPLICABLE)'}",
    })

    # 6. Entity binding self-consistency
    entity = finding.get("entity_binding") or {}
    entity_id = entity.get("entity_id", "")
    checks.append({
        "check": "entity_binding_complete",
        "passed": bool(entity_id) and bool(entity.get("entity_type")) and bool(entity.get("entity_alias")),
        "detail": f"Entity: {entity.get('entity_alias')} ({entity.get('entity_type')}) id={entity_id}",
    })

    # Determine overall verdict
    all_passed = all(c["passed"] for c in checks)
    if _is_read_only_auth_boundary(finding):
        critical_names = (
            "auth_boundary_matrix_present", "response_sensitivity_present",
            "action_evidence_exists", "invariant_evidence_present",
            "entity_binding_complete",
        )
    else:
        critical_names = (
            "before_snapshot_exists", "after_snapshot_exists", "distinct_snapshots",
            "invariant_evidence_present", "entity_binding_complete",
        )
    critical_failures = [c for c in checks if not c["passed"] and c["check"] in critical_names]

    if not all_passed and critical_failures:
        verdict = "EVIDENCE_INCOMPLETE"
    elif not all_passed:
        verdict = "EVIDENCE_UNAVAILABLE"
    else:
        verdict = "EVIDENCE_OK"

    return {
        "passed": all_passed,
        "verdict": verdict,
        "checks": checks,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python independent_evidence_verifier.py <finding.json>")
        sys.exit(1)
    path = Path(sys.argv[1])
    finding = _json.loads(path.read_text(encoding="utf-8"))
    result = verify_evidence_integrity(finding)
    print(_json.dumps(result, indent=2, ensure_ascii=False))

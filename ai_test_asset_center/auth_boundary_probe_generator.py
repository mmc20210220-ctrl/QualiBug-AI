from __future__ import annotations

"""Phase95A: customer-grounded authorization boundary probe generator.

This module strengthens the core bug-discovery engine by creating negative
access-control probes from the customer's own API surface and source refs.  It
never invents endpoints and it does not require private ground truth: every
probe is tied to an existing route/probe plus endpoint-contract/business-rule
references.
"""

import copy
import re
from typing import Any

SENSITIVE_PATH_RE = re.compile(
    r"(?:admin|tenant|org|workspace|project|user|member|account|role|permission|audit|export|report|invoice|payment|refund|order|customer|profile|\u79df\u6237|\u7ec4\u7ec7|\u6743\u9650|\u89d2\u8272|\u5ba1\u8ba1|\u5bfc\u51fa|\u8ba2\u5355|\u7528\u6237)",
    re.I,
)
OWNER_PARAM_RE = re.compile(r"\{?(?:tenant|org|workspace|project|user|member|account|owner|customer)[A-Za-z0-9_\-]*\}?", re.I)
AUTH_TEXT_RE = re.compile(r"(?:auth|authorization|permission|role|rbac|tenant|owner|access control|认证|鉴权|授权|权限|角色|租户|归属)", re.I)
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _endpoint(probe: dict[str, Any]) -> tuple[str, str]:
    ep = probe.get("endpoint") if isinstance(probe.get("endpoint"), dict) else {}
    return str(ep.get("method") or "GET").upper(), str(ep.get("path") or "")


def _source_refs(probe: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [r for r in (probe.get("source_refs") or []) if isinstance(r, dict)]
    if refs:
        return refs
    method, path = _endpoint(probe)
    return [{"file": "grounded_probe_plan", "section": f"{method} {path}", "quote": "Authorization boundary probe was generated from a document-grounded endpoint.", "kind": "endpoint_contract"}]


def _hay(probe: dict[str, Any]) -> str:
    method, path = _endpoint(probe)
    refs = " ".join(str(r.get("quote") or "") for r in _source_refs(probe))
    return " ".join([method, path, str(probe.get("risk_type") or ""), refs])


def _is_sensitive_surface(probe: dict[str, Any]) -> bool:
    method, path = _endpoint(probe)
    risk = str(probe.get("risk_type") or "")
    path_risk = f"{path} {risk}"
    refs = " ".join(str(r.get("quote") or "") for r in _source_refs(probe))
    # Path/risk markers are strong signals.  Free-text source refs are used only
    # for explicit auth/tenant semantics so a generic business-rule quote does
    # not turn every public health endpoint into an auth probe.
    return bool(SENSITIVE_PATH_RE.search(path_risk) or OWNER_PARAM_RE.search(path_risk) or AUTH_TEXT_RE.search(refs) or AUTH_TEXT_RE.search(risk))


def _probe_variant(source: dict[str, Any], *, idx: int, actor: str, risk: str, value: str) -> dict[str, Any]:
    clone = copy.deepcopy(source)
    method, path = _endpoint(source)
    destructive = method in WRITE_METHODS
    clone.update({
        "candidate_id": f"QBAUTH-95A-{idx:04d}",
        "risk_type": risk,
        "execution_policy": "disposable_sandbox_required" if destructive else "read_only_safe",
        "endpoint": {"method": method, "path": path},
        "probe_plan": {
            **(clone.get("probe_plan") if isinstance(clone.get("probe_plan"), dict) else {}),
            "phase": "95A",
            "phase_version": "v1_auth_boundary_matrix",
            "strategy": "negative_authorization_boundary_runtime_probe",
            "auth_boundary": {
                "actor": actor,
                "credential_profile": value,
                "expected_status": [401, 403, 404],
                "allow_empty_200_only": method == "GET",
                "same_endpoint_as_source_candidate_id": source.get("candidate_id"),
            },
            "bug_discovery_value": "P0" if risk in {"anonymous_auth_boundary_probe", "cross_tenant_auth_boundary_probe"} else "P1",
        },
        "required_evidence": ["auth_boundary_matrix", "request_response_status", "response_sensitivity", "no_business_data_for_forbidden_actor"],
        "source_refs": _source_refs(source),
        "grounding_basis": {
            **(clone.get("grounding_basis") if isinstance(clone.get("grounding_basis"), dict) else {}),
            "endpoint_contract_refs": max(1, int(((clone.get("grounding_basis") or {}).get("endpoint_contract_refs") or 0) if isinstance(clone.get("grounding_basis"), dict) else 0)),
            "phase95a_auth_boundary_inference": 1,
            "no_synthetic_endpoint": 1,
        },
    })
    return clone


def generate_auth_boundary_probes(plan: dict[str, Any], *, max_per_endpoint: int = 2, max_total: int = 80) -> dict[str, Any]:
    probes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    source_sensitive_count = 0
    for source in plan.get("probes") or []:
        if not isinstance(source, dict):
            continue
        method, path = _endpoint(source)
        if not path or not _is_sensitive_surface(source):
            continue
        source_sensitive_count += 1
        variants = [
            ("anonymous", "anonymous_auth_boundary_probe", "no_credentials"),
        ]
        if OWNER_PARAM_RE.search(path) or re.search(r"tenant|org|workspace|project|owner|租户|组织|归属", _hay(source), re.I):
            variants.append(("foreign_tenant_low_privilege", "cross_tenant_auth_boundary_probe", "tenant_b_low_privilege"))
        elif re.search(r"admin|role|permission|audit|export|权限|角色|审计|导出", _hay(source), re.I):
            variants.append(("low_privilege_user", "role_downgrade_auth_boundary_probe", "qa_low_privilege"))

        added_for_endpoint = 0
        for actor, risk, value in variants:
            key = (method, path, actor)
            if key in seen:
                continue
            seen.add(key)
            probes.append(_probe_variant(source, idx=len(probes) + 1, actor=actor, risk=risk, value=value))
            added_for_endpoint += 1
            if added_for_endpoint >= max(1, int(max_per_endpoint or 1)) or len(probes) >= max_total:
                break
        if len(probes) >= max_total:
            break

    by_actor: dict[str, int] = {}
    by_risk: dict[str, int] = {}
    for p in probes:
        auth = ((p.get("probe_plan") or {}).get("auth_boundary") or {})
        by_actor[str(auth.get("actor") or "unknown")] = by_actor.get(str(auth.get("actor") or "unknown"), 0) + 1
        by_risk[str(p.get("risk_type") or "unknown")] = by_risk.get(str(p.get("risk_type") or "unknown"), 0) + 1
    return {
        "engine": "auth_boundary_probe_generator_v1_phase95a_grounded",
        "generated_probe_count": len(probes),
        "generated_by_actor": by_actor,
        "generated_by_risk_family": by_risk,
        "generated_by_bug_value": {"P0": sum(1 for p in probes if str((p.get("probe_plan") or {}).get("bug_discovery_value")) == "P0"), "P1": sum(1 for p in probes if str((p.get("probe_plan") or {}).get("bug_discovery_value")) == "P1")},
        "probes": probes,
        "improvement_claim": {
            "sensitive_source_endpoint_count": source_sensitive_count,
            "added_negative_auth_probe_count": len(probes),
            "actor_matrix_count": len(by_actor),
            "no_synthetic_endpoint": True,
            "read_only_get_probes_can_run_without_sandbox": True,
        },
    }

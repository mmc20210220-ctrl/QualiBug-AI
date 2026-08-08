"""Product scan mainline identity and campaign-context binding.

Extracted from ``__main__`` so the canonical scan entrypoint stays a thin
orchestrator. Symbols are re-exported from ``__main__`` for compatibility.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .discovery_quality_projection import (
    SCHEMA_VERSION as QUALITY_PROJECTION_SCHEMA,
)
from .observed_product_scan_protocol import (
    find_evaluator_private_context_paths,
)

def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _reject_evaluator_private_context(context: dict[str, Any]) -> None:
    forbidden = find_evaluator_private_context_paths(context)
    if forbidden:
        # Report bare keys (drop the leading "$.", "$[i]" JSON-path prefix) so the
        # message is stable and human-readable regardless of nesting depth.
        bare_keys = [p.split(".", 1)[-1].split("[", 1)[0] for p in forbidden]
        raise ValueError(
            "evaluator_private_context_forbidden:" + ",".join(sorted(set(bare_keys)))
        )


class CanonicalProductScopeError(ValueError):
    """Runtime output cannot be projected onto the canonical customer scope."""


def _canonical_product_scope(v12: dict[str, Any]) -> dict[str, Any]:
    """Validate and project the only customer-visible defect scope.

    Delivery occurrences remain receipt/audit evidence.  They are never counted
    or displayed directly.  A runtime that emits any finding without the full
    canonical authority chain fails closed instead of falling back to title,
    path, severity, confidence, or historical-database identity.
    """

    from .canonical_defect_registry import (
        CanonicalDefectRegistryError,
        canonical_representative_findings,
        validate_canonical_defect_registry,
        validate_defect_identity_consistency,
    )
    from .discovery_mainline_contract import (
        MainlineContractError,
        validate_mainline_run_contract,
    )

    payload = _as_dict(v12)
    declared_findings = [
        dict(item)
        for item in payload.get("findings", [])
        if isinstance(item, dict)
    ] if isinstance(payload.get("findings"), list) else []
    candidates = [
        dict(item)
        for item in payload.get("candidate_findings", [])
        if isinstance(item, dict)
    ] if isinstance(payload.get("candidate_findings"), list) else []
    occurrences = [
        dict(item)
        for item in payload.get("delivery_occurrences", [])
        if isinstance(item, dict)
    ] if isinstance(payload.get("delivery_occurrences"), list) else []
    mainline = _as_dict(payload.get("mainline_run"))
    ledger = _as_dict(payload.get("obligation_attempt_ledger"))
    registry = _as_dict(payload.get("canonical_defect_registry"))
    blocked_formal = {
        "schema_version": QUALITY_PROJECTION_SCHEMA,
        "authority_status": "BLOCKED",
        "authority_reason": "canonical_defect_registry_required",
        "formal_customer_deliverable_count": 0,
        "canonical_defect_count": 0,
        "canonical_defect_ids": [],
        "delivery_occurrence_count": 0,
        "delivery_occurrence_finding_ids": [],
        "canonical_representative_findings": [],
        "executed_clue_count": len(candidates),
        "confirmation_receipt_count": 0,
        "candidate_count": len(candidates),
        "funnel_validated_bug_count": 0,
        "count_consistency": {
            "formal_equals_funnel_validated": None,
            "note": "Canonical authority is absent; zero is blocked, not a clean result.",
        },
    }

    authority_declared = bool(mainline or ledger or registry or occurrences)
    if not authority_declared:
        if declared_findings:
            raise CanonicalProductScopeError(
                "canonical_defect_registry_required_for_findings"
            )
        return {
            "status": "BLOCKED",
            "reason": "canonical_defect_registry_not_emitted",
            "findings": [],
            "candidates": candidates,
            "delivery_occurrences": [],
            "canonical_defect_registry": {},
            "formal_count_projection": blocked_formal,
            "defect_identity_consistency": {},
        }
    missing = [
        field
        for field, value in (
            ("mainline_run", mainline),
            ("obligation_attempt_ledger", ledger),
            ("canonical_defect_registry", registry),
        )
        if not value
    ]
    if missing:
        if (
            not declared_findings
            and not candidates
            and not occurrences
            and int(ledger.get("selected_count") or 0) == 0
        ):
            return {
                "status": "BLOCKED",
                "reason": "canonical_authority_not_emitted_for_empty_run",
                "findings": [],
                "candidates": [],
                "delivery_occurrences": [],
                "canonical_defect_registry": {},
                "formal_count_projection": blocked_formal,
                "defect_identity_consistency": {},
            }
        raise CanonicalProductScopeError(
            "canonical_authority_incomplete:" + ",".join(missing)
        )
    try:
        contract = validate_mainline_run_contract(mainline)
        validated_registry = validate_canonical_defect_registry(
            registry,
            mainline_run=contract,
            deliverable_occurrences=occurrences,
            obligation_attempt_ledger=ledger,
        )
        representatives = canonical_representative_findings(
            validated_registry,
            deliverable_occurrences=occurrences,
        )
    except (CanonicalDefectRegistryError, MainlineContractError) as exc:
        raise CanonicalProductScopeError(
            f"canonical_authority_invalid:{type(exc).__name__}:{exc}"
        ) from exc

    expected_ids = list(validated_registry.get("canonical_defect_ids") or [])
    representative_ids = [
        str(item.get("canonical_defect_id") or "").strip()
        for item in representatives
    ]
    if representative_ids != expected_ids:
        raise CanonicalProductScopeError(
            "canonical_representative_scope_mismatch"
        )
    declared_ids = [
        str(item.get("canonical_defect_id") or "").strip()
        for item in declared_findings
    ]
    if contract["customer_outputs_published"]:
        if declared_ids != expected_ids:
            raise CanonicalProductScopeError("canonical_finding_scope_mismatch")
        customer_findings = representatives
    else:
        if declared_findings:
            raise CanonicalProductScopeError(
                "shadow_run_customer_findings_forbidden"
            )
        customer_findings = []

    formal = _as_dict(payload.get("formal_count_projection"))
    if (
        formal.get("schema_version") != QUALITY_PROJECTION_SCHEMA
        or formal.get("authority_status") != "VERIFIED"
        or list(formal.get("canonical_defect_ids") or []) != expected_ids
        or int(formal.get("formal_customer_deliverable_count") or 0)
        != len(expected_ids)
        or list(formal.get("delivery_occurrence_finding_ids") or [])
        != list(validated_registry.get("delivery_occurrence_finding_ids") or [])
    ):
        raise CanonicalProductScopeError("formal_count_projection_mismatch")

    consistency = _as_dict(payload.get("defect_identity_consistency"))
    try:
        consistency = validate_defect_identity_consistency(
            consistency,
            required_occurrence_scopes={
                "delivery_gate_ids",
                "registry_occurrence_ids",
                "formal_projection_occurrence_ids",
            },
            required_canonical_scopes={
                "canonical_registry_ids",
                "formal_projection_ids",
                "product_projection_ids",
            },
        )
    except CanonicalDefectRegistryError as exc:
        raise CanonicalProductScopeError(
            f"defect_identity_consistency_invalid:{exc}"
        ) from exc

    return {
        "status": "VERIFIED",
        "reason": "",
        "findings": customer_findings,
        "candidates": candidates,
        "delivery_occurrences": occurrences,
        "canonical_defect_registry": validated_registry,
        "formal_count_projection": formal,
        "defect_identity_consistency": consistency,
    }


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _scan_campaign_context_defaults(project: str, root: Path) -> dict[str, str]:
    profile: dict[str, Any] = {}
    try:
        from .enterprise_pilot_runtime import load_connector_registry

        registry = load_connector_registry(project, root)
        raw_profile = registry.get("test_profile") if isinstance(registry, dict) else {}
        if isinstance(raw_profile, dict):
            profile = raw_profile
    except Exception:
        pass
    # Fall back to real_project_config.json when the connector registry does
    # not carry the identity fields.  This is the project-level SSOT that
    # stores target_id, environment_ref, environment_type, and base_url.
    if not (_first_text(profile.get("scope_id"), profile.get("target_id"))
            and _first_text(profile.get("environment_ref"), profile.get("target_environment"))):
        try:
            from .project_runtime_config import load_real_project_config

            rpc = load_real_project_config(project, root)
            if isinstance(rpc, dict):
                for key in ("target_id", "scope_id", "environment_ref",
                            "environment_type", "base_url", "approved_base_url"):
                    if rpc.get(key) and not profile.get(key):
                        profile[key] = rpc[key]
        except Exception:
            pass
    if not profile:
        return {}
    scope_id = _first_text(
        profile.get("scope_id"),
        profile.get("target_id"),
        profile.get("deployment_scope_id"),
        profile.get("project_scope_id"),
    )
    environment_ref = _first_text(
        profile.get("environment_ref"),
        profile.get("target_environment"),
        profile.get("environment"),
    )
    environment_type = _first_text(
        profile.get("environment_type"),
        profile.get("environment_kind"),
        profile.get("environment_class"),
    )
    defaults: dict[str, str] = {}
    if scope_id:
        defaults["scope_id"] = scope_id[:160]
    if environment_ref:
        defaults["environment_ref"] = environment_ref[:160]
    if environment_type:
        defaults["environment_type"] = environment_type[:80].lower()
    return defaults


def _apply_scan_execution_defaults(context: dict[str, Any], base_url: str) -> dict[str, Any]:
    """Apply the product-wide non-production execution/test-data defaults."""
    normalized = dict(context or {})
    from .private_pilot_scan_context_contract import (
        default_scan_execution_mode,
        default_scan_test_data_contract,
    )

    body = {**normalized, "base_url": str(base_url or normalized.get("base_url") or "")}
    if not str(normalized.get("execution_mode") or "").strip():
        normalized["execution_mode"] = default_scan_execution_mode(body)
    if not _as_dict(normalized.get("test_data_contract")):
        inferred_contract = default_scan_test_data_contract({
            **normalized,
            "base_url": body["base_url"],
        })
        if inferred_contract:
            normalized["test_data_contract"] = dict(inferred_contract)
    # A full discovery scan is a formal-phase campaign: the per-batch execution
    # budget must be the formal budget (≤100), not the silent small-scale
    # default (≤20). An undeclared phase previously truncated every compiled
    # obligation past ~20 per batch and projected that truncation as
    # OBLIGATION_BUDGET_REACHED — a capability ceiling masquerading as budget
    # exhaustion. Operators may still override the phase explicitly through
    # campaign_context; the receipted runtime_contract carries the declared
    # phase downstream.
    if not str(normalized.get("validation_phase") or "").strip():
        normalized["validation_phase"] = "formal"
    # A full discovery scan also guarantees the family-fair execution quota:
    # every risk family present in the compiled pool keeps at least this many
    # experiments per batch (default 1), so authorization obligations can
    # never crowd state/idempotency/conservation/validation/privacy out of
    # the execution budget. Operators may override the quota explicitly;
    # the receipted runtime_contract carries it downstream.
    if not str(normalized.get("family_execution_quota") or "").strip():
        normalized["family_execution_quota"] = 1
    return normalized


def _gap(code: str, detail: str) -> dict[str, str]:
    return {"kind": "SOURCE_INPUT_GAP", "code": code, "detail": detail}


def _safe_project(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return normalized or "unscoped"


def _sha256(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _bind_discovery_mainline_identity(
    *,
    project: str,
    context: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    """Bind one immutable run identity before V12 planning or execution."""

    normalized = dict(context or {})
    submitted_policy_id = str(normalized.get("policy_id") or "").strip()
    submitted_policy_version = str(
        normalized.get("policy_version") or ""
    ).strip()
    from .policy_registry import get_policy_registry, strategy_fingerprint
    from .policy_wiring import get_effective_policy_strategy, get_policy_value

    active_policy = get_policy_registry().get_active()
    policy_version = str(
        normalized.get("policy_version")
        or getattr(active_policy, "policy_version", "")
        or getattr(active_policy, "policy_id", "")
        or ""
    ).strip()
    if not policy_version:
        raise RuntimeError("discovery_mainline_policy_version_missing")
    target_id = str(
        normalized.get("target_id")
        or normalized.get("scope_id")
        or ""
    ).strip()
    environment_id = str(
        normalized.get("environment_id")
        or normalized.get("environment_ref")
        or normalized.get("target_environment")
        or ""
    ).strip()
    if not target_id:
        raise RuntimeError("discovery_mainline_target_id_missing")
    if not environment_id:
        raise RuntimeError("discovery_mainline_environment_id_missing")
    source_hash = str(
        _as_dict(normalized.get("source_manifest")).get("source_hash") or ""
    ).strip()
    run_material = "|".join(
        (project, target_id, environment_id, source_hash, f"{started:.9f}")
    )
    normalized.setdefault(
        "run_id",
        "RUN_" + hashlib.sha256(run_material.encode("utf-8")).hexdigest()[:24],
    )
    normalized.setdefault("target_id", target_id)
    normalized.setdefault("environment_id", environment_id)
    normalized.setdefault("policy_version", policy_version)
    policy_id = str(
        normalized.get("policy_id")
        or getattr(active_policy, "policy_id", "")
        or policy_version
    ).strip()
    if not policy_id:
        raise RuntimeError("discovery_mainline_policy_id_missing")
    effective_strategy_fingerprint = strategy_fingerprint(
        get_effective_policy_strategy()
    )
    active_strategy_fingerprint = (
        strategy_fingerprint(active_policy.strategy)
        if active_policy is not None
        else ""
    )
    if (
        active_strategy_fingerprint
        and effective_strategy_fingerprint != active_strategy_fingerprint
        and (not submitted_policy_id or not submitted_policy_version)
    ):
        raise RuntimeError("discovery_mainline_override_policy_identity_missing")
    submitted_strategy_fingerprint = str(
        normalized.get("strategy_fingerprint") or ""
    ).strip()
    if (
        submitted_strategy_fingerprint
        and submitted_strategy_fingerprint != effective_strategy_fingerprint
    ):
        raise RuntimeError("discovery_mainline_strategy_fingerprint_mismatch")
    normalized["policy_id"] = policy_id
    normalized["strategy_fingerprint"] = effective_strategy_fingerprint
    policy_authority = str(
        get_policy_value(
            "execution",
            "mainline_authority",
            "experiment_candidate",
        )
        or ""
    ).strip()
    if policy_authority not in {"legacy_champion", "experiment_candidate"}:
        raise RuntimeError("discovery_mainline_policy_authority_invalid")
    context_authority = str(normalized.get("mainline_authority") or "").strip()
    if context_authority and context_authority != policy_authority:
        raise RuntimeError("mainline_authority_policy_mismatch")
    normalized["mainline_authority"] = policy_authority
    normalized.setdefault("evaluation_mode", "operational")
    return normalized


def _bind_scan_rows_to_mainline(
    rows: list[dict[str, Any]],
    v12_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Join post-V12 external/UI rows to the immutable scan authority."""

    if not rows:
        return []
    from .discovery_mainline_contract import (
        MainlineContractError,
        validate_mainline_run_contract,
    )

    contract = validate_mainline_run_contract(
        _as_dict(v12_result).get("mainline_run")
    )
    fingerprint = contract["contract_fingerprint"]
    bound: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        observed = str(
            _as_dict(item.get("mainline_run")).get("contract_fingerprint")
            or item.get("mainline_contract_fingerprint")
            or ""
        ).strip()
        if observed and observed != fingerprint:
            raise MainlineContractError("post_v12_finding_authority_mismatch")
        item["mainline_run"] = {"contract_fingerprint": fingerprint}
        bound.append(item)
    return bound

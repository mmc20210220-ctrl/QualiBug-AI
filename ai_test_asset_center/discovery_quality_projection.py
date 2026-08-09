"""Single source of truth for discovery quality / formal-count projection.

Commercial quality claims come only from the external evaluator. Runtime and
product surfaces may expose funnel diagnostics and formal customer-deliverable
counts, but MUST NOT invent recall/precision/quality scores when measurement
status is NOT_MEASURED.
"""
from __future__ import annotations

from typing import Any

from .canonical_defect_registry import (
    CanonicalDefectRegistryError,
    build_defect_identity_consistency,
    canonical_representative_findings,
    validate_canonical_defect_registry,
    validate_defect_identity_consistency,
)
from .customer_delivery_gate import (
    customer_delivery_rejection_reasons,
)
from .discovery_mainline_contract import (
    MainlineContractError,
    MainlineRunContract,
    validate_mainline_run_contract,
)
from .formal_delivery_scope import (
    formal_customer_deliverable_findings,
    validated_delivery_gate_finding_ids,
)


SCHEMA_VERSION = "qualibug.discovery-quality-projection.v2"

# A stored artifact whose authority cannot be RE-DERIVED is a different failure from
# an artifact that contradicts itself, and the two must not share an outcome.
#
# "Cannot re-derive" means the proof is absent or no longer revalidates: no mainline
# run, no attempt ledger, a ledger that fails bundle revalidation, or a row whose
# authority fingerprint is missing. Nothing can be claimed about such an artifact, so
# the honest projection is zero deliverables with an explicit UNVERIFIABLE status and
# the exact reason -- the same discipline as NOT_MEASURED. Raising instead took the
# whole response down: 4 of 8 real projects under platform_outputs/ raise one of these
# today and return HTTP 500 from /api/v1/projects/{id}/command-center, so the console
# is simply broken for them.
#
# An internal CONTRADICTION (registry authority mismatch, occurrence scope mismatch,
# identity-consistency invalid, run-delivery contradiction) still raises. Those mean
# the product computed two disagreeing answers, which must never be smoothed over.
#
# Matching is on the full token before the first ':' so a future code that merely
# starts with one of these prefixes cannot silently inherit the degrade path.
# NOT included, deliberately: finding_authority_fingerprint_missing / _mismatch. A row
# sitting in an authoritative list while unable to prove it belongs there is a
# contradiction, not an unprovable archive, and
# tests/test_discovery_mainline_authority.py rightly pins it as raising. The reason it
# looked like a legacy-compatibility problem is that
# private_pilot_command_center_builder fed display rows -- which never carry a
# fingerprint -- into the authority input; the fix is to stop doing that, not to
# weaken the fingerprint check.
_UNVERIFIABLE_AUTHORITY_CODES = frozenset({
    "mainline_run_missing",
    "attempt_ledger_missing",
    "formal_attempt_ledger_invalid",
})


def _is_unverifiable_authority(exc: Exception) -> bool:
    return str(exc).split(":", 1)[0] in _UNVERIFIABLE_AUTHORITY_CODES


def _unverifiable_authority_projection(reason: str) -> dict[str, Any]:
    """A zero projection that names why nothing could be proven."""
    return {
        "schema_version": SCHEMA_VERSION,
        "authority_status": "UNVERIFIABLE",
        "authority_reason": reason,
        "formal_customer_deliverable_count": 0,
        "canonical_defect_count": 0,
        "canonical_defect_ids": [],
        "canonical_representative_findings": [],
        "delivery_occurrence_count": 0,
        "delivery_occurrence_finding_ids": [],
        "candidate_count": 0,
        "executed_clue_count": 0,
        "confirmation_receipt_count": 0,
        "funnel_validated_bug_count": 0,
        "count_consistency": {
            "formal_equals_funnel_validated": True,
            "note": "authority unverifiable; all counts are 0 by refusal, not by measurement",
        },
    }


_EMPTY_AUTHORITY_SCOPE: dict[str, Any] = {
    "mainline_run": None,
    "authoritative_findings": [],
    "authoritative_candidates": [],
    "shadow": [],
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _finding_id(item: dict[str, Any]) -> str:
    return _text(item.get("finding_id") or item.get("id") or item.get("bug_id"))


def authority_scoped_findings(scan_result: dict[str, Any]) -> dict[str, Any]:
    """Bind every projected row to the immutable run authority fingerprint."""

    result = _dict(scan_result)
    v12 = _dict(result.get("v12"))
    raw_findings = [item for item in _list(result.get("findings")) if isinstance(item, dict)]
    raw_candidates = [
        item for item in _list(result.get("candidate_findings")) if isinstance(item, dict)
    ]
    raw_shadow = [
        item
        for item in _list(result.get("shadow_findings") or v12.get("shadow_findings"))
        if isinstance(item, dict)
    ]
    raw_contract = result.get("mainline_run") or v12.get("mainline_run")
    if raw_contract is None:
        if raw_findings or raw_candidates or raw_shadow:
            raise MainlineContractError("mainline_run_missing")
        return {
            "mainline_run": None,
            "authoritative_findings": [],
            "authoritative_candidates": [],
            "shadow": [],
        }
    contract: MainlineRunContract = validate_mainline_run_contract(raw_contract)
    expected_fingerprint = contract["contract_fingerprint"]
    for item in raw_findings + raw_candidates + raw_shadow:
        finding_id = _finding_id(item) or "MISSING"
        observed_fingerprint = _text(
            _dict(item.get("mainline_run")).get("contract_fingerprint")
            or item.get("mainline_contract_fingerprint")
        )
        if not observed_fingerprint:
            raise MainlineContractError(
                f"finding_authority_fingerprint_missing:{finding_id}"
            )
        if observed_fingerprint != expected_fingerprint:
            # Verified Discovery Archive holdover（archive_entry=true）：
            # 历史已验证证据在先前已验证 run 的权威下交付，携带的是其交付 run
            # 自己的指纹（来源证明）。本 run 并未重新验证它们，强制匹配当前
            # run 指纹等于伪造本 run 权威——因此豁免相等性校验（仍要求非空
            # 指纹）。本 run 新交付与 candidate 仍必须严格匹配当前指纹。
            if item.get("archive_entry") is True:
                continue
            raise MainlineContractError(
                f"finding_authority_fingerprint_mismatch:{finding_id}"
            )
    if contract["customer_outputs_published"]:
        return {
            "mainline_run": contract,
            "authoritative_findings": raw_findings,
            "authoritative_candidates": raw_candidates,
            "shadow": [
                {
                    **item,
                    "finding_class": "shadow",
                    "shadow_origin": _text(item.get("shadow_origin"))
                    or "shadow_finding",
                }
                for item in raw_shadow
            ],
        }
    shadow: list[dict[str, Any]] = []
    for source, rows in (
        ("finding", raw_findings),
        ("candidate", raw_candidates),
        ("shadow_finding", raw_shadow),
    ):
        for item in rows:
            shadow.append({
                **item,
                "finding_class": "shadow",
                "shadow_origin": _text(item.get("shadow_origin")) or source,
            })
    return {
        "mainline_run": contract,
        "authoritative_findings": [],
        "authoritative_candidates": [],
        "shadow": shadow,
    }


def build_formal_count_projection(
    *,
    findings: Any = None,
    candidate_findings: Any = None,
    discovery_funnel: dict[str, Any] | None = None,
    obligation_attempt_ledger: dict[str, Any] | None = None,
    mainline_run: dict[str, Any] | None = None,
    canonical_defect_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project commercial counts from the canonical registry only."""
    all_findings = [item for item in _list(findings) if isinstance(item, dict)]
    candidates = [item for item in _list(candidate_findings) if isinstance(item, dict)]
    defects = formal_customer_deliverable_findings(
        all_findings,
        obligation_attempt_ledger=obligation_attempt_ledger,
    )
    deliverable_ids = {_finding_id(item) for item in defects}
    clues_from_findings = [
        item for item in all_findings if _finding_id(item) not in deliverable_ids
    ]
    funnel = _dict(discovery_funnel)
    funnel_validated = int(funnel.get("validated_bug_count") or 0)
    occurrence_ids = [_finding_id(item) for item in defects]
    if not all(occurrence_ids):
        raise MainlineContractError("formal_finding_id_missing")
    if len(occurrence_ids) != len(set(occurrence_ids)):
        raise MainlineContractError("formal_finding_id_duplicate")
    occurrence_ids = sorted(occurrence_ids)
    canonical_ids: list[str] = []
    canonical_representatives: list[dict[str, Any]] = []
    authority_status = "VERIFIED"
    authority_reason = ""
    if canonical_defect_registry is None:
        authority_status = "BLOCKED"
        authority_reason = "canonical_defect_registry_required"
    else:
        if not isinstance(mainline_run, dict):
            raise MainlineContractError(
                "canonical_defect_registry_mainline_required"
            )
        try:
            registry = validate_canonical_defect_registry(
                canonical_defect_registry,
                mainline_run=mainline_run,
                deliverable_occurrences=defects,
                obligation_attempt_ledger=_dict(obligation_attempt_ledger),
            )
            canonical_representatives = canonical_representative_findings(
                registry,
                deliverable_occurrences=defects,
            )
        except CanonicalDefectRegistryError as exc:
            raise MainlineContractError(
                f"canonical_defect_registry_invalid:{exc}"
            ) from exc
        canonical_ids = list(registry["canonical_defect_ids"])
        if list(registry["delivery_occurrence_finding_ids"]) != occurrence_ids:
            raise MainlineContractError(
                "canonical_defect_registry_occurrence_scope_mismatch"
            )
    formal_count = len(canonical_ids)
    return {
        "schema_version": SCHEMA_VERSION,
        "authority_status": authority_status,
        "authority_reason": authority_reason,
        "formal_customer_deliverable_count": formal_count,
        "canonical_defect_count": formal_count,
        "canonical_defect_ids": canonical_ids,
        "delivery_occurrence_count": len(occurrence_ids),
        "delivery_occurrence_finding_ids": occurrence_ids,
        "canonical_representative_findings": canonical_representatives,
        "executed_clue_count": len(clues_from_findings) + len(candidates),
        "confirmation_receipt_count": len(all_findings),
        "candidate_count": len(candidates),
        "funnel_validated_bug_count": funnel_validated,
        "count_consistency": {
            "formal_equals_funnel_validated": (
                formal_count == funnel_validated if "validated_bug_count" in funnel else None
            ),
            "note": (
                "formal_customer_deliverable_count equals canonical_defect_count; "
                "delivery_occurrence_count and funnel diagnostics are audit-only."
            ),
        },
    }


def build_finding_classification_projection(
    *,
    findings: Any = None,
    candidate_findings: Any = None,
    shadow_findings: Any = None,
    obligation_attempt_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Partition product results without reinterpreting the delivery gate."""
    finding_rows = [
        item for item in _list(findings) if isinstance(item, dict)
    ]
    verified_deliverable = formal_customer_deliverable_findings(
        finding_rows,
        obligation_attempt_ledger=obligation_attempt_ledger,
    )
    deliverable_ids = {_finding_id(item) for item in verified_deliverable}
    deliverable: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in finding_rows:
        row = dict(item)
        if _finding_id(row) in deliverable_ids:
            row["finding_class"] = "deliverable"
            deliverable.append(row)
            continue
        gate = _dict(row.get("delivery_gate_receipt"))
        reasons = [
            _text(value)
            for value in _list(gate.get("reason_codes"))
            if _text(value)
        ]
        if not reasons:
            reasons = ["VALIDATED_DELIVERY_GATE_REQUIRED"]
            diagnostic_reasons = customer_delivery_rejection_reasons(row)
            if diagnostic_reasons:
                row["legacy_delivery_diagnostics"] = diagnostic_reasons
        row["finding_class"] = "rejected"
        row["delivery_rejection_reasons"] = reasons
        rejected.append(row)
    candidates = []
    for item in _list(candidate_findings):
        if isinstance(item, dict):
            row = dict(item)
            row["finding_class"] = "candidate"
            candidates.append(row)
    shadow = []
    for item in _list(shadow_findings):
        if isinstance(item, dict):
            row = dict(item)
            row["finding_class"] = "shadow"
            shadow.append(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "deliverable": deliverable,
        "candidate": candidates,
        "rejected": rejected,
        "shadow": shadow,
        "counts": {
            "deliverable": len(deliverable),
            "candidate": len(candidates),
            "rejected": len(rejected),
            "shadow": len(shadow),
        },
    }


def build_external_evaluation_projection(
    *,
    measurement_status: str = "NOT_MEASURED",
    reason: str = "external_evaluator_receipt_required",
    formal_customer_deliverable_count: int = 0,
    evaluator_report: dict[str, Any] | None = None,
    claim_status: str | None = None,
    receipt_signing_key: str | bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Product-facing external evaluation projection.

    When NOT_MEASURED, quality_score / recall / precision / f1 are explicitly
    null and must not be rendered as 100 or 0.
    """
    status = str(measurement_status or "NOT_MEASURED").strip().upper() or "NOT_MEASURED"
    report = _dict(evaluator_report)
    claim = str(claim_status or report.get("claim_status") or status).strip().upper()
    measured = status == "MEASURED" and claim == "MEASURED"
    if measured or status == "MEASURED" or claim == "MEASURED":
        # Product processes must not hold the evaluator's symmetric HMAC key;
        # otherwise they could forge the same claim they verify. Until a
        # dedicated public-verification gateway exists, the honest product
        # state is NOT_MEASURED and evaluator metrics remain evaluator-only.
        raise ValueError("product_external_measurement_gateway_required")
    metrics = _dict(report.get("metrics") or report.get("quality_metrics"))
    projection: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "measurement_status": status,
        "claim_status": claim,
        "reason": str(reason or report.get("reason") or ""),
        "commercial_promotion_evidence": bool(report.get("commercial_promotion_evidence")) if measured else False,
        "formal_customer_deliverable_count": int(formal_customer_deliverable_count),
        "quality_score": None,
        "recall": None,
        "precision": None,
        "f1": None,
        "display": {
            "quality_label": (
                "外部质量评测已完成"
                if measured
                else "尚未完成外部质量评测"
            ),
            "suppress_quality_score": not measured,
            "suppress_recall_precision": not measured,
        },
    }
    if measured:
        for key in ("quality_score", "recall", "precision", "f1", "f1_score"):
            if key in metrics and metrics.get(key) is not None:
                out_key = "f1" if key == "f1_score" else key
                projection[out_key] = metrics.get(key)
        if projection["quality_score"] is None and report.get("quality_score") is not None:
            projection["quality_score"] = report.get("quality_score")
    return projection


def build_obligation_execution_projection(scan_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Product-facing obligation / experiment / adapter health (Spec §10.3)."""
    result = _dict(scan_result)
    v12 = _dict(result.get("v12"))
    obligations = _dict(result.get("test_obligations") or v12.get("test_obligations"))
    experiments = _dict(result.get("experiment_compile") or v12.get("experiment_compile"))
    experiment_execution = _dict(result.get("experiment_execution") or v12.get("experiment_execution"))
    plan = _dict(result.get("obligation_plan") or v12.get("obligation_plan"))
    adapters = _dict(result.get("execution_adapters") or v12.get("execution_adapters"))
    phases = _dict(result.get("phases") or v12.get("phases"))
    ir_phase = _dict(phases.get("behavior_ir"))
    oracle_phase = _dict(phases.get("oracle"))
    execution_phase = _dict(phases.get("execution"))
    formal = _dict(result.get("formal_count_projection"))
    if formal.get("schema_version") != SCHEMA_VERSION:
        formal = build_formal_count_projection(
            findings=result.get("findings") or v12.get("findings"),
            candidate_findings=result.get("candidate_findings"),
            discovery_funnel=_dict(result.get("discovery_funnel")),
            obligation_attempt_ledger=_dict(
                result.get("obligation_attempt_ledger")
                or v12.get("obligation_attempt_ledger")
            ) or None,
        )
    obligation_count = int(obligations.get("count") or obligations.get("obligation_count") or ir_phase.get("obligation_count") or 0)
    compiled = int(experiments.get("compiled_count") or ir_phase.get("compiled_experiments") or 0)
    blocked = int(experiments.get("blocked_count") or ir_phase.get("blocked_experiments") or 0)
    selected = int(
        experiment_execution.get("selected_count")
        or plan.get("selected_count")
        or len(_list(plan.get("selected")))
        or 0
    )
    executed = int(
        experiment_execution.get("executed_count")
        if "executed_count" in experiment_execution
        else (
            execution_phase.get("executed_count")
            or oracle_phase.get("traces_with_http")
            or oracle_phase.get("total_evaluated")
            or 0
        )
    )
    execution_blocked = int(experiment_execution.get("blocked_count") or 0)
    block_reasons = _dict(experiments.get("block_reason_counts"))
    fingerprints = {
        "policy_version": _text(_dict(result.get("policy") or result.get("active_policy")).get("policy_version")),
        "manifest_id": _text(_dict(result.get("evaluation_manifest") or result.get("manifest")).get("dataset_id")),
        "target_id": _text(result.get("target_id") or _dict(result.get("campaign")).get("environment_ref")),
        "source_hash": _text(
            _dict(result.get("campaign")).get("source_snapshot_hash")
            or _dict(result.get("behavior_slice_ledger")).get("source_snapshot_hash")
        ),
        "behavior_ir_model_id": _text(_dict(result.get("behavior_ir") or v12.get("behavior_ir")).get("model_id")),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "obligation_total": obligation_count,
        "obligation_compiled": compiled,
        "obligation_blocked": blocked,
        "obligation_selected": selected,
        "obligation_executed": executed,
        "obligation_execution_blocked": execution_blocked,
        "formal_defect_count": formal["formal_customer_deliverable_count"],
        "executed_clue_count": formal["executed_clue_count"],
        "block_reason_counts": block_reasons,
        "adapter_health": {
            "supported_count": int(adapters.get("supported_count") or len(_list(adapters.get("supported"))) or 0),
            "blocked_count": int(adapters.get("blocked_count") or len(_list(adapters.get("blocked"))) or 0),
            "degraded_count": int(adapters.get("degraded_count") or len(_list(adapters.get("degraded"))) or 0),
            "matrix": adapters if adapters else {},
        },
        "fixture_cleanup_health": {
            "status": _text(
                _dict(result.get("post_run_cleanliness") or result.get("cleanup") or execution_phase).get("status")
                or _dict(result.get("pipeline_health")).get("cleanup_status")
            ),
            "pipeline_health_status": _text(_dict(result.get("pipeline_health")).get("status")),
        },
        "fingerprints": fingerprints,
        "display": {
            "obligation_label": f"义务 {obligation_count} · 已编译 {compiled} · 已阻断 {blocked}",
            "execution_label": f"已执行 {executed} · 正式缺陷 {formal['formal_customer_deliverable_count']} · 线索 {formal['executed_clue_count']}",
        },
    }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _non_negative_int(value: Any, *, field: str, identities: dict[str, str]) -> int:
    if isinstance(value, bool):
        raise MainlineContractError(
            f"run_delivery_projection_invalid:{field}:identities={identities}"
        )
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise MainlineContractError(
            f"run_delivery_projection_invalid:{field}:identities={identities}"
        ) from exc
    if parsed < 0:
        raise MainlineContractError(
            f"run_delivery_projection_invalid:{field}:identities={identities}"
        )
    return parsed


def build_coverage_gap_projection(scan_result: dict[str, Any]) -> dict[str, Any]:
    """Aggregate explicit gaps from each discovery stage without inventing coverage."""

    result = _dict(scan_result)
    v12 = _dict(result.get("v12"))
    stage_values = {
        "input": result.get("input_gaps"),
        "legacy_scan": result.get("coverage_gaps"),
        "behavior_ir": _dict(result.get("behavior_ir") or v12.get("behavior_ir")).get("coverage_gaps"),
        "test_obligations": _dict(
            result.get("test_obligations") or v12.get("test_obligations")
        ).get("coverage_gaps"),
    }
    by_stage: dict[str, int] = {}
    for stage, value in stage_values.items():
        if value is None:
            continue
        if not isinstance(value, list):
            raise MainlineContractError(f"coverage_gap_projection_invalid:{stage}")
        if value:
            by_stage[stage] = len(value)
    pipeline = _dict(result.get("pipeline_health") or v12.get("pipeline_health"))
    terminal_reasons = {
        _text(code): int(count)
        for code, count in _dict(pipeline.get("terminal_reason_counts")).items()
        if _text(code) and int(count or 0) > 0
    }
    blocker_reasons = {
        code: count
        for code, count in terminal_reasons.items()
        if code.startswith("BLOCKED_")
    }
    return {
        "schema_version": "qualibug.discovery-coverage-gap-projection.v1",
        "total": sum(by_stage.values()),
        "by_stage": by_stage,
        "terminal_reason_counts": terminal_reasons,
        "blocked_reason_counts": blocker_reasons,
    }


def build_run_delivery_readiness_projection(
    scan_result: dict[str, Any],
    *,
    formal_count_projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide current-run formal publication eligibility from runtime SSOTs."""

    result = _dict(scan_result)
    v12 = _dict(result.get("v12"))
    pipeline = _dict(result.get("pipeline_health") or v12.get("pipeline_health"))
    ledger = _dict(
        result.get("obligation_attempt_ledger") or v12.get("obligation_attempt_ledger")
    )
    mainline = _dict(result.get("mainline_run") or v12.get("mainline_run"))
    campaign = _dict(result.get("campaign"))
    slice_ledger = _dict(result.get("behavior_slice_ledger") or v12.get("behavior_slice_ledger"))
    evidence_bundle = _dict(result.get("evidence_bundle"))
    identities = {
        key: value
        for key, value in {
            "campaign_id": _text(
                campaign.get("campaign_id")
                or mainline.get("campaign_id")
                or ledger.get("campaign_id")
            ),
            "run_id": _text(mainline.get("run_id") or ledger.get("run_id")),
            "mainline_authority": _text(mainline.get("mainline_authority")),
            "mainline_contract_fingerprint": _text(mainline.get("contract_fingerprint")),
            "attempt_ledger_fingerprint": _text(ledger.get("ledger_fingerprint")),
            "behavior_slice_ledger_fingerprint": _text(
                slice_ledger.get("ledger_fingerprint")
                or slice_ledger.get("source_snapshot_hash")
            ),
            "evidence_bundle_id": _text(evidence_bundle.get("bundle_id")),
        }.items()
        if value
    }
    counts = _dict(formal_count_projection or result.get("formal_count_projection"))
    selected = _non_negative_int(
        pipeline.get("selected_obligation_count"), field="selected_obligation_count", identities=identities
    )
    terminal = _non_negative_int(
        pipeline.get("terminal_obligation_count"), field="terminal_obligation_count", identities=identities
    )
    executed = _non_negative_int(
        pipeline.get("executed_obligation_count"), field="executed_obligation_count", identities=identities
    )
    blocked = _non_negative_int(
        pipeline.get("blocked_obligation_count"), field="blocked_obligation_count", identities=identities
    )
    cleanup_failures = _non_negative_int(
        pipeline.get("cleanup_failure_count"), field="cleanup_failure_count", identities=identities
    )

    if ledger:
        ledger_selected = _non_negative_int(
            ledger.get("selected_count"), field="ledger.selected_count", identities=identities
        )
        ledger_terminal = _non_negative_int(
            ledger.get("terminal_count"), field="ledger.terminal_count", identities=identities
        )
        contradictions = []
        if pipeline and selected != ledger_selected:
            contradictions.append(f"selected:{selected}!={ledger_selected}")
        if pipeline and terminal != ledger_terminal:
            contradictions.append(f"terminal:{terminal}!={ledger_terminal}")
        for name, left, right in (
            ("campaign", mainline.get("campaign_id"), ledger.get("campaign_id")),
            (
                "authority",
                mainline.get("contract_fingerprint"),
                ledger.get("mainline_contract_fingerprint"),
            ),
        ):
            if _text(left) and _text(right) and _text(left) != _text(right):
                contradictions.append(f"{name}:{_text(left)}!={_text(right)}")
        if contradictions:
            raise MainlineContractError(
                "run_delivery_projection_contradiction:"
                + ",".join(contradictions)
                + f":identities={identities}"
            )

    gaps = build_coverage_gap_projection(result)
    pipeline_status = _text(pipeline.get("status")).upper()
    reasons: list[str] = []
    if not pipeline:
        reasons.append("PIPELINE_HEALTH_MISSING")
    elif pipeline_status != "OK":
        reasons.append(f"PIPELINE_{pipeline_status or 'STATUS_MISSING'}")
    if not ledger:
        reasons.append("ATTEMPT_LEDGER_MISSING")
    elif ledger.get("complete") is not True:
        reasons.append("ATTEMPT_LEDGER_INCOMPLETE")
    if not mainline:
        reasons.append("MAINLINE_RUN_MISSING")
    elif not _text(mainline.get("mainline_authority")) or not _text(
        mainline.get("contract_fingerprint")
    ):
        reasons.append("MAINLINE_AUTHORITY_IDENTITY_MISSING")
    if ledger and (
        not _text(ledger.get("run_id"))
        or not _text(ledger.get("campaign_id"))
        or not _text(ledger.get("ledger_fingerprint"))
    ):
        reasons.append("ATTEMPT_LEDGER_IDENTITY_MISSING")
    if selected == 0:
        reasons.append("ZERO_SELECTED_OBLIGATIONS")
    if executed == 0:
        reasons.append("NO_REAL_EXECUTION")
    if selected > 0 and executed == 0 and blocked >= selected:
        reasons.append("ALL_OBLIGATIONS_BLOCKED")
    elif blocked > 0 or (selected > 0 and executed < selected):
        reasons.append("PARTIAL_OBLIGATION_EXECUTION")
    if cleanup_failures:
        reasons.append("CLEANUP_FAILURE")
    if gaps["total"]:
        reasons.append("COVERAGE_GAPS_REMAIN")
    if counts.get("schema_version") != SCHEMA_VERSION:
        reasons.append("FORMAL_COUNT_PROJECTION_MISSING")
    elif _text(counts.get("authority_status")).upper() != "VERIFIED":
        reasons.append("FORMAL_DELIVERY_AUTHORITY_NOT_VERIFIED")
    if mainline and mainline.get("customer_outputs_published") is not True:
        reasons.append("CUSTOMER_OUTPUTS_NOT_PUBLISHED")

    release_ready = not reasons
    eligible_count = _non_negative_int(
        counts.get("formal_customer_deliverable_count"),
        field="formal_customer_deliverable_count",
        identities=identities,
    )
    return {
        "schema_version": "qualibug.run-delivery-readiness.v1",
        "scope": "current_run_formal_finding_publication",
        "status": "READY" if release_ready else "NOT_READY",
        "release_ready": release_ready,
        "reason_codes": reasons,
        "pipeline_health_status": pipeline_status or "MISSING",
        "selected_obligation_count": selected,
        "terminal_obligation_count": terminal,
        "executed_obligation_count": executed,
        "blocked_obligation_count": blocked,
        "cleanup_failure_count": cleanup_failures,
        "coverage_gap_projection": gaps,
        "eligible_formal_deliverable_count": eligible_count,
        "published_formal_deliverable_count": eligible_count if release_ready else 0,
        "identities": identities,
    }


def attach_quality_projection_to_scan_result(scan_result: dict[str, Any]) -> dict[str, Any]:
    """Mutate-safe: return a copy of scan_result with SSOT quality projection."""
    result = dict(scan_result or {})
    ledger = _dict(
        result.get("obligation_attempt_ledger")
        or _dict(result.get("v12")).get("obligation_attempt_ledger")
    )
    delivery_occurrences = [
        item
        for item in _list(
            result.get("delivery_occurrences")
            or _dict(result.get("v12")).get("delivery_occurrences")
        )
        if isinstance(item, dict)
    ]
    canonical_registry = _dict(
        result.get("canonical_defect_registry")
        or _dict(result.get("v12")).get("canonical_defect_registry")
    )
    try:
        scoped = authority_scoped_findings(result)
        counts = build_formal_count_projection(
            findings=delivery_occurrences,
            candidate_findings=scoped["authoritative_candidates"],
            discovery_funnel=_dict(result.get("discovery_funnel")),
            obligation_attempt_ledger=ledger or None,
            mainline_run=scoped["mainline_run"],
            canonical_defect_registry=canonical_registry or None,
        )
    except MainlineContractError as exc:
        # See _UNVERIFIABLE_AUTHORITY_CODES: an unprovable stored artifact degrades
        # visibly; a self-contradicting one still raises.
        if not _is_unverifiable_authority(exc):
            raise
        scoped = dict(_EMPTY_AUTHORITY_SCOPE)
        counts = _unverifiable_authority_projection(str(exc))
    authoritative_findings = scoped["authoritative_findings"]
    authoritative_candidates = scoped["authoritative_candidates"]
    contract = scoped["mainline_run"]
    result["formal_count_projection"] = counts
    existing_external = _dict(result.get("external_evaluation"))
    external = build_external_evaluation_projection(
        measurement_status=str(existing_external.get("measurement_status") or "NOT_MEASURED"),
        reason=str(existing_external.get("reason") or "external_evaluator_receipt_required"),
        formal_customer_deliverable_count=counts["formal_customer_deliverable_count"],
        evaluator_report=_dict(existing_external.get("evaluator_report")),
        claim_status=existing_external.get("claim_status"),
    )
    # Preserve submission file pointer and other non-metric fields.
    for key, value in existing_external.items():
        if key not in external:
            external[key] = value
    run_delivery = build_run_delivery_readiness_projection(
        result,
        formal_count_projection=counts,
    )
    from .release_gate import reconcile_release_gate_with_run_readiness

    result["run_delivery_readiness"] = run_delivery
    result["release_gate"] = reconcile_release_gate_with_run_readiness(
        _dict(result.get("release_gate")),
        run_delivery,
    )
    external_reason_code = (
        _text(external.get("reason")).upper()
        if _text(external.get("reason"))
        else "EXTERNAL_EVALUATOR_RECEIPT_REQUIRED"
    )
    result["commercial_readiness"] = {
        "schema_version": "qualibug.commercial-readiness.v1",
        "scope": "commercial_product_readiness",
        "status": _text(external.get("claim_status")).upper() or "NOT_MEASURED",
        "reason_code": external_reason_code,
        "external_evaluation_schema_version": external["schema_version"],
        "commercial_promotion_evidence": external["commercial_promotion_evidence"],
    }
    obligation_proj = build_obligation_execution_projection(result)
    canonical_deliverables = (
        [
            dict(item)
            for item in _list(counts.get("canonical_representative_findings"))
            if isinstance(item, dict)
        ]
        if contract is not None and contract["customer_outputs_published"]
        else []
    )
    finding_classification = {
        "schema_version": SCHEMA_VERSION,
        "scope": "canonical_formal_delivery_eligibility",
        "publication_scope": "current_run_formal_finding_publication",
        "publication_status": run_delivery["status"],
        "deliverable": [
            {**dict(item), "finding_class": "deliverable"}
            for item in canonical_deliverables
        ],
        "candidate": [
            {**dict(item), "finding_class": "candidate"}
            for item in authoritative_candidates
        ],
        "rejected": [],
        "shadow": [
            {**dict(item), "finding_class": "shadow"}
            for item in scoped["shadow"]
        ],
        "counts": {
            "deliverable": len(canonical_deliverables),
            "candidate": len(authoritative_candidates),
            "rejected": 0,
            "shadow": len(scoped["shadow"]),
        },
    }
    if contract is not None and not contract["customer_outputs_published"]:
        external["commercial_promotion_evidence"] = False
    result["external_evaluation"] = external
    result["obligation_execution_projection"] = obligation_proj
    result["finding_classification"] = finding_classification
    identity_consistency = _dict(
        result.get("defect_identity_consistency")
        or _dict(result.get("v12")).get("defect_identity_consistency")
    )
    if contract is not None:
        required_occurrence_scopes = {
            "delivery_gate_ids",
            "registry_occurrence_ids",
            "formal_projection_occurrence_ids",
        }
        required_canonical_scopes = {
            "canonical_registry_ids",
            "formal_projection_ids",
            "product_projection_ids",
        }
        if (
            not identity_consistency
            and not delivery_occurrences
            and not canonical_registry
            and int(counts.get("formal_customer_deliverable_count") or 0) == 0
        ):
            identity_consistency = build_defect_identity_consistency(
                occurrence_scopes={
                    scope: [] for scope in sorted(required_occurrence_scopes)
                },
                canonical_scopes={
                    scope: [] for scope in sorted(required_canonical_scopes)
                },
            )
        try:
            result["defect_identity_consistency"] = (
                validate_defect_identity_consistency(
                    identity_consistency,
                    required_occurrence_scopes=required_occurrence_scopes,
                    required_canonical_scopes=required_canonical_scopes,
                )
            )
        except CanonicalDefectRegistryError as exc:
            raise MainlineContractError(
                f"defect_identity_consistency_invalid:{exc}"
            ) from exc
    result["authority_scope"] = {
        "status": "BOUND" if contract is not None else "UNBOUND_EMPTY",
        "contract_fingerprint": (
            contract["contract_fingerprint"] if contract is not None else ""
        ),
        "mainline_authority": (
            contract["mainline_authority"] if contract is not None else ""
        ),
        "evaluation_mode": contract["evaluation_mode"] if contract is not None else "",
        "customer_outputs_published": (
            contract["customer_outputs_published"] if contract is not None else False
        ),
        "shadow_count": len(scoped["shadow"]),
    }
    result["scope_counts"] = {
        "current_run_formal_deliverable": counts["formal_customer_deliverable_count"],
        "current_campaign_formal_deliverable": counts["formal_customer_deliverable_count"],
        "current_run_published_formal_deliverable": run_delivery[
            "published_formal_deliverable_count"
        ],
        "project_open_formal_deliverable": None,
        "semantics": {
            "current_run_formal_deliverable": "canonical_eligible_audit_count",
            "current_campaign_formal_deliverable": "canonical_eligible_audit_count",
            "current_run_published_formal_deliverable": "publication_count_after_run_delivery_readiness",
            "project_open_formal_deliverable": "not_projected_by_current_run",
        },
    }
    # Internal evidence-strength score must never be presented as quality when
    # external evaluation is incomplete.
    if external["display"]["suppress_quality_score"]:
        result["quality_claim_status"] = "NOT_MEASURED"
        result["commercial_quality_score"] = None
        # Keep legacy `score` for internal diagnostics but mark it non-commercial.
        result["score_semantics"] = {
            "score_field": "internal_evidence_strength_only",
            "commercial_quality_score": None,
            "note": "score is not recall/precision/commercial capability while external_evaluation is NOT_MEASURED",
        }
    else:
        result["quality_claim_status"] = "MEASURED"
        result["commercial_quality_score"] = external.get("quality_score")
        result["score_semantics"] = {
            "score_field": "external_evaluator",
            "commercial_quality_score": external.get("quality_score"),
        }
    # Preserve the immutable funnel receipt and attach the formal projection as
    # a separate authority. A mismatch is observable; projection must not hide
    # it by rewriting the historical funnel count.
    funnel = _dict(result.get("discovery_funnel"))
    if funnel:
        funnel = dict(funnel)
        funnel["formal_count_projection"] = counts
        result["discovery_funnel"] = funnel
    return result


def suppress_benchmark_quality_when_not_measured(
    benchmark_metrics: dict[str, Any] | None,
    external_evaluation: dict[str, Any] | None,
) -> dict[str, Any]:
    """Strip product-facing quality metrics from benchmark_metrics when NOT_MEASURED."""
    metrics = dict(benchmark_metrics or {})
    external = _dict(external_evaluation)
    status = str(external.get("measurement_status") or "NOT_MEASURED").upper()
    if status != "MEASURED":
        metrics = dict(metrics)
        metrics["measurement_status"] = "NOT_MEASURED"
        metrics["commercial_quality_suppressed"] = True
        for key in ("recall", "precision", "f1_score", "f1", "quality_score", "score"):
            if key in metrics:
                metrics[key] = None
        metrics["display_note"] = "尚未完成外部质量评测；内部 benchmark_metrics 不得作为商业能力主张。"
    return metrics

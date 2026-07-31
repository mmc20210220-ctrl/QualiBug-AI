"""Evaluator-side closed-world measurement for governed implicit rules.

The product may propose, promote, execute and retire implicit rules, but it may never
label those outputs as true or false.  This module consumes a frozen human/source-backed
candidate universe and measures the existing product artifacts without writing anything
back into runtime state.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

IMPLICIT_RULE_GROUND_TRUTH_SCHEMA = (
    "qualibug.enterprise-understanding-implicit-rule-ground-truth.v1"
)
IMPLICIT_RULE_MEASUREMENT_SCHEMA = (
    "qualibug.enterprise-understanding-implicit-rule-measurement.v1"
)
ANNOTATION_SCOPE = "FROZEN_CLOSED_WORLD_IMPLICIT_RULE_CANDIDATE_UNIVERSE"
_ALLOWED_STATUSES = frozenset(
    {
        "ABSENT",
        "CANDIDATE",
        "PENDING_VALIDATION",
        "CONFLICTED",
        "REJECTED",
        "ACTIVE",
        "STALE",
        "SUPERSEDED",
    }
)
_NON_EXECUTABLE_STATUSES = frozenset(
    {"ABSENT", "CANDIDATE", "PENDING_VALIDATION", "CONFLICTED", "REJECTED", "STALE", "SUPERSEDED"}
)
_CRITICALITY_WEIGHTS = {"P0": 5.0, "P1": 3.0, "P2": 1.0, "P3": 0.5}
_MATCH_FIELDS = (
    "logical_form",
    "operator",
    "statement",
    "subject_refs",
    "actor_refs",
    "operation_refs",
    "table_refs",
    "field_refs",
)


class ImplicitRuleGroundTruthValidationError(ValueError):
    """Raised when the evaluator-only implicit-rule truth set is not closed or valid."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _text(value).casefold())


def _keys(values: Any) -> tuple[str, ...]:
    return tuple(sorted({_key(value) for value in _list(values) if _key(value)}))


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or not (precision + recall):
        return None
    return round(2 * precision * recall / (precision + recall), 4)


def _source_snapshot_row_valid(row: dict[str, Any]) -> bool:
    path_identity = bool(_text(row.get("path")) and _text(row.get("blob_sha")))
    registry_identity = bool(
        _text(row.get("source_id"))
        and (_text(row.get("source_hash")) or _text(row.get("source_version_id")))
    )
    return path_identity or registry_identity


def _normalized_match(value: Any) -> dict[str, Any]:
    row = _dict(value)
    result: dict[str, Any] = {}
    for field in _MATCH_FIELDS:
        raw = row.get(field)
        if field.endswith("_refs"):
            values = _keys(raw)
            if values:
                result[field] = list(values)
        else:
            normalized = _key(raw)
            if normalized:
                result[field] = normalized
    return result


def validate_implicit_rule_ground_truth(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ImplicitRuleGroundTruthValidationError(
            "implicit-rule Ground Truth root must be an object"
        )
    if _text(document.get("schema")) != IMPLICIT_RULE_GROUND_TRUTH_SCHEMA:
        raise ImplicitRuleGroundTruthValidationError(
            f"schema must equal {IMPLICIT_RULE_GROUND_TRUTH_SCHEMA}"
        )
    project_id = _text(document.get("project_id"))
    if not project_id:
        raise ImplicitRuleGroundTruthValidationError("project_id is required")
    if _text(document.get("annotation_scope")) != ANNOTATION_SCOPE:
        raise ImplicitRuleGroundTruthValidationError(
            f"annotation_scope must equal {ANNOTATION_SCOPE}"
        )
    if document.get("candidate_universe_complete") is not True:
        raise ImplicitRuleGroundTruthValidationError(
            "candidate_universe_complete must be true before precision or overpromotion is measurable"
        )
    if bool(document.get("ground_truth_generated_from_product_output")):
        raise ImplicitRuleGroundTruthValidationError(
            "product output cannot generate implicit-rule Ground Truth"
        )

    source_snapshot = _rows(document.get("source_snapshot"))
    if not source_snapshot:
        raise ImplicitRuleGroundTruthValidationError(
            "source_snapshot must freeze the source authority used by annotators"
        )
    for index, row in enumerate(source_snapshot):
        if not _source_snapshot_row_valid(row):
            raise ImplicitRuleGroundTruthValidationError(
                f"source_snapshot[{index}] requires path+blob_sha or source_id+version identity"
            )

    rules = _rows(document.get("rules"))
    if not rules:
        raise ImplicitRuleGroundTruthValidationError("rules must not be empty")
    seen_ids: set[str] = set()
    seen_matches: set[str] = set()
    normalized_rules: list[dict[str, Any]] = []
    for index, raw in enumerate(rules):
        context = f"rules[{index}]"
        ground_truth_id = _text(raw.get("ground_truth_id"))
        if not ground_truth_id:
            raise ImplicitRuleGroundTruthValidationError(
                f"{context}: ground_truth_id is required"
            )
        if ground_truth_id in seen_ids:
            raise ImplicitRuleGroundTruthValidationError(
                f"duplicate ground_truth_id: {ground_truth_id}"
            )
        seen_ids.add(ground_truth_id)
        if _text(raw.get("annotation_status") or "CONFIRMED").upper() != "CONFIRMED":
            raise ImplicitRuleGroundTruthValidationError(
                f"{context}: closed-world annotations must be CONFIRMED"
            )
        if "candidate_id" in raw or "rule_id" in raw:
            raise ImplicitRuleGroundTruthValidationError(
                f"{context}: product candidate_id/rule_id cannot be used as Ground Truth identity"
            )
        expected_rule = raw.get("expected_rule")
        if not isinstance(expected_rule, bool):
            raise ImplicitRuleGroundTruthValidationError(
                f"{context}: expected_rule must be boolean"
            )
        expected_status = _text(raw.get("expected_status")).upper()
        if expected_status not in _ALLOWED_STATUSES:
            raise ImplicitRuleGroundTruthValidationError(
                f"{context}: expected_status must be one of {sorted(_ALLOWED_STATUSES)}"
            )
        if not expected_rule and expected_status not in {"ABSENT", "REJECTED"}:
            raise ImplicitRuleGroundTruthValidationError(
                f"{context}: hard-negative rules must expect ABSENT or REJECTED"
            )
        execution_required = raw.get("execution_required", False)
        if not isinstance(execution_required, bool):
            raise ImplicitRuleGroundTruthValidationError(
                f"{context}: execution_required must be boolean"
            )
        if execution_required and (not expected_rule or expected_status != "ACTIVE"):
            raise ImplicitRuleGroundTruthValidationError(
                f"{context}: execution_required is valid only for expected ACTIVE rules"
            )
        criticality = _text(raw.get("criticality") or "P2").upper()
        if criticality not in _CRITICALITY_WEIGHTS:
            raise ImplicitRuleGroundTruthValidationError(
                f"{context}: criticality must be one of {sorted(_CRITICALITY_WEIGHTS)}"
            )
        source_refs = [_text(value) for value in _list(raw.get("source_refs")) if _text(value)]
        source_locators = [
            _text(value) for value in _list(raw.get("source_locators")) if _text(value)
        ]
        if not source_refs and not source_locators:
            raise ImplicitRuleGroundTruthValidationError(
                f"{context}: source_refs or source_locators is required"
            )
        match = _normalized_match(raw.get("match"))
        if not match.get("logical_form"):
            raise ImplicitRuleGroundTruthValidationError(
                f"{context}: match.logical_form is required"
            )
        if len(match) < 2:
            raise ImplicitRuleGroundTruthValidationError(
                f"{context}: match requires logical_form plus at least one exact coordinate"
            )
        match_identity = json.dumps(match, ensure_ascii=False, sort_keys=True)
        if match_identity in seen_matches:
            raise ImplicitRuleGroundTruthValidationError(
                f"{context}: duplicate exact match contract"
            )
        seen_matches.add(match_identity)
        normalized = dict(raw)
        normalized.update(
            {
                "annotation_status": "CONFIRMED",
                "expected_status": expected_status,
                "criticality": criticality,
                "execution_required": execution_required,
                "match": match,
                "source_refs": source_refs,
                "source_locators": source_locators,
            }
        )
        normalized_rules.append(normalized)

    positive_count = sum(1 for row in normalized_rules if row.get("expected_rule") is True)
    negative_count = len(normalized_rules) - positive_count
    normalized_document = dict(document)
    normalized_document["rules"] = normalized_rules
    normalized_document["validation_receipt"] = {
        "status": "PASS",
        "project_id": project_id,
        "annotation_scope": ANNOTATION_SCOPE,
        "closed_world": True,
        "candidate_universe_complete": True,
        "rule_annotation_count": len(normalized_rules),
        "positive_rule_count": positive_count,
        "hard_negative_rule_count": negative_count,
        "execution_required_rule_count": sum(
            1 for row in normalized_rules if row.get("execution_required")
        ),
        "source_snapshot_count": len(source_snapshot),
        "generated_from_product_output": False,
        "model_writeback_allowed": False,
    }
    return normalized_document


def load_implicit_rule_ground_truth(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ImplicitRuleGroundTruthValidationError(
            f"implicit-rule Ground Truth file not found: {source}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ImplicitRuleGroundTruthValidationError(
            f"implicit-rule Ground Truth is not valid JSON: {source}: {exc}"
        ) from exc
    return validate_implicit_rule_ground_truth(document)


def _structured(row: dict[str, Any]) -> dict[str, Any]:
    return _dict(row.get("structured_expression"))


def _product_projection(row: dict[str, Any]) -> dict[str, Any]:
    structured = _structured(row)
    consequent = _dict(structured.get("consequent"))
    projection = {
        "logical_form": _key(row.get("logical_form") or structured.get("logical_form")),
        "operator": _key(row.get("operator") or structured.get("operator") or consequent.get("operator")),
        "statement": _key(row.get("statement") or row.get("name") or row.get("expected")),
        "subject_refs": list(_keys(row.get("subject_refs"))),
        "actor_refs": list(_keys(row.get("actor_refs"))),
        "operation_refs": list(_keys(row.get("operation_refs"))),
        "table_refs": list(_keys(row.get("table_refs"))),
        "field_refs": list(_keys(row.get("field_refs"))),
    }
    if not projection["subject_refs"]:
        fallback = _key(row.get("entity") or row.get("business_object"))
        if fallback:
            projection["subject_refs"] = [fallback]
    return projection


def _matches(match: dict[str, Any], row: dict[str, Any]) -> bool:
    product = _product_projection(row)
    for field, expected in match.items():
        if field.endswith("_refs"):
            if tuple(expected) != tuple(product.get(field) or []):
                return False
        elif expected != product.get(field):
            return False
    return True


def _candidate_statuses(asset: dict[str, Any]) -> dict[str, str]:
    receipt = _dict(asset.get("implicit_rule_candidate_validation_receipt"))
    result: dict[str, str] = {}
    for field, status in (
        ("validated", "ACTIVE"),
        ("pending", "PENDING_VALIDATION"),
        ("conflicted", "CONFLICTED"),
        ("rejected", "REJECTED"),
        ("stale", "STALE"),
    ):
        for row in _rows(receipt.get(field)):
            identity = _text(row.get("candidate_id"))
            if identity:
                result[identity] = status
    return result


def _implicit_candidates(asset: dict[str, Any]) -> list[dict[str, Any]]:
    statuses = _candidate_statuses(asset)
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(_rows(asset.get("implicit_rule_candidates"))):
        if _text(raw.get("kind") or "rule") != "rule":
            continue
        row = dict(raw)
        identity = _text(row.get("candidate_id")) or f"candidate:{index}"
        row["_benchmark_identity"] = identity
        row["_benchmark_status"] = statuses.get(identity, "CANDIDATE")
        rows.append(row)
    return rows


def _active_rules(asset: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(_rows(asset.get("rule_library"))):
        if _text(raw.get("derivation")) != "implicit_rule_entailment":
            continue
        row = dict(raw)
        row["_benchmark_identity"] = _text(row.get("rule_id")) or f"rule:{index}"
        row["_benchmark_status"] = "ACTIVE"
        rows.append(row)
    return rows


def _lifecycle_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    ledger = _dict(asset.get("implicit_rule_lifecycle_ledger"))
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(_rows(ledger.get("items"))):
        snapshot = _dict(raw.get("rule_snapshot"))
        row = {**snapshot, **raw}
        row["_benchmark_identity"] = _text(row.get("rule_id")) or f"lifecycle:{index}"
        row["_benchmark_status"] = _text(row.get("status")).upper() or "ABSENT"
        result.append(row)
    return result


def _match_rows(match: dict[str, Any], rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows if _matches(match, row)]


def _predicted_status(
    candidates: list[dict[str, Any]],
    active: list[dict[str, Any]],
    lifecycle: list[dict[str, Any]],
) -> str:
    if active:
        return "ACTIVE"
    lifecycle_statuses = {
        _text(row.get("_benchmark_status")).upper() for row in lifecycle
    }
    for status in ("SUPERSEDED", "REJECTED", "STALE", "ACTIVE"):
        if status in lifecycle_statuses:
            return status
    candidate_statuses = {
        _text(row.get("_benchmark_status")).upper() for row in candidates
    }
    for status in ("CONFLICTED", "PENDING_VALIDATION", "REJECTED", "CANDIDATE"):
        if status in candidate_statuses:
            return status
    return "ABSENT"


def _rule_ids(rows: Iterable[dict[str, Any]]) -> set[str]:
    return {_text(row.get("rule_id")) for row in rows if _text(row.get("rule_id"))}


def _execution_projection(asset: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, bool]:
    rule_ids = _rule_ids(rules)
    relationships = _rows(asset.get("relationships"))
    oracles = _rows(asset.get("oracle_library"))
    bound = any(
        _text(row.get("relation")) == "rule_to_interface"
        and _text(row.get("from") or row.get("from_ref")) in rule_ids
        and _text(row.get("to") or row.get("to_ref"))
        and _text(row.get("status") or "accepted").lower() in {"accepted", "active", "pass"}
        for row in relationships
    )
    oracle = any(_text(row.get("rule_id")) in rule_ids for row in oracles)
    runtime_receipts = [
        _dict(asset.get("implicit_rule_runtime_evolution")),
        _dict(asset.get("latest_implicit_rule_runtime_evolution")),
    ]
    observed = any(
        _text(rule.get("rule_ref")) in rule_ids
        and int(rule.get("evidence_count") or 0) > 0
        for receipt in runtime_receipts
        for rule in _rows(receipt.get("rules"))
    )
    return {
        "authoritative_operation_bound": bound,
        "oracle_projected": oracle,
        "executable_projection_complete": bound and oracle,
        "runtime_observed": observed,
    }


def _not_measured(reason_code: str, **details: Any) -> dict[str, Any]:
    result = {
        "schema": IMPLICIT_RULE_MEASUREMENT_SCHEMA,
        "status": "NOT_MEASURED",
        "reason_code": reason_code,
        "quality_claim_allowed": False,
        "ground_truth_entered_product_runtime": False,
        "model_writeback_allowed": False,
        "fuzzy_or_llm_alignment_used": False,
    }
    result.update({key: value for key, value in details.items() if value not in (None, "", {}, [])})
    return result


def _next_repair_target(metrics: dict[str, Any]) -> str:
    if metrics.get("candidate_recall") not in (None, 1.0):
        return "IMPLICIT_RULE_ENTAILMENT_RECALL"
    if metrics.get("promotion_precision") not in (None, 1.0):
        return "IMPLICIT_RULE_AUTHORITY_OVERPROMOTION"
    if metrics.get("promotion_recall") not in (None, 1.0):
        return "IMPLICIT_RULE_AUTHORITY_UNDERPROMOTION"
    if metrics.get("lifecycle_accuracy") not in (None, 1.0):
        return "IMPLICIT_RULE_SOURCE_VERSION_LIFECYCLE"
    if metrics.get("executable_projection_recall") not in (None, 1.0):
        return "IMPLICIT_RULE_OPERATION_ORACLE_BINDING"
    return ""


def evaluate_implicit_rules(
    ground_truth: dict[str, Any] | None,
    product_asset: dict[str, Any],
) -> dict[str, Any]:
    if not ground_truth:
        return _not_measured("EVALUATOR_IMPLICIT_RULE_GROUND_TRUTH_NOT_PROVIDED")
    validated = validate_implicit_rule_ground_truth(ground_truth)
    truth_rows = _rows(validated.get("rules"))
    candidates = _implicit_candidates(product_asset)
    active_rules = _active_rules(product_asset)
    lifecycle_rows = _lifecycle_rows(product_asset)

    alignments: list[dict[str, Any]] = []
    for truth in truth_rows:
        match = _dict(truth.get("match"))
        matched_candidates = _match_rows(match, candidates)
        matched_active = _match_rows(match, active_rules)
        matched_lifecycle = _match_rows(match, lifecycle_rows)
        predicted = _predicted_status(
            matched_candidates, matched_active, matched_lifecycle
        )
        execution = _execution_projection(product_asset, matched_active)
        alignments.append(
            {
                "ground_truth_id": truth.get("ground_truth_id"),
                "expected_rule": truth.get("expected_rule"),
                "expected_status": truth.get("expected_status"),
                "predicted_status": predicted,
                "status_exact": predicted == truth.get("expected_status"),
                "candidate_discovered": bool(matched_candidates or matched_active or matched_lifecycle),
                "active_rule_promoted": bool(matched_active),
                "criticality": truth.get("criticality"),
                "execution_required": bool(truth.get("execution_required")),
                "match": match,
                "candidate_ids": sorted(
                    {_text(row.get("candidate_id")) for row in matched_candidates if _text(row.get("candidate_id"))}
                ),
                "rule_ids": sorted(_rule_ids(matched_active)),
                "lifecycle_rule_ids": sorted(_rule_ids(matched_lifecycle)),
                "source_version_traceable": any(
                    bool(_list(row.get("source_version_refs")))
                    for row in [*matched_active, *matched_lifecycle]
                ),
                **execution,
            }
        )

    positive = [row for row in alignments if row.get("expected_rule") is True]
    expected_active = [row for row in positive if row.get("expected_status") == "ACTIVE"]
    expected_stale = [row for row in positive if row.get("expected_status") == "STALE"]
    discovered_positive = [row for row in positive if row.get("candidate_discovered")]
    promoted_true = [row for row in expected_active if row.get("active_rule_promoted")]

    matched_candidate_ids: set[str] = set()
    positive_candidate_ids: set[str] = set()
    negative_candidate_ids: set[str] = set()
    for truth, alignment in zip(truth_rows, alignments):
        ids = set(alignment.get("candidate_ids") or [])
        matched_candidate_ids.update(ids)
        if truth.get("expected_rule") is True:
            positive_candidate_ids.update(ids)
        else:
            negative_candidate_ids.update(ids)
    all_candidate_ids = {
        _text(row.get("_benchmark_identity")) for row in candidates if _text(row.get("_benchmark_identity"))
    }
    unmatched_candidate_ids = all_candidate_ids - matched_candidate_ids
    candidate_false_positive_ids = negative_candidate_ids | unmatched_candidate_ids

    matched_active_ids: set[str] = set()
    true_active_ids: set[str] = set()
    false_active_ids: set[str] = set()
    for truth, alignment in zip(truth_rows, alignments):
        ids = set(alignment.get("rule_ids") or [])
        matched_active_ids.update(ids)
        if truth.get("expected_rule") is True and truth.get("expected_status") == "ACTIVE":
            true_active_ids.update(ids)
        else:
            false_active_ids.update(ids)
    all_active_ids = {
        _text(row.get("_benchmark_identity")) for row in active_rules if _text(row.get("_benchmark_identity"))
    }
    unmatched_active_ids = all_active_ids - matched_active_ids
    false_active_ids.update(unmatched_active_ids)

    candidate_precision = _ratio(
        len(positive_candidate_ids),
        len(positive_candidate_ids | candidate_false_positive_ids),
    )
    candidate_recall = _ratio(len(discovered_positive), len(positive))
    promotion_precision = _ratio(
        len(true_active_ids), len(true_active_ids | false_active_ids)
    )
    promotion_recall = _ratio(len(promoted_true), len(expected_active))

    predicted_stale = [row for row in alignments if row.get("predicted_status") == "STALE"]
    true_stale = [row for row in expected_stale if row.get("predicted_status") == "STALE"]
    stale_precision = _ratio(
        len([row for row in predicted_stale if row.get("expected_status") == "STALE"]),
        len(predicted_stale),
    )
    stale_recall = _ratio(len(true_stale), len(expected_stale))

    execution_required = [row for row in alignments if row.get("execution_required")]
    operation_bound = [
        row for row in execution_required if row.get("authoritative_operation_bound")
    ]
    oracle_projected = [row for row in execution_required if row.get("oracle_projected")]
    executable = [
        row for row in execution_required if row.get("executable_projection_complete")
    ]
    runtime_receipt_present = bool(
        _dict(product_asset.get("implicit_rule_runtime_evolution"))
        or _dict(product_asset.get("latest_implicit_rule_runtime_evolution"))
    )
    runtime_observed = [row for row in execution_required if row.get("runtime_observed")]

    lifecycle_exact = [row for row in alignments if row.get("status_exact")]
    traceable = [
        row
        for row in alignments
        if row.get("predicted_status") not in {"ABSENT", "CANDIDATE", "PENDING_VALIDATION", "CONFLICTED"}
        and row.get("source_version_traceable")
    ]
    traceable_denominator = [
        row
        for row in alignments
        if row.get("predicted_status") not in {"ABSENT", "CANDIDATE", "PENDING_VALIDATION", "CONFLICTED"}
    ]

    weighted_false_promotion = sum(
        _CRITICALITY_WEIGHTS.get(_text(row.get("criticality")).upper(), 1.0)
        for row in alignments
        if row.get("active_rule_promoted")
        and not (
            row.get("expected_rule") is True and row.get("expected_status") == "ACTIVE"
        )
    )
    weighted_active_total = sum(
        _CRITICALITY_WEIGHTS.get(_text(row.get("criticality")).upper(), 1.0)
        for row in alignments
        if row.get("active_rule_promoted")
    )

    metrics = {
        "annotated_rule_count": len(alignments),
        "expected_rule_count": len(positive),
        "hard_negative_rule_count": len(alignments) - len(positive),
        "product_candidate_count": len(all_candidate_ids),
        "product_active_rule_count": len(all_active_ids),
        "candidate_true_positive_count": len(positive_candidate_ids),
        "candidate_false_positive_count": len(candidate_false_positive_ids),
        "candidate_false_negative_count": len(positive) - len(discovered_positive),
        "candidate_precision": candidate_precision,
        "candidate_recall": candidate_recall,
        "candidate_f1": _f1(candidate_precision, candidate_recall),
        "promotion_true_positive_count": len(true_active_ids),
        "promotion_false_positive_count": len(false_active_ids),
        "promotion_false_negative_count": len(expected_active) - len(promoted_true),
        "promotion_precision": promotion_precision,
        "promotion_recall": promotion_recall,
        "promotion_f1": _f1(promotion_precision, promotion_recall),
        "overpromotion_rate": _ratio(
            len(false_active_ids), len(true_active_ids | false_active_ids)
        ),
        "underpromotion_rate": _ratio(
            len(expected_active) - len(promoted_true), len(expected_active)
        ),
        "criticality_weighted_overpromotion_rate": _ratio(
            weighted_false_promotion, weighted_active_total
        ),
        "lifecycle_exact_count": len(lifecycle_exact),
        "lifecycle_accuracy": _ratio(len(lifecycle_exact), len(alignments)),
        "stale_precision": stale_precision,
        "stale_recall": stale_recall,
        "source_version_traceability_rate": _ratio(
            len(traceable), len(traceable_denominator)
        ),
        "execution_required_rule_count": len(execution_required),
        "authoritative_operation_binding_recall": _ratio(
            len(operation_bound), len(execution_required)
        ),
        "oracle_projection_recall": _ratio(
            len(oracle_projected), len(execution_required)
        ),
        "executable_projection_recall": _ratio(
            len(executable), len(execution_required)
        ),
        "runtime_observation_recall": (
            _ratio(len(runtime_observed), len(execution_required))
            if runtime_receipt_present
            else None
        ),
        "runtime_observation_measurable": runtime_receipt_present,
        "unmatched_product_candidate_ids": sorted(unmatched_candidate_ids),
        "unmatched_active_rule_ids": sorted(unmatched_active_ids),
    }
    repair_target = _next_repair_target(metrics)
    return {
        "schema": IMPLICIT_RULE_MEASUREMENT_SCHEMA,
        "status": "MEASURED",
        "measurement_contract": (
            "EVALUATOR_SIDE_FROZEN_CLOSED_WORLD_RULE_CANDIDATE_PROMOTION_LIFECYCLE_EXECUTION"
        ),
        "quality_claim_allowed": True,
        "ground_truth_validation_receipt": validated.get("validation_receipt") or {},
        "metric_authority": "EVALUATOR_SIDE_HUMAN_SOURCE_BACKED_GROUND_TRUTH",
        "metrics": metrics,
        "alignments": alignments,
        "false_promotions": [
            row
            for row in alignments
            if row.get("active_rule_promoted")
            and not (
                row.get("expected_rule") is True
                and row.get("expected_status") == "ACTIVE"
            )
        ],
        "missed_rules": [
            row for row in positive if not row.get("candidate_discovered")
        ],
        "lifecycle_errors": [row for row in alignments if not row.get("status_exact")],
        "execution_bridge_gaps": [
            row
            for row in execution_required
            if not row.get("executable_projection_complete")
        ],
        "next_repair_target": repair_target,
        "repair_policy": "FIX_THE_EARLIEST_EXISTING_IMPLICIT_RULE_MAINLINE_STAGE",
        "ground_truth_entered_product_runtime": False,
        "product_model_can_self_label_true_or_false": False,
        "fuzzy_or_llm_alignment_used": False,
        "model_writeback_allowed": False,
    }


__all__ = [
    "ANNOTATION_SCOPE",
    "IMPLICIT_RULE_GROUND_TRUTH_SCHEMA",
    "IMPLICIT_RULE_MEASUREMENT_SCHEMA",
    "ImplicitRuleGroundTruthValidationError",
    "evaluate_implicit_rules",
    "load_implicit_rule_ground_truth",
    "validate_implicit_rule_ground_truth",
]

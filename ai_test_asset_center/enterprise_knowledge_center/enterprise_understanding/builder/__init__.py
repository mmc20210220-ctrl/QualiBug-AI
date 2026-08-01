"""Enterprise-understanding builder with one identity authority.

The mature semantic projection remains in the historical sibling ``builder.py``.
Business-object type, identity evidence, stable registry drift, technical bindings,
authority receipts and optional external measurement are closed before projection.
"""
from __future__ import annotations

import importlib.util
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..business_object_benchmark import project_business_object_benchmark
from ..business_object_recognition import (
    apply_recognition_to_model,
    project_asset_for_recognized_objects,
    publish_recognition_and_identity,
    recognize_business_objects,
)
from ..identity_annotation_manifest import project_identity_annotation_manifest
from ..identity_authority_projection import project_identity_authority_receipt
from ..identity_benchmark import project_identity_benchmark
from ..identity_benchmark_regression import project_identity_benchmark_regression
from ..identity_evidence_policy import apply_identity_evidence_policy
from ..identity_field_evidence import augment_identity_field_evidence
from ..identity_registry_governance import govern_identity_registry
from ..identity_resolution import (
    apply_identity_resolution_to_model,
    project_asset_for_legacy_builder,
    resolve_enterprise_identities,
)
from ..identity_structural_evidence import project_identity_structural_candidates
from ..identity_structural_review import (
    apply_identity_structural_review_decisions,
    begin_identity_structural_review_rebuild,
    consume_identity_structural_review_pending_receipt,
    finalize_identity_structural_review_measurement,
    identity_structural_review_rebuild_in_progress,
    scrub_operator_structural_review_mentions,
)
from ..identity_source_governed_table_binding import (
    augment_source_governed_table_bindings,
)
from ..identity_structural_review_governance import (
    attach_identity_structural_review_admission,
    govern_identity_structural_review_decision_admission,
    preserve_identity_structural_review_registry_merges,
)
from ..identity_technical_projection import augment_technical_identity_projection
from ..schema import as_dict, as_list, stable_id, text

_PACKAGE = __package__.rsplit(".builder", 1)[0]
_LEGACY_NAME = f"{_PACKAGE}._semantic_projection_builder_v1"
_LEGACY_PATH = Path(__file__).resolve().parents[1] / "builder_legacy_v1.py"
_spec = importlib.util.spec_from_file_location(_LEGACY_NAME, _LEGACY_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - import contract failure
    raise ImportError(f"cannot load semantic projection builder: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_spec)
sys.modules.setdefault(_LEGACY_NAME, _legacy)
_spec.loader.exec_module(_legacy)

# Preserve private/public helpers used by existing tests and modules. Object-type
# and identity authority are overridden below; semantic projection is reused.
for _name, _value in vars(_legacy).items():
    if _name.startswith("__") or _name == "build_enterprise_understanding_model":
        continue
    globals().setdefault(_name, _value)


def _govern_identity_conflicts(resolution: dict[str, Any]) -> None:
    for conflict in as_list(resolution.get("conflicts")):
        if not isinstance(conflict, dict):
            continue
        kind = text(conflict.get("kind")) or "ENTERPRISE_IDENTITY_CONFLICT"
        conflict.setdefault(
            "conflict_id",
            stable_id(
                "enterprise_identity_conflict",
                kind,
                conflict.get("alias") or conflict.get("label") or conflict.get("labels"),
                conflict.get("candidate_entity_ids") or conflict.get("prior_entity_id"),
            ),
        )
        conflict.setdefault("reason_code", kind)
        conflict.setdefault("blocks_formal_understanding", True)


def _publish_identity_audit_receipts(
    asset: dict[str, Any], recognized_asset: dict[str, Any]
) -> None:
    for key in (
        "identity_evidence_policy_receipt",
        "enterprise_identity_registry_recompute_receipt",
        "enterprise_identity_authority_projection_receipt",
        "enterprise_identity_field_evidence",
        "enterprise_identity_source_governed_table_binding",
        "enterprise_identity_structural_evidence",
        "enterprise_identity_structural_review_queue",
        "enterprise_identity_structural_review_admission",
        "enterprise_identity_structural_review_receipt",
        "enterprise_identity_annotation_manifest",
        "enterprise_identity_benchmark",
    ):
        if key in recognized_asset:
            asset[key] = deepcopy(recognized_asset[key])


def _attach_identity_audit_receipts(
    model: dict[str, Any],
    asset: dict[str, Any],
    resolution: dict[str, Any],
) -> dict[str, Any]:
    model["identity_registry_recompute_receipt"] = as_dict(
        resolution.get("registry_recompute_receipt")
        or asset.get("enterprise_identity_registry_recompute_receipt")
    )
    model["identity_authority_projection"] = as_dict(
        resolution.get("authority_decision_projection")
        or asset.get("enterprise_identity_authority_projection_receipt")
    )
    field_evidence = as_dict(
        resolution.get("identity_field_evidence")
        or asset.get("enterprise_identity_field_evidence")
    )
    model["identity_field_evidence"] = field_evidence
    source_governed_table_binding = as_dict(
        resolution.get("source_governed_table_binding")
        or asset.get("enterprise_identity_source_governed_table_binding")
    )
    model["identity_source_governed_table_binding"] = (
        source_governed_table_binding
    )
    structural_evidence = as_dict(
        resolution.get("identity_structural_evidence")
        or asset.get("enterprise_identity_structural_evidence")
    )
    model["identity_structural_evidence"] = structural_evidence
    structural_admission = as_dict(
        model.get("identity_structural_review_admission")
        or asset.get("enterprise_identity_structural_review_admission")
    )
    model["identity_structural_review_admission"] = structural_admission
    structural_review = as_dict(
        model.get("identity_structural_review_receipt")
        or asset.get("enterprise_identity_structural_review_receipt")
    )
    model["identity_structural_review_receipt"] = structural_review
    model["identity_structural_review_queue"] = as_dict(
        model.get("identity_structural_review_queue")
        or asset.get("enterprise_identity_structural_review_queue")
        or structural_review.get("review_queue")
    )
    model["identity_annotation_manifest"] = as_dict(
        resolution.get("annotation_manifest")
        or asset.get("enterprise_identity_annotation_manifest")
    )
    benchmark = as_dict(
        resolution.get("benchmark") or asset.get("enterprise_identity_benchmark")
    )
    model["identity_benchmark"] = benchmark
    model["identity_evidence_policy_receipt"] = as_dict(
        asset.get("identity_evidence_policy_receipt")
    )

    identity_gate = as_dict(resolution.get("gate"))
    quality_gate = as_dict(benchmark.get("quality_gate"))
    model["identity_quality_gate"] = quality_gate
    gate = dict(as_dict(model.get("gate")))
    gate["identity_gate"] = identity_gate
    gate["identity_quality_gate"] = quality_gate
    model["gate"] = gate

    measured = (
        text(benchmark.get("status")) == "MEASURED"
        and bool(benchmark.get("quality_claim_allowed"))
    )
    metrics = dict(as_dict(model.get("metrics")))
    metrics.update(
        {
            "enterprise_identity_measurement_status": benchmark.get("status")
            or "NOT_MEASURED",
            "enterprise_identity_is_measured_precision_recall": measured,
            "enterprise_identity_quality_gate_status": quality_gate.get("status")
            or "NOT_CONFIGURED",
            "enterprise_identity_quality_gate_enforced": bool(
                quality_gate.get("enforced")
            ),
            "enterprise_identity_regression_gate_status": as_dict(
                benchmark.get("regression")
            ).get("status")
            or "NOT_COMPARABLE",
            "enterprise_identity_annotation_manifest_count": int(
                as_dict(model.get("identity_annotation_manifest")).get("mention_count")
                or 0
            ),
            "enterprise_identity_field_binding_count": int(
                field_evidence.get("field_binding_count") or 0
            ),
            "enterprise_identity_source_governed_table_binding_count": int(
                source_governed_table_binding.get("admitted_binding_count") or 0
            ),
            "enterprise_identity_source_governed_table_conflict_count": int(
                source_governed_table_binding.get("conflict_count") or 0
            ),
            "enterprise_identity_cross_technical_key_binding_count": int(
                field_evidence.get("cross_technical_binding_count") or 0
            ),
            "enterprise_identity_field_candidate_count": int(
                field_evidence.get("candidate_only_count") or 0
            ),
            "enterprise_identity_field_conflict_count": int(
                field_evidence.get("field_conflict_count") or 0
            ),
            "enterprise_identity_structural_profile_count": int(
                structural_evidence.get("entity_profile_count") or 0
            ),
            "enterprise_identity_structural_candidate_count": int(
                structural_evidence.get("candidate_count") or 0
            ),
            "enterprise_identity_strong_structural_candidate_count": int(
                structural_evidence.get("strong_candidate_count") or 0
            ),
            "enterprise_identity_structural_review_pending_count": int(
                as_dict(model.get("identity_structural_review_queue")).get(
                    "pending_count"
                )
                or 0
            ),
            "enterprise_identity_structural_review_blocked_decision_count": len(
                as_list(structural_admission.get("blocked_decision_ids"))
            ),
            "enterprise_identity_structural_review_applied_count": int(
                structural_review.get("applied_confirmation_count") or 0
            ),
            "enterprise_identity_structural_review_rejected_count": int(
                structural_review.get("rejected_count") or 0
            ),
            "enterprise_identity_structural_review_stale_count": int(
                structural_review.get("stale_decision_count") or 0
            ),
        }
    )
    if measured:
        metrics.update(
            {
                f"enterprise_identity_{key}": value
                for key, value in as_dict(benchmark.get("metrics")).items()
            }
        )
    model["metrics"] = metrics
    return model


def build_enterprise_understanding_model(asset: dict[str, Any]) -> dict[str, Any]:
    review_rebuild = identity_structural_review_rebuild_in_progress(asset)
    prior_registry = deepcopy(asset.get("enterprise_identity_registry") or {})
    apply_identity_evidence_policy(asset)
    recognition = recognize_business_objects(asset)
    recognition = project_business_object_benchmark(asset, recognition)
    recognized_asset = project_asset_for_recognized_objects(asset, recognition)
    resolution = resolve_enterprise_identities(recognized_asset)
    resolution = scrub_operator_structural_review_mentions(
        recognized_asset, resolution
    )
    resolution = govern_identity_registry(prior_registry, resolution, asset=recognized_asset)
    resolution = augment_technical_identity_projection(recognized_asset, resolution)
    resolution = augment_source_governed_table_bindings(
        recognized_asset, resolution
    )
    resolution = augment_identity_field_evidence(recognized_asset, resolution)
    resolution = project_identity_authority_receipt(recognized_asset, resolution)
    resolution = project_identity_annotation_manifest(recognized_asset, resolution)
    resolution = project_identity_benchmark(recognized_asset, resolution)
    resolution = project_identity_benchmark_regression(recognized_asset, resolution)
    _govern_identity_conflicts(resolution)
    publish_recognition_and_identity(asset, recognized_asset, resolution)
    _publish_identity_audit_receipts(asset, recognized_asset)
    projected_asset = project_asset_for_legacy_builder(recognized_asset, resolution)
    model = _legacy.build_enterprise_understanding_model(projected_asset)
    model = apply_identity_resolution_to_model(model, resolution)
    model = apply_recognition_to_model(model, recognition)
    model = project_identity_structural_candidates(asset, model, resolution)

    if review_rebuild:
        pending = consume_identity_structural_review_pending_receipt(asset)
        model = finalize_identity_structural_review_measurement(asset, model, pending)
        model = attach_identity_structural_review_admission(asset, model)
    else:
        model = govern_identity_structural_review_decision_admission(asset, model)
        model = apply_identity_structural_review_decisions(asset, model, resolution)
        model = attach_identity_structural_review_admission(asset, model)
        review_receipt = as_dict(
            model.get("identity_structural_review_receipt")
            or asset.get("enterprise_identity_structural_review_receipt")
        )
        if bool(review_receipt.get("rebuild_required")):
            review_receipt = preserve_identity_structural_review_registry_merges(
                asset, review_receipt
            )
            asset["enterprise_identity_structural_review_receipt"] = deepcopy(
                review_receipt
            )
            model["identity_structural_review_receipt"] = deepcopy(review_receipt)
            begin_identity_structural_review_rebuild(asset, review_receipt)
            return build_enterprise_understanding_model(asset)

    return _attach_identity_audit_receipts(model, asset, resolution)


__all__ = ["build_enterprise_understanding_model"]

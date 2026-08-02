"""Product identity bookkeeping must not become Behavior IR invariants."""
from __future__ import annotations

import json

from ai_test_asset_center import behavior_ir as bir
from ai_test_asset_center.behavior_ir_surface_reconciliation import (
    reconcile_declared_observation_surfaces,
)


def test_product_bookkeeping_rule_statements_are_excluded_from_invariants() -> None:
    asset = {
        "rule_library": [
            {
                "rule_id": "rule:business:order_owner",
                "statement": "买家只能查询自己的订单",
                "source_id": "src:business_rules",
            },
            {
                "rule_id": "rule:product:identity_annotation",
                "statement": (
                    '"manifest_id": "enterprise_identity_annotation_manifest:abc", '
                    '"is_ground_truth": false, '
                    '"required_annotation_output_schema": "qualibug"'
                ),
                "source_id": "src:enterprise_business_knowledge_center",
                "causal_chain": {
                    "trigger_action": "annotate",
                    "postconditions": [
                        {"description": '"blind_ground_truth_workflow_used": false'},
                        {
                            "description": (
                                '"product_candidates_enter_ground_truth": false'
                            )
                        },
                    ],
                },
            },
        ],
        "coverage_gaps": [
            {
                "kind": "BLOCKED_BUSINESS_COMPREHENSION_DOWNSTREAM_UNBOUND",
                "gap_type": "accepted_chinese_rule_missing_authoritative_operation",
                "source_id": "*",
                "blocked_rules": [
                    {
                        "rule_id": "zh_business:contaminated",
                        "statement": (
                            '"enterprise_identity_benchmark_repository_receipt": '
                            '{"ground_truth_loaded": false}'
                        ),
                        "reason": "BLOCKED_NO_AUTHORITATIVE_OPERATION_LINK",
                    }
                ],
            }
        ],
    }
    model = bir.build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="bookkeeping-guard",
        source_snapshot_hash="diag",
        available_surfaces={"http_api": True},
    )
    invariant_blobs = [
        json.dumps(row, ensure_ascii=False)
        for row in model.get("invariants") or []
        if isinstance(row, dict)
    ]
    assert not any("ground_truth" in blob.lower() for blob in invariant_blobs)
    gap_types = {
        str(row.get("gap_type") or "")
        for row in model.get("coverage_gaps") or []
        if isinstance(row, dict)
    }
    assert "product_bookkeeping_rule_excluded" in gap_types

    reconciled, _receipt = reconcile_declared_observation_surfaces(
        model,
        {"http_api": True, "db_snapshot": True, "process_timeline": True},
    )
    errors = bir.validate_behavior_ir(reconciled, require_explicit_relations=True)
    assert not any(
        err.startswith("forbidden_ground_truth_ref:") for err in errors
    ), errors

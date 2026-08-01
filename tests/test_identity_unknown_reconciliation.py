from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_authority_projection import (
    project_identity_authority_receipt,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_unknown_reconciliation import (
    reconcile_resolved_technical_identity_unknowns,
)


def _direct(ref: str, *, reason: str = "CROSS_SOURCE_IDENTITY_UNRESOLVED") -> dict:
    return {
        "unknown_id": f"direct:{ref}:{reason}",
        "kind": reason,
        "reason_code": reason,
        "details": {"artifact_ref": ref},
        "evidence": [{"asset_ref": ref, "quote": ref}],
    }


def _aggregate(*refs: str) -> dict:
    return {
        "unknown_id": "aggregate",
        "kind": "CROSS_SOURCE_IDENTITY_UNRESOLVED",
        "reason_code": "CROSS_SOURCE_IDENTITY_UNRESOLVED",
        "question": "stale",
        "details": {
            "unresolved_artifacts": [
                {"artifact_ref": ref, "artifact_type": "DATABASE_TABLE"}
                for ref in refs
            ]
        },
        "evidence": [
            {"asset_ref": ref, "quote": ref}
            for ref in refs
        ],
    }


def test_reconcile_direct_and_aggregate_unknowns_together() -> None:
    unknowns = [
        _direct("table:orders"),
        _direct("table:payments"),
        _aggregate("table:orders", "table:payments"),
    ]
    original = deepcopy(unknowns)

    result = reconcile_resolved_technical_identity_unknowns(
        unknowns, {"table:orders"}
    )

    assert unknowns == original
    assert [row["unknown_id"] for row in result] == [
        "direct:table:payments:CROSS_SOURCE_IDENTITY_UNRESOLVED",
        "aggregate",
    ]
    aggregate = result[-1]
    assert aggregate["details"]["unresolved_artifacts"] == [
        {"artifact_ref": "table:payments", "artifact_type": "DATABASE_TABLE"}
    ]
    assert aggregate["evidence"] == [
        {"asset_ref": "table:payments", "quote": "table:payments"}
    ]
    assert "1个技术资产" in aggregate["question"]


def test_reconcile_preserves_unrelated_unknown_reason() -> None:
    validation = _direct("table:orders", reason="DATABASE_SCHEMA_INVALID")
    assert reconcile_resolved_technical_identity_unknowns(
        [validation], {"table:orders"}
    ) == [validation]


def test_reconcile_supports_row_level_artifact_ref_and_is_idempotent() -> None:
    row = {
        "unknown_id": "legacy",
        "reason_code": "CROSS_SOURCE_IDENTITY_UNRESOLVED",
        "artifact_ref": "api:get-order",
    }
    once = reconcile_resolved_technical_identity_unknowns(
        [row], {"api:get-order"}
    )
    twice = reconcile_resolved_technical_identity_unknowns(
        once, {"api:get-order"}
    )
    assert once == twice == []


def test_authority_receipt_enforces_binding_unknown_postcondition() -> None:
    asset = {"cross_document_conflicts": []}
    resolution = {
        "bindings": [
            {
                "binding_id": "binding:orders",
                "artifact_ref": "table:orders",
                "status": "RESOLVED",
            }
        ],
        "unknowns": [
            _direct("table:orders"),
            _direct("table:payments"),
            _aggregate("table:orders", "table:payments"),
        ],
        "conflicts": [],
        "gate": {
            "status": "PARTIAL_ENTERPRISE_IDENTITY_BINDING",
            "entry_allowed": True,
            "metrics": {
                "technical_identity_unknown_count": 3,
                "unknown_count": 3,
            },
        },
    }

    result = project_identity_authority_receipt(asset, resolution)

    refs = {
        item.get("artifact_ref")
        for row in result["unknowns"]
        for item in (row.get("details") or {}).get("unresolved_artifacts", [])
    }
    assert "table:orders" not in refs
    assert all(
        (row.get("details") or {}).get("artifact_ref") != "table:orders"
        for row in result["unknowns"]
    )
    assert result["gate"]["status"] == "PARTIAL_ENTERPRISE_IDENTITY_BINDING"
    assert result["gate"]["metrics"]["technical_identity_unknown_count"] == 2
    assert result["gate"]["metrics"]["unknown_count"] == 2
    assert asset["enterprise_identity_gate"] == result["gate"]


def test_authority_receipt_turns_gate_pass_after_last_unknown_resolves() -> None:
    asset = {"cross_document_conflicts": []}
    resolution = {
        "bindings": [
            {
                "binding_id": "binding:orders",
                "artifact_ref": "table:orders",
                "status": "RESOLVED",
            }
        ],
        "unknowns": [_direct("table:orders"), _aggregate("table:orders")],
        "conflicts": [],
        "gate": {
            "status": "PARTIAL_ENTERPRISE_IDENTITY_BINDING",
            "entry_allowed": True,
            "metrics": {},
        },
    }

    result = project_identity_authority_receipt(asset, resolution)

    assert result["unknowns"] == []
    assert result["gate"]["status"] == "PASS"
    assert result["gate"]["entry_allowed"] is True
    assert result["gate"]["metrics"]["unknown_count"] == 0

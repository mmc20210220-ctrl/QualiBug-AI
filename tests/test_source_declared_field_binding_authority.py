"""Regressions for source-declared request-schema field gate authority."""
from __future__ import annotations

from ai_test_asset_center.binding_completeness_gate import check_binding_completeness
from ai_test_asset_center.binding_ledger import BindingLedger, BindingStatus


def _ledger_with_operation(op_id: str, path: str) -> BindingLedger:
    ledger = BindingLedger(project_id="p")
    op = ledger.propose(
        binding_type="operation",
        source_node_id=op_id,
        target_key=f"POST {path}",
        source_module="binding_builder",
        metadata={"method": "POST", "endpoint_path": path},
    )
    ledger.promote(
        op["binding_id"],
        BindingStatus.HIGH_CONFIDENCE,
        reason="test_source_declared_operation",
        confidence=0.9,
    )
    ledger.promote(
        op["binding_id"],
        BindingStatus.EXECUTABLE,
        reason="test_source_declared_operation",
    )
    return ledger


def _field(
    ledger: BindingLedger,
    *,
    op_id: str,
    path: str,
    name: str,
) -> dict:
    return ledger.propose(
        binding_type="field",
        source_node_id=op_id,
        target_key=f"{path}:{name}",
        source_module="binding_builder",
        metadata={
            "field_name": name,
            "request_path": name,
            "operation_ref": op_id,
            "schema_type": "string",
        },
    )


def _obligation(op_id: str, field_name: str) -> dict:
    return {
        "obligation_id": "obl_validation",
        "risk_family": "validation",
        "required_operations": [op_id],
        "required_actors": [],
        "required_fixtures": [],
        "property": {
            "template": "input_boundary_validation",
            "fields": [field_name],
        },
    }


def _ir(*ops: tuple[str, str]) -> dict:
    return {
        "operations": [
            {"id": op_id, "method": "POST", "path": path}
            for op_id, path in ops
        ]
    }


def test_exact_source_declared_candidate_field_is_structurally_executable() -> None:
    ledger = _ledger_with_operation("op_orders", "/orders")
    field = _field(
        ledger,
        op_id="op_orders",
        path="/orders",
        name="status",
    )
    # Reproduces production: exact request-schema field rows can remain
    # CANDIDATE because the generic confidence scorer has only two dimensions.
    assert field["status"] == BindingStatus.CANDIDATE.value

    receipt = check_binding_completeness(
        ledger,
        obligation=_obligation("op_orders", "status"),
        behavior_ir=_ir(("op_orders", "/orders")),
    )

    assert receipt["gate_passed"] is True
    assert set(receipt["executable_dimensions"]) == {"field", "operation"}


def test_path_placeholder_substring_cannot_impersonate_field_identity() -> None:
    ledger = _ledger_with_operation("op_orders", "/orders/{id}")
    # There is a real source field named id, but it belongs to another operation.
    _field(
        ledger,
        op_id="op_users",
        path="/users",
        name="id",
    )
    # The order operation has a different request field. Under the previous
    # generic ``ref in target_key`` rule, field="id" could match {id} in this
    # target key and falsely pass.
    _field(
        ledger,
        op_id="op_orders",
        path="/orders/{id}",
        name="status",
    )

    receipt = check_binding_completeness(
        ledger,
        obligation=_obligation("op_orders", "id"),
        behavior_ir=_ir(
            ("op_orders", "/orders/{id}"),
            ("op_users", "/users"),
        ),
    )

    assert receipt["gate_passed"] is False
    field_block = next(
        row for row in receipt["blocked_dimensions"] if row["dimension"] == "field"
    )
    assert field_block["missing_bindings"] == ["id(no_binding)"]


def test_rejected_source_field_remains_blocked() -> None:
    ledger = _ledger_with_operation("op_orders", "/orders")
    field = _field(
        ledger,
        op_id="op_orders",
        path="/orders",
        name="status",
    )
    ledger.promote(
        field["binding_id"],
        BindingStatus.REJECTED,
        reason="source_conflict",
    )

    receipt = check_binding_completeness(
        ledger,
        obligation=_obligation("op_orders", "status"),
        behavior_ir=_ir(("op_orders", "/orders")),
    )

    assert receipt["gate_passed"] is False
    field_block = next(
        row for row in receipt["blocked_dimensions"] if row["dimension"] == "field"
    )
    assert field_block["missing_bindings"] == ["status(status=REJECTED)"]

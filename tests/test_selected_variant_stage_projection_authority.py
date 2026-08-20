from __future__ import annotations

import pytest


def _receipt(name: str) -> dict[str, str]:
    return {"receipt_id": name, "status": "EXECUTED"}


def test_public_ledger_projection_preserves_exact_selected_variant() -> None:
    from ai_test_asset_center import obligation_attempt_ledger as ledger
    from ai_test_asset_center.recall_execution_variant_authority import (
        install_exact_execution_variant_authority,
    )

    install_exact_execution_variant_authority()

    base = "obl-x"
    legacy_face = f"{base}__v_aaaaaaaaaaaa"
    exact_selected_face = f"{base}__v_bbbbbbbbbbbb"
    selected = [
        {"obligation_id": base},
        {"obligation_id": exact_selected_face},
    ]

    # Put the exact selected face first.  The old public facade treated it as the
    # base compatibility face solely because it appeared first, then popped both
    # variant keys and silently lost the independently selected sibling attempt.
    compile_results = {
        exact_selected_face: _receipt("compile-exact"),
        legacy_face: _receipt("compile-legacy"),
    }
    execution_results = {
        exact_selected_face: _receipt("execute-exact"),
        legacy_face: _receipt("execute-legacy"),
    }
    gate_results = {
        exact_selected_face: _receipt("gate-exact"),
        legacy_face: _receipt("gate-legacy"),
    }

    compile_out, execution_out, gate_out = ledger._project_variant_stage_receipts(
        selected=selected,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )

    assert set(compile_out) == {base, exact_selected_face}
    assert set(execution_out) == {base, exact_selected_face}
    assert set(gate_out) == {base, exact_selected_face}
    assert compile_out[base]["receipt_id"] == "compile-legacy"
    assert execution_out[base]["receipt_id"] == "execute-legacy"
    assert gate_out[base]["receipt_id"] == "gate-legacy"
    assert compile_out[exact_selected_face]["receipt_id"] == "compile-exact"
    assert execution_out[exact_selected_face]["receipt_id"] == "execute-exact"
    assert gate_out[exact_selected_face]["receipt_id"] == "gate-exact"


def test_public_ledger_projection_rejects_multiple_unselected_legacy_faces() -> None:
    from ai_test_asset_center import obligation_attempt_ledger as ledger
    from ai_test_asset_center.recall_execution_variant_authority import (
        install_exact_execution_variant_authority,
    )

    install_exact_execution_variant_authority()

    base = "obl-x"
    first = f"{base}__v_aaaaaaaaaaaa"
    second = f"{base}__v_bbbbbbbbbbbb"

    with pytest.raises(Exception, match="multiple_unselected_variant_receipts"):
        ledger._project_variant_stage_receipts(
            selected=[{"obligation_id": base}],
            compile_results={
                first: _receipt("compile-a"),
                second: _receipt("compile-b"),
            },
            execution_results={
                first: _receipt("execute-a"),
                second: _receipt("execute-b"),
            },
            gate_results={
                first: _receipt("gate-a"),
                second: _receipt("gate-b"),
            },
        )

from __future__ import annotations

import pytest

from ai_test_asset_center.recall_execution_variant_authority import (
    collapse_variant_receipts_preserving_selected,
    repair_selected_execution_variant_identities,
)


def _compiled(
    obligation_id: str,
    experiment_id: str,
    *,
    expanded_from: str = "",
    arm_of: str = "",
) -> dict:
    receipt = {"status": "COMPILED"}
    if arm_of:
        receipt["arm_derived"] = True
    row = {
        "obligation_id": obligation_id,
        "experiment_id": experiment_id,
        "risk_family": "validation",
        "compile_receipt": receipt,
        "property": {"operation_ref": "op-write"},
    }
    if expanded_from:
        row["expanded_from_obligation_id"] = expanded_from
    if arm_of:
        row["arm_of"] = arm_of
    return row


def test_every_compiled_input_and_actor_face_gets_one_selected_identity() -> None:
    rep_v1 = _compiled("obl-rep__v_aaaaaaaaaaaa", "exp-rep-v1", expanded_from="obl-rep")
    rep_v2 = _compiled("obl-rep__v_bbbbbbbbbbbb", "exp-rep-v2", expanded_from="obl-rep")
    arm_v1 = _compiled("obl-actor", "exp-arm-v1", arm_of="obl-rep")
    arm_v2 = _compiled("obl-actor", "exp-arm-v2", arm_of="obl-rep")

    plan = {
        "selected_units": [
            {"coverage_unit_id": "cunit-1", "obligation_id": "obl-rep"}
        ],
        # Compatibility projection already points the representative base at
        # rep_v1; arm derivation currently appends two rows with the same actor
        # obligation id, which is the collision this authority repairs.
        "selected": [
            {
                "obligation_id": "obl-rep",
                "experiment_id": "exp-rep-v1",
                "coverage_unit_id": "cunit-1",
                "risk_family": "validation",
            },
            {
                "obligation_id": "obl-actor",
                "experiment_id": "exp-arm-v1",
                "coverage_unit_id": "cunit-1",
                "risk_family": "validation",
            },
            {
                "obligation_id": "obl-actor",
                "experiment_id": "exp-arm-v2",
                "coverage_unit_id": "cunit-1",
                "risk_family": "validation",
            },
        ],
    }
    units = [
        {
            "coverage_unit_id": "cunit-1",
            "representative_obligation_id": "obl-rep",
            "obligation_ids": ["obl-rep", "obl-actor"],
        }
    ]
    pack = {
        "experiments": [rep_v1, rep_v2, arm_v1, arm_v2],
        "compiled_count": 4,
    }
    by_obligation = {
        "obl-rep": rep_v1,
        rep_v1["obligation_id"]: rep_v1,
        rep_v2["obligation_id"]: rep_v2,
        # Existing arm force-index semantics leave the last arm under the base.
        "obl-actor": arm_v2,
    }

    receipt = repair_selected_execution_variant_identities(
        obligation_plan=plan,
        units=units,
        experiment_pack=pack,
        by_obligation=by_obligation,
        receipt={"status": "APPLIED"},
    )

    selected = plan["selected"]
    selected_ids = [row["obligation_id"] for row in selected]
    selected_experiment_ids = [row["experiment_id"] for row in selected]

    assert len(selected) == 4
    assert len(selected_ids) == len(set(selected_ids))
    assert set(selected_experiment_ids) == {
        "exp-rep-v1",
        "exp-rep-v2",
        "exp-arm-v1",
        "exp-arm-v2",
    }
    assert receipt["execution_variant_alias_count"] == 1
    assert receipt["compiled_input_variants_selected"] == 1
    assert receipt["selected_execution_face_count"] == 4

    # Same invariant enforced by build_agent_intent_plan: every selected key
    # resolves to exactly the experiment id carried by that selected row.
    for row in selected:
        resolved = by_obligation[row["obligation_id"]]
        assert resolved["experiment_id"] == row["experiment_id"]
        assert resolved["compile_receipt"]["status"] == "COMPILED"

    alias_row = next(
        row
        for row in selected
        if row["experiment_id"] == "exp-arm-v1"
    )
    assert alias_row["obligation_id"].startswith("obl-actor__v_")
    assert alias_row["execution_face_base_obligation_id"] == "obl-actor"

    rep_v2_row = next(
        row
        for row in selected
        if row["experiment_id"] == "exp-rep-v2"
    )
    assert rep_v2_row["obligation_id"] == "obl-rep__v_bbbbbbbbbbbb"
    assert rep_v2_row["execution_face_origin"] == "compiled_input_variant"


def test_selected_variant_receipt_is_not_folded_into_selected_base() -> None:
    base = {"status": "EXECUTED", "receipt_id": "exec-base"}
    variant = {"status": "DELIVERABLE", "receipt_id": "exec-variant"}
    collapsed = collapse_variant_receipts_preserving_selected(
        {
            "obl-x": base,
            "obl-x__v_aaaaaaaaaaaa": variant,
        },
        selected_ids={"obl-x", "obl-x__v_aaaaaaaaaaaa"},
    )
    assert collapsed["obl-x"] == base
    assert collapsed["obl-x__v_aaaaaaaaaaaa"] == variant


def test_single_legacy_unselected_variant_can_still_project_to_selected_base() -> None:
    variant = {"status": "EXECUTED", "receipt_id": "exec-variant"}
    collapsed = collapse_variant_receipts_preserving_selected(
        {"obl-x__v_aaaaaaaaaaaa": variant},
        selected_ids={"obl-x"},
    )
    assert collapsed == {"obl-x": variant}


def test_multiple_unselected_variants_fail_closed_instead_of_first_wins() -> None:
    with pytest.raises(Exception, match="multiple_unselected_variant_receipts"):
        collapse_variant_receipts_preserving_selected(
            {
                "obl-x__v_aaaaaaaaaaaa": {"receipt_id": "a"},
                "obl-x__v_bbbbbbbbbbbb": {"receipt_id": "b"},
            },
            selected_ids={"obl-x"},
        )

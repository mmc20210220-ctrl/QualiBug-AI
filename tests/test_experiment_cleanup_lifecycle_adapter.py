from __future__ import annotations

from ai_test_asset_center import experiment_cleanup_lifecycle_adapter as adapter


def _governance(*, accepted: bool = True) -> dict:
    return {
        "accepted": accepted,
        "observation_path": "/items/42",
        "before": {"status": 200, "body": {"id": "42", "state": "NEW"}},
        "write": {"status": 200 if accepted else 409, "body": {"id": "42"}},
        "after": {
            "status": 200,
            "body": {"id": "42", "state": "READY" if accepted else "NEW"},
        },
    }


def _precondition_step(*, accepted: bool = True) -> dict:
    return {
        "phase": "precondition",
        "step_id": "precondition_1",
        "operation_ref": "op_prepare",
        "actor_ref": "actor_1",
        "method": "PATCH",
        "path": "/items/42",
        "status_code": 200 if accepted else 409,
        "governance_receipt": _governance(accepted=accepted),
        "semantic_verdict_receipt": {
            "step_id": "precondition_1",
            "target_reached": accepted,
        },
    }


def _experiment() -> dict:
    return {
        "precondition_plan": [
            {
                "step_id": "precondition_1",
                "operation_ref": "op_prepare",
                "body": {"item_id": "{id}", "state": "READY"},
            }
        ]
    }


def _call(monkeypatch, *, steps_out: list[dict], existing_bodies: dict | None = None):
    captured: dict = {}

    def fake_cleanup(**kwargs):
        captured.update(kwargs)
        return {
            "steps_out": [
                *kwargs["steps_out"],
                {
                    "phase": "cleanup",
                    "cleanup_subject_id": "cleanup_1",
                    "status_code": 200,
                },
            ],
            "observations": kwargs.get("observations") or {},
            "contract_evidence_receipts": [],
            "cleanup_failures": 0,
        }

    monkeypatch.setattr(adapter, "_execute_cleanup", fake_cleanup)
    result = adapter.execute_experiment_cleanup_compensation(
        exp=_experiment(),
        steps_out=steps_out,
        observations={},
        request_bodies_for_cleanup=dict(existing_bodies or {}),
        runtime_bindings={"id": "42"},
        ops={
            "op_prepare": {
                "id": "op_prepare",
                "method": "PATCH",
                "path": "/items/{id}",
                "request_example": {"item_id": "{id}", "state": "READY"},
            }
        },
    )
    return captured, result


def test_precondition_write_is_visible_to_existing_cleanup_filters(monkeypatch) -> None:
    original = _precondition_step()
    captured, result = _call(monkeypatch, steps_out=[original])

    projected = captured["steps_out"]
    assert projected[0] is original
    shadow = projected[1]
    assert shadow["phase"] == "treatment"
    assert shadow["original_phase"] == "precondition"
    assert shadow["_precondition_cleanup_shadow"] is True
    assert shadow["step_id"] == "precondition_1"
    assert shadow["governance_receipt"] == original["governance_receipt"]
    assert shadow["observation_path"] == "/items/42"

    assert [row["phase"] for row in result["steps_out"]] == [
        "precondition",
        "cleanup",
    ]
    assert not any(
        row.get("_precondition_cleanup_shadow") is True
        for row in result["steps_out"]
    )


def test_precondition_request_body_is_materialized_for_compensation(monkeypatch) -> None:
    captured, _ = _call(monkeypatch, steps_out=[_precondition_step()])

    assert captured["request_bodies_for_cleanup"]["precondition_1"] == {
        "item_id": "42",
        "state": "READY",
    }


def test_existing_cleanup_request_body_is_never_overwritten(monkeypatch) -> None:
    authoritative = {"state": "SOURCE_CAPTURED"}
    captured, _ = _call(
        monkeypatch,
        steps_out=[_precondition_step()],
        existing_bodies={"precondition_1": authoritative},
    )

    assert captured["request_bodies_for_cleanup"]["precondition_1"] is authoritative


def test_rejected_precondition_receipt_is_projected_for_unchanged_proof(monkeypatch) -> None:
    captured, result = _call(
        monkeypatch,
        steps_out=[_precondition_step(accepted=False)],
    )

    shadow = captured["steps_out"][1]
    assert shadow["governance_receipt"]["accepted"] is False
    receipt = result["observations"]["precondition_cleanup_projection_receipt"]
    assert receipt["projected_step_ids"] == ["precondition_1"]
    assert receipt["projected_step_count"] == 1
    assert receipt["shadow_rows_persisted"] is False


def test_non_precondition_steps_are_not_projected(monkeypatch) -> None:
    treatment = {
        "phase": "treatment",
        "step_id": "treatment_1",
        "operation_ref": "op_prepare",
        "governance_receipt": _governance(),
    }
    captured, result = _call(monkeypatch, steps_out=[treatment])

    assert captured["steps_out"] == [treatment]
    receipt = result["observations"]["precondition_cleanup_projection_receipt"]
    assert receipt["projected_step_ids"] == []
    assert receipt["projected_step_count"] == 0

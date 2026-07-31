from copy import deepcopy

from ai_test_asset_center.experiment_compiler_obligation import make_experiment


def _compile() -> dict:
    return make_experiment(
        obligation_id="obligation-compile-seal",
        risk_family="state",
        control_plan=[{"step_id": "control-1"}],
        treatment_plan=[{"step_id": "treatment-1"}],
        observers=[
            {"observer_id": "http_response"},
            {"observer_id": "business_effect"},
        ],
        compile_receipt={"status": "COMPILED", "reason_code": ""},
    )


def test_compile_receipt_contains_complete_observer_binding_receipt() -> None:
    experiment = _compile()
    top_level = experiment["observer_subject_binding_receipt"]
    sealed = experiment["compile_receipt"][
        "observer_subject_binding_receipt"
    ]

    assert sealed == top_level
    assert sealed is not top_level
    assert sealed["complete"] is True
    assert sealed["binding_count"] == 2
    assert sealed["bindings"] == [
        {
            "observer_id": "http_response",
            "scope_mode": "per_plan_step",
            "subject_step_ids": ["control-1", "treatment-1"],
        },
        {
            "observer_id": "business_effect",
            "scope_mode": "single_step",
            "subject_step_id": "treatment-1",
        },
    ]
    assert experiment["compile_receipt"][
        "observer_subject_binding_hash"
    ] == sealed["binding_hash"]


def test_top_level_binding_mutation_does_not_change_compile_seal() -> None:
    experiment = _compile()
    sealed_before = deepcopy(
        experiment["compile_receipt"]["observer_subject_binding_receipt"]
    )

    experiment["observer_subject_binding_receipt"]["bindings"][0][
        "subject_step_ids"
    ].append("forged-step")

    assert experiment["compile_receipt"][
        "observer_subject_binding_receipt"
    ] == sealed_before

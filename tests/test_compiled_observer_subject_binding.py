import ast
from pathlib import Path

import ai_test_asset_center.experiment_compiler_obligation as compiler
import ai_test_asset_center.experiment_outcome_finalizer as finalizer


ROOT = Path(__file__).resolve().parents[1]


def _experiment(*, observers, control=None, treatment=None):
    return compiler.make_experiment(
        obligation_id="obligation-1",
        risk_family="state",
        control_plan=list(control or []),
        treatment_plan=list(treatment or []),
        observers=list(observers),
        compile_receipt={"status": "COMPILED", "reason_code": ""},
    )


def test_compiler_binds_http_to_plan_set_and_business_to_final_treatment() -> None:
    experiment = _experiment(
        control=[{"step_id": "control-1"}],
        treatment=[
            {"step_id": "treatment-1"},
            {"step_id": "treatment-2"},
        ],
        observers=[
            {"observer_id": "http_response"},
            {"observer_id": "business_effect"},
            {"observer_id": "after_state"},
        ],
    )
    observers = {
        row["observer_id"]: row for row in experiment["observers"]
    }

    assert observers["http_response"]["scope_mode"] == "per_plan_step"
    assert observers["http_response"]["subject_step_ids"] == [
        "control-1",
        "treatment-1",
        "treatment-2",
    ]
    assert observers["http_response"]["scope_basis"] == (
        "compiled_plan_step_set"
    )

    for observer_id in ("business_effect", "after_state"):
        assert observers[observer_id]["scope_mode"] == "single_step"
        assert observers[observer_id]["subject_step_id"] == "treatment-2"
        assert observers[observer_id]["scope_basis"] == (
            "compiled_protocol_final_measurement"
        )

    receipt = experiment["observer_subject_binding_receipt"]
    assert receipt["complete"] is True
    assert receipt["binding_count"] == 3
    assert receipt["control_step_ids"] == ["control-1"]
    assert receipt["treatment_step_ids"] == [
        "treatment-1",
        "treatment-2",
    ]
    assert receipt["invalid_observer_ids"] == []
    assert len(receipt["binding_hash"]) == 64

    compile_receipt = experiment["compile_receipt"]
    assert compile_receipt["observer_subject_binding_complete"] is True
    assert compile_receipt["observer_subject_binding_count"] == 3
    assert compile_receipt["observer_subject_binding_hash"] == receipt[
        "binding_hash"
    ]


def test_explicit_observer_subject_is_preserved() -> None:
    experiment = _experiment(
        control=[{"step_id": "control-1"}],
        treatment=[{"step_id": "treatment-1"}],
        observers=[
            {
                "observer_id": "authorization_comparison",
                "subject_step_id": "control-1",
            }
        ],
    )
    observer = experiment["observers"][0]

    assert observer["subject_step_id"] == "control-1"
    assert observer["scope_mode"] == "single_step"
    assert observer["scope_basis"] == "observer_declaration"


def test_control_only_protocol_binds_semantic_observer_to_final_control() -> None:
    experiment = _experiment(
        control=[
            {"step_id": "control-1"},
            {"step_id": "control-2"},
        ],
        observers=[{"observer_id": "typed_assertion"}],
    )

    assert experiment["observers"][0]["subject_step_id"] == "control-2"
    assert experiment["observers"][0]["scope_basis"] == (
        "compiled_protocol_final_measurement"
    )


def test_binding_hash_is_deterministic() -> None:
    kwargs = {
        "control": [{"step_id": "control-1"}],
        "treatment": [{"step_id": "treatment-1"}],
        "observers": [
            {"observer_id": "http_response"},
            {"observer_id": "entity_state"},
        ],
    }
    first = _experiment(**kwargs)
    second = _experiment(**kwargs)

    assert first["observer_subject_binding_receipt"] == second[
        "observer_subject_binding_receipt"
    ]
    assert first["compile_receipt"][
        "observer_subject_binding_hash"
    ] == second["compile_receipt"]["observer_subject_binding_hash"]


def test_original_input_observer_objects_are_not_mutated() -> None:
    observers = [
        {"observer_id": "http_response"},
        {"observer_id": "business_effect"},
    ]
    original = [dict(row) for row in observers]

    _experiment(
        treatment=[{"step_id": "treatment-1"}],
        observers=observers,
    )

    assert observers == original


def test_core_make_experiment_hook_is_idempotent() -> None:
    original = getattr(
        compiler._core,
        compiler._ORIGINAL_MAKE_EXPERIMENT_ATTR,
    )

    compiler._install_core_hooks()
    compiler._install_core_hooks()

    assert getattr(
        compiler._core,
        compiler._ORIGINAL_MAKE_EXPERIMENT_ATTR,
    ) is original
    assert compiler._core.make_experiment is compiler._subject_bound_make_experiment
    assert original is not compiler._subject_bound_make_experiment


def test_finalizer_consumes_compiled_subject_without_runtime_selection() -> None:
    experiment = _experiment(
        control=[{"step_id": "control-1"}],
        treatment=[{"step_id": "treatment-1"}],
        observers=[{"observer_id": "business_effect"}],
    )

    step_id, basis = finalizer._observer_subject_step(
        experiment,
        "business_effect",
    )

    assert step_id == "treatment-1"
    assert basis == "observer_declaration"


def test_blocked_minimal_experiment_does_not_forge_subject_binding() -> None:
    experiment = compiler.make_experiment(
        obligation_id="blocked-1",
        observers=None,
        compile_receipt={
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_OPERATION",
        },
    )

    assert experiment["observers"] == []
    assert "observer_subject_binding_receipt" not in experiment
    assert "observer_subject_binding_hash" not in experiment[
        "compile_receipt"
    ]


def test_main_compiler_imports_public_obligation_facade() -> None:
    # After the module split, the single-obligation semantic compiler lives in
    # ``experiment_compiler_obligation_core`` and the raw freeze mechanics moved
    # to ``_experiment_compiler_base_mechanics``. The mainline surface that must
    # import the public obligation facade (not the private core) is now
    # ``_experiment_compiler_base_mechanics``, which re-exports the facade's
    # symbols to ``experiment_compiler_base``.
    source = (
        ROOT / "ai_test_asset_center/_experiment_compiler_base_mechanics.py"
    ).read_text(encoding="utf-8")

    assert "from .experiment_compiler_obligation import" in source
    assert "from . import experiment_compiler_obligation_core" not in source


def test_no_mainline_module_imports_obligation_core_directly() -> None:
    # Only the public facade (``experiment_compiler_obligation``) may import the
    # private core. A plain substring scan is too naive: it also matches the
    # product SSOT comment in ``coverage_unit_registry`` and the fail-closed
    # deferred import (a legitimate circular-import workaround, evaluated at
    # call time inside a function) in ``experiment_protocols``. Inspect
    # module-scope ``import`` statements only, so function-local deferred
    # imports and prose comments do not read as layering violations.
    def _top_level_imports(module_name: str) -> set[str]:
        tree = ast.parse(
            (ROOT / f"ai_test_asset_center/{module_name}").read_text(encoding="utf-8")
        )
        imports: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                imports.update(alias.name for alias in node.names)
        return imports

    violations: list[str] = []
    for path in (ROOT / "ai_test_asset_center").glob("*.py"):
        if path.name in {
            "experiment_compiler_obligation.py",
            "experiment_compiler_obligation_core.py",
        }:
            continue
        if "experiment_compiler_obligation_core" in _top_level_imports(path.name):
            violations.append(path.name)

    assert violations == []

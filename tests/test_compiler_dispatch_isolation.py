from __future__ import annotations

from typing import Any

import ai_test_asset_center.experiment_compiler as experiment_compiler
import ai_test_asset_center.experiment_compiler_base as experiment_compiler_base
import ai_test_asset_center.obligation_compiler as obligation_compiler
import ai_test_asset_center.obligation_compiler_mainline_base as obligation_mainline
import ai_test_asset_center.obligation_compiler_privacy_pair_base as obligation_pair


def _empty_obligation_result(marker: str) -> dict[str, Any]:
    return {
        "schema_version": "qualibug.obligation-compile.v1",
        "behavior_ir_model_id": marker,
        "obligation_count": 0,
        "by_family": {},
        "obligations": [],
        "coverage_gaps": [],
    }


def test_obligation_facade_passes_baseline_compiler_explicitly(
    monkeypatch,
) -> None:
    observed: dict[str, Any] = {}

    def baseline(
        behavior_ir: dict[str, Any], *, root: str = "", project: str = ""
    ) -> dict[str, Any]:
        observed["baseline_input"] = behavior_ir
        return _empty_obligation_result("baseline")

    def pair_compile(
        behavior_ir: dict[str, Any],
        *,
        base_compile,
        root: str = "",
        project: str = "",
    ) -> dict[str, Any]:
        observed["injected"] = base_compile
        return base_compile(behavior_ir, root=root, project=project)

    # The baseline compiler is consumed by the mainline base, not by the
    # public facade directly; the pair layer resolves base_compile from the
    # mainline base and forwards it. Patch both real call sites.
    monkeypatch.setattr(
        obligation_mainline, "_original_compile", baseline
    )
    monkeypatch.setattr(
        obligation_pair, "compile_obligations_from_behavior_ir", pair_compile
    )
    behavior_ir = {"schema_version": "qualibug.behavior-ir.v1"}

    result = obligation_compiler.compile_obligations_from_behavior_ir(behavior_ir)

    assert observed["injected"] is baseline
    # The mainline base normalizes the IR (ownership-relation projection) before
    # delegating, so the baseline receives an equivalent copy, not the same object.
    assert observed["baseline_input"] == behavior_ir
    assert result["behavior_ir_model_id"] == "baseline"


def test_experiment_batch_dispatch_is_explicit_and_order_independent(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def facade_compile(
        obligation: dict[str, Any],
        **_: Any,
    ) -> dict[str, Any]:
        calls.append(f"facade:{obligation['obligation_id']}")
        return {
            "obligation_id": obligation["obligation_id"],
            "compile_receipt": {"status": "COMPILED", "reason_code": ""},
        }

    def poisoned_base(*_: Any, **__: Any) -> dict[str, Any]:
        raise AssertionError("facade batch leaked into base module dispatch")

    monkeypatch.setattr(
        experiment_compiler,
        "compile_experiment_for_obligation",
        facade_compile,
    )
    monkeypatch.setattr(
        experiment_compiler_base,
        "compile_experiment_for_obligation",
        poisoned_base,
    )
    obligation = {
        "obligation_id": "obl-explicit-dispatch",
        "property": {},
        "required_operations": [],
    }

    first = experiment_compiler.compile_experiments(
        [dict(obligation)],
        behavior_ir={"operations": []},
        environment_type="test",
    )
    second = experiment_compiler.compile_experiments(
        [dict(obligation)],
        behavior_ir={"operations": []},
        environment_type="test",
    )

    assert first["compiled_count"] == 1
    assert second["compiled_count"] == 1
    assert calls == [
        "facade:obl-explicit-dispatch",
        "facade:obl-explicit-dispatch",
    ]

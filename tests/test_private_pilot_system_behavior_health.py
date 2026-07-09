from pathlib import Path

from ai_test_asset_center.private_pilot_health_contract import build_private_pilot_health_payload


class _Handler:
    def __init__(self, root: Path) -> None:
        self._value = root

    def _root(self) -> Path:
        return self._value

    def _llm_health(self) -> dict:
        return {"available": True, "status": "online", "label": "online"}


def test_health_payload_reports_system_behavior_runtime_chain(tmp_path: Path) -> None:
    from ai_test_asset_center import business_state_graph as _bsg
    from ai_test_asset_center import oracle_engine as _oe
    from ai_test_asset_center import regression_runner as _rr
    from ai_test_asset_center import semantic_scenario_generator as _ssg
    from ai_test_asset_center import v12_pipeline as _v12

    old_values = {
        "bsg_patched": getattr(_bsg, "_SYSTEM_BEHAVIOR_SPACE_PATCHED", None),
        "bsg_source": getattr(_bsg, "_SYSTEM_BEHAVIOR_SPACE_PATCH_SOURCE", None),
        "v12_context": getattr(_v12, "_SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED", None),
        "v12_finding": getattr(_v12, "_SYSTEM_BEHAVIOR_FINDING_PATCHED", None),
        "v12_steering": getattr(_v12, "_COVERAGE_STEERING_PATCHED", None),
        "v12_steering_source": getattr(_v12, "_COVERAGE_STEERING_PATCH_SOURCE", None),
        "ssg": getattr(_ssg, "_SYSTEM_BEHAVIOR_SCENARIO_PATCHED", None),
        "oe": getattr(_oe, "_SYSTEM_BEHAVIOR_ORACLE_PATCHED", None),
        "rr": getattr(_rr, "_SYSTEM_BEHAVIOR_REGRESSION_PATCHED", None),
    }
    try:
        _bsg._SYSTEM_BEHAVIOR_SPACE_PATCHED = True  # type: ignore[attr-defined]
        _bsg._SYSTEM_BEHAVIOR_SPACE_PATCH_SOURCE = "test"  # type: ignore[attr-defined]
        _v12._SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED = True  # type: ignore[attr-defined]
        _v12._SYSTEM_BEHAVIOR_FINDING_PATCHED = True  # type: ignore[attr-defined]
        _v12._COVERAGE_STEERING_PATCHED = True  # type: ignore[attr-defined]
        _v12._COVERAGE_STEERING_PATCH_SOURCE = "test"  # type: ignore[attr-defined]
        _ssg._SYSTEM_BEHAVIOR_SCENARIO_PATCHED = True  # type: ignore[attr-defined]
        _oe._SYSTEM_BEHAVIOR_ORACLE_PATCHED = True  # type: ignore[attr-defined]
        _rr._SYSTEM_BEHAVIOR_REGRESSION_PATCHED = True  # type: ignore[attr-defined]

        payload = build_private_pilot_health_payload(_Handler(tmp_path), fallback_root=tmp_path, patch_source="test")
        runtime = payload["system_behavior_runtime"]

        assert runtime["ready"] is True
        assert runtime["mode"] == "system_promise_discovery_loop"
        assert runtime["checks"]["behavior_contract"] is True
        assert runtime["checks"]["scenario_runtime_hints"] is True
        assert runtime["checks"]["oracle_evidence_linkage"] is True
        assert runtime["checks"]["confirmed_finding_contract"] is True
        assert runtime["checks"]["regression_contract_replay"] is True
        assert runtime["checks"]["coverage_steering"] is True
        assert runtime["versions"]["system_behavior_space"] == "system_behavior_space.v1"
        assert runtime["versions"]["risk_clue_pool_project_learning"] == "risk_clue_pool_project_learning.v3"
        assert runtime["data_boundary"]["raw_customer_data_in_platform_learning"] is False
        assert "coverage_learning_steering" in runtime["chain"]
    finally:
        for module, name, key in [
            (_bsg, "_SYSTEM_BEHAVIOR_SPACE_PATCHED", "bsg_patched"),
            (_bsg, "_SYSTEM_BEHAVIOR_SPACE_PATCH_SOURCE", "bsg_source"),
            (_v12, "_SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED", "v12_context"),
            (_v12, "_SYSTEM_BEHAVIOR_FINDING_PATCHED", "v12_finding"),
            (_v12, "_COVERAGE_STEERING_PATCHED", "v12_steering"),
            (_v12, "_COVERAGE_STEERING_PATCH_SOURCE", "v12_steering_source"),
            (_ssg, "_SYSTEM_BEHAVIOR_SCENARIO_PATCHED", "ssg"),
            (_oe, "_SYSTEM_BEHAVIOR_ORACLE_PATCHED", "oe"),
            (_rr, "_SYSTEM_BEHAVIOR_REGRESSION_PATCHED", "rr"),
        ]:
            value = old_values[key]
            if value is None and hasattr(module, name):
                delattr(module, name)
            elif value is not None:
                setattr(module, name, value)

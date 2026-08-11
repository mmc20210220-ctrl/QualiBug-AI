from __future__ import annotations


def test_v150_registration_failure_is_visible_and_retryable(monkeypatch) -> None:
    from ai_test_asset_center import _experiment_protocols_mechanics as protocols
    from ai_test_asset_center import multi_step_protocol

    monkeypatch.setattr(protocols, "_v150_protocols_registered", False)
    monkeypatch.setattr(protocols, "_v150_protocol_registration_error", "")

    def _raise() -> list[str]:
        raise RuntimeError("synthetic bootstrap failure")

    monkeypatch.setattr(multi_step_protocol, "register_v150_multi_step_protocols", _raise)

    result = protocols.compile_family_protocol(
        risk_family="process",
        operation={"id": "op-1", "method": "POST", "path": "/process"},
        operation_ref="op-1",
        control_actor_ref="actor-1",
        treatment_actor_ref="actor-1",
        property_spec={"template": "multi_step_business_process"},
        behavior_ir={"operations": [], "actors": []},
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_REGISTERED_PROTOCOL_INVALID"
    assert "v150_protocol_registration_failed:RuntimeError" in result["detail"]
    assert protocols._v150_protocols_registered is False

    monkeypatch.setattr(
        multi_step_protocol,
        "register_v150_multi_step_protocols",
        lambda: [],
    )
    assert protocols._ensure_v150_protocols() == ""
    assert protocols._v150_protocols_registered is True
    assert protocols._v150_protocol_registration_error == ""


def test_v150_registration_failure_does_not_block_unrelated_builtin(monkeypatch) -> None:
    from ai_test_asset_center import _experiment_protocols_mechanics as protocols
    from ai_test_asset_center import multi_step_protocol

    monkeypatch.setattr(protocols, "_v150_protocols_registered", False)
    monkeypatch.setattr(protocols, "_v150_protocol_registration_error", "")
    monkeypatch.setattr(
        multi_step_protocol,
        "register_v150_multi_step_protocols",
        lambda: (_ for _ in ()).throw(RuntimeError("bootstrap unavailable")),
    )

    result = protocols.compile_family_protocol(
        risk_family="idempotency",
        operation={
            "id": "op-idem",
            "method": "POST",
            "path": "/items",
            "request_example": {"name": "declared"},
        },
        operation_ref="op-idem",
        control_actor_ref="actor-1",
        treatment_actor_ref="actor-1",
        property_spec={"template": "idempotent_effect_cardinality"},
        behavior_ir={"operations": [], "actors": []},
    )

    assert result["status"] == "COMPILED"

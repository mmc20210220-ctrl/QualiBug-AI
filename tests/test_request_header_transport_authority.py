from __future__ import annotations

from ai_test_asset_center.request_header_transport_authority import (
    build_step_header_contract,
    install_request_header_transport_authority,
)


def _operation(*, method: str = "GET", header: str = "Accept") -> dict:
    return {
        "id": "op_1",
        "method": method,
        "path": "/resource",
        "parameters": [
            {
                "name": header,
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
            }
        ],
    }


def _behavior_ir(*, anonymous: bool = False) -> dict:
    actor = {
        "id": "actor_1",
        "role": "anonymous" if anonymous else "buyer",
    }
    if not anonymous:
        actor["credential_secret_ref"] = "secret_ref:test_accounts:user@example.test"
    return {"actors": [actor], "operations": []}


def test_accept_is_real_transport_default() -> None:
    contract = build_step_header_contract(
        step={"step_id": "s1", "actor_ref": "actor_1"},
        operation=_operation(header="Accept"),
        experiment={},
        behavior_ir=_behavior_ir(anonymous=True),
    )
    assert contract["status"] == "DEFERRED_RUNTIME"
    assert contract["required"][0]["authority"] == "http_transport_default_accept_json"


def test_authorization_requires_exact_actor_credential_channel() -> None:
    anonymous = build_step_header_contract(
        step={"step_id": "s1", "actor_ref": "actor_1"},
        operation=_operation(header="Authorization"),
        experiment={},
        behavior_ir=_behavior_ir(anonymous=True),
    )
    assert anonymous["status"] == "BLOCKED"
    assert anonymous["required"][0]["reason_code"] == (
        "REQUEST_AUTHORIZATION_HEADER_CHANNEL_UNPROVEN"
    )

    authenticated = build_step_header_contract(
        step={"step_id": "s1", "actor_ref": "actor_1"},
        operation=_operation(header="Authorization"),
        experiment={},
        behavior_ir=_behavior_ir(),
    )
    assert authenticated["status"] == "DEFERRED_RUNTIME"
    assert authenticated["required"][0]["authority"] == "actor_declared_credential"


def test_content_type_requires_non_get_request_with_body_channel() -> None:
    no_body = build_step_header_contract(
        step={"step_id": "s1", "actor_ref": "actor_1"},
        operation=_operation(method="POST", header="Content-Type"),
        experiment={},
        behavior_ir=_behavior_ir(),
    )
    assert no_body["status"] == "BLOCKED"
    assert no_body["required"][0]["reason_code"] == (
        "REQUEST_CONTENT_TYPE_HEADER_CHANNEL_UNPROVEN"
    )

    with_body = build_step_header_contract(
        step={"step_id": "s1", "actor_ref": "actor_1", "body": {}},
        operation=_operation(method="POST", header="Content-Type"),
        experiment={},
        behavior_ir=_behavior_ir(),
    )
    assert with_body["status"] == "DEFERRED_RUNTIME"
    assert with_body["required"][0]["authority"].startswith(
        "json_body_transport:"
    )

    get_with_body_key = build_step_header_contract(
        step={"step_id": "s1", "actor_ref": "actor_1", "body": {}},
        operation=_operation(method="GET", header="Content-Type"),
        experiment={},
        behavior_ir=_behavior_ir(),
    )
    assert get_with_body_key["status"] == "BLOCKED"


def test_arbitrary_required_header_remains_blocked() -> None:
    contract = build_step_header_contract(
        step={"step_id": "s1", "actor_ref": "actor_1"},
        operation=_operation(header="X-Tenant-ID"),
        experiment={},
        behavior_ir=_behavior_ir(),
    )
    assert contract["status"] == "BLOCKED"
    assert contract["required"][0]["reason_code"] == (
        "REQUEST_REQUIRED_HEADER_TRANSPORT_UNSUPPORTED"
    )


def test_installed_builder_reseals_header_component_and_fingerprint() -> None:
    install_request_header_transport_authority()
    from ai_test_asset_center.request_build_contract import (
        STATUS_BLOCKED,
        build_request_build_contract,
    )

    operation = _operation(header="Authorization")
    behavior_ir = _behavior_ir(anonymous=True)
    behavior_ir["operations"] = [operation]
    experiment = {
        "experiment_id": "exp_header",
        "obligation_id": "obl_header",
        "control_plan": [
            {
                "step_id": "control_1",
                "actor_ref": "actor_1",
                "operation_ref": "op_1",
            }
        ],
    }
    contract = build_request_build_contract(
        experiment,
        behavior_ir=behavior_ir,
        flow_execution_contract={},
    )
    assert contract["status"] == STATUS_BLOCKED
    assert contract["contract_fingerprint"]
    header = next(
        component
        for component in contract["steps"][0]["components"]
        if component["component"] == "header"
    )
    assert header["required"][0]["reason_code"] == (
        "REQUEST_AUTHORIZATION_HEADER_CHANNEL_UNPROVEN"
    )

"""Session G: account-enumeration guard.

Covers the generic mechanism added in session G: identity-locator GET/HEAD
operations with NO declared permit/deny relation are anonymous-reachable by
definition, and an anonymous identity query must not return account
attributes (email/phone/status/role) — revealing existence AND attributes of
an account to an anonymous caller is the industry-universal
account-enumeration defect.

The guard flows through the existing response-side privacy field-policy
channel:

1. ``build_account_enumeration_guard_obligations`` derives single-arm privacy
   guard obligations from structure only (read method + undeclared permission
   matrix + identity-locator parameter + generic account-attribute rule
   text), and ensures an ``anonymous`` actor exists in the IR actor pool.
2. Compilation resolves the treatment actor to ``anonymous`` with an empty
   credential binding — the executor sends the GET without an Authorization
   header, which is exactly what "anonymous reachable" means.
3. The ``privacy_field_policy`` assertion (absent policy + match_field_names)
   scans response field names recursively; a compliant surface (boolean-only
   existence answer) passes, a surface leaking account attributes violates.
"""
from __future__ import annotations

from ai_test_asset_center.account_enumeration_guard import (
    GUARD_SOURCE_KIND,
    build_account_enumeration_guard_obligations,
)
from ai_test_asset_center.experiment_compiler_obligation import (
    compile_experiment_for_obligation,
)
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir


def _relation(
    relation_id: str,
    relation_type: str,
    from_ref: str,
    to_ref: str,
    *,
    operation_ref: str = "",
    actor_ref: str = "",
) -> dict:
    return {
        "id": relation_id,
        "relation_type": relation_type,
        "from_ref": from_ref,
        "to_ref": to_ref,
        "operation_ref": operation_ref or to_ref,
        "actor_ref": actor_ref or from_ref,
        "preconditions": [],
        "effects": [],
        "status": "accepted",
        "confidence": 0.9,
        "source_refs": [{"source_id": "test", "locator": relation_id, "kind": "relation"}],
    }


def _operation(
    operation_id: str,
    method: str,
    path: str,
    *,
    summary: str = "",
    parameters: list[dict] | None = None,
) -> dict:
    return {
        "id": operation_id,
        "method": method,
        "path": path,
        "raw_path": path,
        "read_write": "read" if method in {"GET", "HEAD"} else "write",
        "summary": summary,
        "parameters": parameters or [],
        "source_refs": [
            {"source_id": "api_spec", "locator": f"{method} {path}", "kind": "api_operation"}
        ],
    }


def _guard_ir() -> dict:
    """IR mirroring the run6 user module: anonymous phone check + authenticated profile."""
    return {
        "schema_version": "qualibug.behavior-ir.v2",
        "project_id": "user009-guard",
        "asset_id": "user009-guard",
        "model_id": "user009-guard-model",
        "actors": [
            {"id": "admin", "name": "admin", "role": "admin", "credential_secret_ref": "sec-admin"},
            {"id": "member", "name": "member", "role": "member", "credential_secret_ref": "sec-member"},
        ],
        "operations": [
            _operation(
                "op-phone-check",
                "GET",
                "/api/auth/phone/check",
                summary="匿名手机号查询",
                parameters=[
                    {"name": "phone", "in": "query", "required": True,
                     "schema": {"type": "string"}}
                ],
            ),
            _operation(
                "op-profile",
                "GET",
                "/api/users/me",
                summary="个人资料",
            ),
        ],
        "relations": [
            _relation(
                "rel-profile-member",
                "permits",
                "actor:member",
                "operation:op-profile",
                operation_ref="op-profile",
                actor_ref="member",
            ),
        ],
        "invariants": [],
        "sources": [{"id": "src-api", "source_id": "api_spec", "kind": "api_spec"}],
        "entities": [],
        "states": [],
        "observation_surfaces": [],
        "capabilities": [],
        "conflicts": [],
        "coverage_gaps": [],
    }


def test_guard_generates_for_anonymous_identity_locator_read() -> None:
    ir = _guard_ir()
    obligations = build_account_enumeration_guard_obligations(ir)
    by_op = {ob["property"]["operation_ref"]: ob for ob in obligations}
    assert by_op.keys() == {"op-phone-check"}
    guard = by_op["op-phone-check"]
    assert guard["risk_family"] == "privacy"
    assert guard["required_actors"] == ["anonymous"]
    prop = guard["property"]
    assert prop["template"] == "account_enumeration_guard"
    assert prop["privacy_test_mode"] == "field_policy"
    assert prop["privacy_policy"] == "absent"
    assert prop["privacy_field_source"] == GUARD_SOURCE_KIND
    assert prop["match_field_names"] is True
    assert set(prop["field_tokens"]) == {"email", "phone", "mobile", "status", "role"}
    assert "匿名身份查询" in prop["expression"]["raw"]
    assert "不得返回账号属性" in prop["expression"]["raw"]


def test_guard_skips_authenticated_operations() -> None:
    ir = _guard_ir()
    obligations = build_account_enumeration_guard_obligations(ir)
    # op-profile carries a permits relation → not anonymous-reachable → no guard.
    assert "op-profile" not in {ob["property"]["operation_ref"] for ob in obligations}


def test_guard_injects_anonymous_actor_idempotently() -> None:
    ir = _guard_ir()
    build_account_enumeration_guard_obligations(ir)
    anonymous = [a for a in ir["actors"] if a.get("id") == "anonymous"]
    assert len(anonymous) == 1
    assert anonymous[0]["role"] == "anonymous"
    assert anonymous[0]["credential_secret_ref"] == ""
    # Second call must not duplicate the actor.
    build_account_enumeration_guard_obligations(ir)
    anonymous = [a for a in ir["actors"] if a.get("id") == "anonymous"]
    assert len(anonymous) == 1


def test_guard_compiles_with_anonymous_treatment_actor() -> None:
    ir = _guard_ir()
    obligations = build_account_enumeration_guard_obligations(ir)
    guard = obligations[0]
    result = compile_experiment_for_obligation(
        guard,
        behavior_ir=ir,
        environment_type="test",
    )
    assert result["compile_receipt"]["status"] == "COMPILED", result.get("compile_receipt")
    experiment = result
    treatment = experiment["treatment_plan"]
    assert len(treatment) == 1
    assert treatment[0]["actor_ref"] == "anonymous"
    assert treatment[0]["operation_ref"] == "op-phone-check"
    binding = next(
        row for row in experiment["binding_plan"]
        if row.get("target") == "actor:anonymous"
    )
    assert binding["secret_ref"] == ""
    assertions = experiment["assertions"]
    assert len(assertions) == 1
    assertion = assertions[0]
    assert assertion["kind"] == "privacy_field_policy"
    assert assertion["property"]["privacy_policy"] == "absent"
    assert assertion["property"]["match_field_names"] is True
    assert set(assertion["property"]["field_tokens"]) == {
        "email", "phone", "mobile", "status", "role"
    }


def test_guard_obligation_survives_pairing() -> None:
    """The guard must stay single-arm through the privacy pairing layer —
    anonymous has no permit/deny pair to dualize with."""
    ir = _guard_ir()
    pack = compile_obligations_from_behavior_ir(ir, root=".", project="user009-guard")
    all_obligations = list(pack.get("obligations") or [])
    guard_obligations = build_account_enumeration_guard_obligations(ir)
    all_obligations.extend(guard_obligations)
    guard = next(
        ob for ob in all_obligations
        if ob["property"].get("privacy_field_source") == GUARD_SOURCE_KIND
    )
    # Response-side privacy obligations are preserved single-arm by pairing.
    assert guard["required_actors"] == ["anonymous"]
    assert guard["property"]["privacy_test_mode"] == "field_policy"
    assert guard["property"]["privacy_policy"] == "absent"
    assert "control_actor_ref" not in guard["property"]


def test_guard_excludes_posts_and_parameterless_gets() -> None:
    ir = _guard_ir()
    ir["operations"].append(
        _operation(
            "op-phone-register",
            "POST",
            "/api/auth/phone/register",
            summary="手机号注册",
            parameters=[{"name": "phone", "in": "body", "required": True}],
        )
    )
    ir["operations"].append(
        _operation(
            "op-status",
            "GET",
            "/api/status",
            summary="健康检查",
        )
    )
    obligations = build_account_enumeration_guard_obligations(ir)
    # POST (write) and GET without an identity-locator parameter are not guards.
    assert {ob["property"]["operation_ref"] for ob in obligations} == {"op-phone-check"}


def test_guard_assertion_violates_on_attribute_leaking_anonymous_response() -> None:
    """Real-target observation shape (run6 probe): the anonymous phone/check
    response carries email/status/role — the guard's absent policy must
    VIOLATE. A compliant boolean-only existence answer must PASS."""
    from ai_test_asset_center._assertion_dsl_privacy_mechanics import (
        evaluate_assertion as _privacy_evaluate,
    )

    guard_assertion = {
        "kind": "privacy_field_policy",
        "privacy_policy": "absent",
        "field_tokens": ["email", "phone", "mobile", "status", "role"],
        "match_field_names": True,
    }
    leaking = _privacy_evaluate(
        guard_assertion,
        observations={
            "status_code": 200,
            "body": {"exists": True, "user": {"email": "a@b.c", "status": "active", "role": "member"}},
        },
        source_refs=[{"source_id": "api_spec", "locator": "GET /api/auth/phone/check", "kind": "api_operation"}],
    )
    assert leaking["status"] == "VIOLATION", leaking.get("reason_code")
    assert leaking.get("reason_code") == "PRIVACY_FORBIDDEN_FIELD_EXPOSED"
    compliant = _privacy_evaluate(
        guard_assertion,
        observations={
            "status_code": 200,
            "body": {"exists": True},
        },
        source_refs=[{"source_id": "api_spec", "locator": "GET /api/auth/phone/check", "kind": "api_operation"}],
    )
    assert compliant["status"] == "PASS", compliant.get("reason_code")

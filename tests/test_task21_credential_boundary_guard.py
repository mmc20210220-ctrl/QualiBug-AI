"""Task 21: credential-boundary guard — auth/backdoor obligation chain.

Covers the generic mechanisms added in task 21:

1. Public-access declaration authority (``behavior_ir_core``): an operation
   whose own contract declares PUBLIC access (权限：公开) is anonymous-reachable
   by that declaration; wildcard permission-matrix grants from other roles
   (admin | 所有权限) must not mask it. Without this, public-but-gated
   surfaces (password reset, verification-code login) compile zero
   obligations because every anonymous-reachability guard sees a permits
   relation and skips them.
2. Arm A of the credential-boundary guard: a ROLE-DECLARED credential
   surface (token/password/login/impersonate/debug-sign …) must reject
   anonymous callers — an accepted anonymous call lets anyone mint /
   impersonate / change credentials (backdoor shape).
3. Arm B of the credential-boundary guard: a declared verification-code
   login must actually verify the code — a wrong code must be rejected
   (any-code-login weakness). Flows through the validation channel's
   semantic invalid-value machinery.

All mechanisms are structure/contract-text driven — no endpoint names, no
industry terms, no benchmark data.
"""
from __future__ import annotations

from ai_test_asset_center.behavior_ir_core import (
    _derive_permission_relations,
    _operation_declares_public_access,
)
from ai_test_asset_center.credential_boundary_guard import (
    GUARD_SOURCE_KIND,
    build_credential_boundary_guard_obligations,
)
from ai_test_asset_center.credential_gated_write_guard import (
    build_credential_gated_write_guard_obligations,
)
from ai_test_asset_center.experiment_compiler_obligation import (
    compile_experiment_for_obligation,
)


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
    description: str = "",
    request_schema: dict | None = None,
    request_example: dict | None = None,
    source_refs: list[dict] | None = None,
) -> dict:
    op = {
        "id": operation_id,
        "method": method,
        "path": path,
        "raw_path": path,
        "read_write": "read" if method in {"GET", "HEAD"} else "write",
        "summary": summary,
        "description": description,
        "request_schema": request_schema or {},
        "request_example": request_example or {},
        "source_refs": source_refs
        or [
            {"source_id": "api_spec", "locator": f"{method} {path}", "kind": "api_operation"}
        ],
    }
    return op


def _ir(
    operations: list[dict],
    relations: list[dict],
    actors: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": "qualibug.behavior-ir.v2",
        "project_id": "task21-guard",
        "asset_id": "task21-guard",
        "model_id": "task21-guard-model",
        "actors": actors
        or [
            {"id": "admin", "name": "admin", "role": "admin", "credential_secret_ref": "sec-admin"},
            {"id": "buyer", "name": "buyer", "role": "buyer", "credential_secret_ref": "sec-buyer"},
        ],
        "operations": operations,
        "relations": relations,
        "invariants": [],
        "sources": [{"id": "src-api", "source_id": "api_spec", "kind": "api_spec"}],
        "entities": [],
        "states": [],
        "observation_surfaces": [],
        "capabilities": [],
        "conflicts": [],
        "coverage_gaps": [],
    }


# ──────────────────────────────────────────────────────────────────────────
# 1. Public-access declaration authority (RC1)
# ──────────────────────────────────────────────────────────────────────────

def test_public_access_declaration_detected() -> None:
    public = _operation("op-reset", "POST", "/api/auth/password/reset", description="权限：公开。\n\n业务约束：必须完成验证码或等价身份校验；不得仅凭邮箱重置。")
    assert _operation_declares_public_access(public) is True
    restricted = _operation("op-debug", "POST", "/api/auth/debug/token", description="权限：管理员。\n\n业务约束：仅本地测试环境开放，生产环境必须关闭。")
    assert _operation_declares_public_access(restricted) is False


def test_public_operation_not_masked_by_wildcard_admin_grant() -> None:
    """A public op must not receive permits from an admin | * row."""
    operations = [
        _operation("op-reset", "POST", "/api/auth/password/reset", description="权限：公开。"),
        _operation("op-debug", "POST", "/api/auth/debug/token", description="权限：管理员。"),
    ]
    model = _ir(operations, [])
    # admin | 所有权限 wildcard row (USER_ROLES.md shape, normalized to "*").
    rows = [{
        "permission_id": "perm:test:all",
        "source_id": "test",
        "role": "admin",
        "resource": "*",
        "actions": ["*"],
        "decision": "allow",
        "scope": "all",
        "evidence": "admin 所有权限",
    }]
    relations = _derive_permission_relations(model, rows)
    reset_permits = [
        r for r in relations
        if r.get("operation_ref") == "op-reset"
        and r.get("relation_type") == "permits"
    ]
    debug_permits = [
        r for r in relations
        if r.get("operation_ref") == "op-debug"
        and r.get("relation_type") == "permits"
    ]
    assert reset_permits == [], "public op must stay anonymous-reachable"
    assert debug_permits, "role-declared op keeps its matrix grants"


def test_password_reset_now_compiles_credential_gated_guard() -> None:
    """AUTH-002 chain: public + verification contract + email locator →
    anonymous-write-rejection obligation (previously zero obligations)."""
    op = _operation(
        "op-reset",
        "POST",
        "/api/auth/password/reset",
        description="权限：公开。\n\n业务约束：必须完成验证码或等价身份校验；不得仅凭邮箱重置。",
        request_schema={
            "type": "object",
            "properties": {
                "email": {"type": "string", "format": "email", "example": "buyer01@example.com"},
                "newPassword": {"type": "string", "example": "NewTest@123456"},
            },
            "required": ["email", "newPassword"],
        },
        request_example={"email": "buyer01@example.com", "newPassword": "NewTest@123456"},
    )
    # A write authorization obligation gains a business_effect observer; the
    # observer resolves a GET identity read from the IR (same as the runtime
    # environment, where the account module declares /me-style reads).
    me_read = _operation(
        "op-me",
        "GET",
        "/api/auth/me",
        summary="当前用户信息",
        description="权限：登录用户。",
    )
    ir = _ir([op, me_read], [])  # no permits → anonymous-reachable (post-RC1 semantics)
    guards = build_credential_gated_write_guard_obligations(ir)
    by_op = {g["property"]["operation_ref"]: g for g in guards}
    assert "op-reset" in by_op
    guard = by_op["op-reset"]
    assert guard["risk_family"] == "authorization"
    assert guard["required_actors"] == ["anonymous"]
    assert guard["property"]["template"] == "credential_gated_write"
    assert guard["property"]["credential_gate"] is True
    result = compile_experiment_for_obligation(
        guard,
        behavior_ir=ir,
        environment_type="test",
        available_adapters={"http_api"},
    )
    assert result["compile_receipt"]["status"] == "COMPILED", result.get("compile_receipt")


# ──────────────────────────────────────────────────────────────────────────
# 2. Guard Arm A — role-declared credential surfaces reject anonymous callers
# ──────────────────────────────────────────────────────────────────────────

def _role_declared_credential_surface() -> tuple[dict, dict]:
    op = _operation(
        "op-debug-token",
        "POST",
        "/api/auth/debug/token",
        summary="测试环境签发调试令牌",
        description="权限：管理员。\n\n业务约束：仅本地测试环境开放，生产环境必须关闭。",
        request_schema={
            "type": "object",
            "properties": {
                "email": {"type": "string", "format": "email", "example": "buyer01@example.com"},
                "role": {"type": "string", "example": "buyer"},
            },
        },
        request_example={"email": "buyer01@example.com", "role": "buyer"},
    )
    ir = _ir([op], [
        _relation("rel-permit-admin", "permits", "admin", "operation:op-debug-token",
                  operation_ref="op-debug-token", actor_ref="admin"),
        _relation("rel-deny-buyer", "denies", "buyer", "operation:op-debug-token",
                  operation_ref="op-debug-token", actor_ref="buyer"),
    ])
    return op, ir


def test_arm_a_generates_anonymous_rejection_for_role_declared_credential_surface() -> None:
    op, ir = _role_declared_credential_surface()
    obligations = build_credential_boundary_guard_obligations(ir)
    by_op = {ob["property"]["operation_ref"]: ob for ob in obligations}
    assert "op-debug-token" in by_op
    guard = by_op["op-debug-token"]
    assert guard["risk_family"] == "authorization"
    assert guard["required_actors"] == ["anonymous"]
    prop = guard["property"]
    assert prop["template"] == "credential_gated_write"
    assert prop["credential_gate"] is True
    assert prop["credential_boundary"] == "role_declared_anonymous_rejection"
    assert prop["identity_locator_field"] == "email"
    assert "匿名调用" in prop["expression"]["raw"]


def test_arm_a_compiles() -> None:
    op, ir = _role_declared_credential_surface()
    guard = build_credential_boundary_guard_obligations(ir)[0]
    result = compile_experiment_for_obligation(
        guard, behavior_ir=ir, environment_type="test"
    )
    assert result["compile_receipt"]["status"] == "COMPILED", result.get("compile_receipt")


def test_arm_a_skips_non_credential_role_declared_surface() -> None:
    op = _operation(
        "op-product-admin",
        "POST",
        "/api/products/admin",
        summary="创建商品",
        description="权限：商家本人或管理员。",
    )
    ir = _ir([op], [
        _relation("rel-permit-admin", "permits", "admin", "operation:op-product-admin",
                  operation_ref="op-product-admin", actor_ref="admin"),
    ])
    obligations = build_credential_boundary_guard_obligations(ir)
    # products/admin is a role-declared WRITE but NOT a credential surface —
    # no Arm A obligation (the anonymous-rejection arm is auth-surface scoped).
    assert all(ob["property"]["operation_ref"] != "op-product-admin" for ob in obligations)


def test_arm_a_skips_public_credential_surface() -> None:
    """A public credential surface is covered by the credential-gated write
    guard (Arm A requires a ROLE declaration — permits present)."""
    op = _operation(
        "op-reset",
        "POST",
        "/api/auth/password/reset",
        description="权限：公开。",
    )
    ir = _ir([op], [])
    obligations = build_credential_boundary_guard_obligations(ir)
    assert all(ob["property"]["operation_ref"] != "op-reset" for ob in obligations)


# ──────────────────────────────────────────────────────────────────────────
# 3. Guard Arm B — verification-code login must actually verify the code
# ──────────────────────────────────────────────────────────────────────────

def _verification_login_surface() -> tuple[dict, dict]:
    op = _operation(
        "op-login-phone",
        "POST",
        "/api/auth/login/phone",
        summary="手机号验证码登录",
        description="权限：公开。",
        request_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "验证码", "example": "NEW100"},
                "phone": {"type": "string", "description": "手机号", "example": "13900000000"},
            },
            "required": ["code", "phone"],
        },
        request_example={"code": "NEW100", "phone": "13900000000"},
    )
    ir = _ir([op], [])
    return op, ir


def test_arm_b_generates_verification_must_verify_for_verification_login() -> None:
    op, ir = _verification_login_surface()
    obligations = build_credential_boundary_guard_obligations(ir)
    by_op = {ob["property"]["operation_ref"]: ob for ob in obligations}
    assert "op-login-phone" in by_op
    guard = by_op["op-login-phone"]
    assert guard["risk_family"] == "validation"
    assert guard["required_actors"] == ["anonymous"]
    prop = guard["property"]
    assert prop["field"] == "code"
    assert prop["field_tokens"] == ["code"]
    assert prop["expected_rejection_status_class"] == 4
    assert prop["credential_boundary"] == "verification_code_must_be_verified"
    assert "未签发验证码" in prop["expression"]["raw"]


def test_arm_b_compiles_with_wrong_code_treatment() -> None:
    op, ir = _verification_login_surface()
    guard = build_credential_boundary_guard_obligations(ir)[0]
    prop = guard["property"]
    assert prop["validation_constraint"] == "verification_code_mismatch"
    assert prop["validation_constraint_value"] == "000000"
    result = compile_experiment_for_obligation(
        guard, behavior_ir=ir, environment_type="test"
    )
    assert result["compile_receipt"]["status"] == "COMPILED", result.get("compile_receipt")
    # The treatment must carry the deterministic wrong code (000000) — the
    # "code never issued" shape that a length-only verification would accept.
    treatment_bodies = [
        step.get("body")
        for step in (result.get("treatment_plan") or [])
        if isinstance(step, dict)
    ]
    assert treatment_bodies, "no treatment step compiled"
    assert treatment_bodies[0].get("code") == "000000", treatment_bodies[0]
    # The phone field must stay untouched — only the verification value mutates.
    assert treatment_bodies[0].get("phone") == "13900000000"


def test_arm_b_skips_non_verification_code_surface() -> None:
    """A coupon code-validation operation is not a verification-code login."""
    op = _operation(
        "op-coupon-validate",
        "POST",
        "/api/coupons/validate",
        summary="校验优惠券码",
        description="权限：公开。",
        request_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "example": "DISCOUNT10"},
            },
        },
        request_example={"code": "DISCOUNT10"},
    )
    ir = _ir([op], [])
    obligations = build_credential_boundary_guard_obligations(ir)
    assert all(ob["property"]["operation_ref"] != "op-coupon-validate" for ob in obligations)


# ──────────────────────────────────────────────────────────────────────────
# 4. Stability / determinism
# ──────────────────────────────────────────────────────────────────────────

def test_guard_ids_are_deterministic_and_deduplicated() -> None:
    op, ir = _verification_login_surface()
    first = build_credential_boundary_guard_obligations(ir)
    second = build_credential_boundary_guard_obligations(ir)
    assert [ob["obligation_id"] for ob in first] == [ob["obligation_id"] for ob in second]
    ids = [ob["obligation_id"] for ob in first]
    assert len(ids) == len(set(ids))


def test_guard_injects_anonymous_actor_idempotently() -> None:
    op, ir = _verification_login_surface()
    build_credential_boundary_guard_obligations(ir)
    anonymous = [a for a in ir["actors"] if a.get("id") == "anonymous"]
    assert len(anonymous) == 1
    assert anonymous[0]["role"] == "anonymous"
    assert anonymous[0]["credential_secret_ref"] == ""
    build_credential_boundary_guard_obligations(ir)
    anonymous = [a for a in ir["actors"] if a.get("id") == "anonymous"]
    assert len(anonymous) == 1

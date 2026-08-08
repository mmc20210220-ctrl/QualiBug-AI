# -*- coding: utf-8 -*-
"""Task 3 unit tests: parameter/trigger-shape mechanisms.

Covers:
1. credential_gated_write_guard: anonymous verification-gated write surfaces
   (password-reset email-locator shape, callback success-state shape) generate
   single-arm authorization obligations; non-gated / role-declared / read ops
   are excluded.
2. compile_family_protocol credential_gated_write template: treatment body
   aims the identity locator at a runtime account, callback status carries the
   success literal, assertion expects 4xx.
3. obligation_compiler write-side own-scope owns derivation (cart-merge
   cross-user shape) -> isolation obligations compile.
4. _NUMERIC_NEGATIVE_FIELDS percent vocabulary: deltaPercent gets a negative
   mutation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
sys.path.insert(0, str(REPO))

import pytest

from ai_test_asset_center.credential_gated_write_guard import (
    build_credential_gated_write_guard_obligations,
)
from ai_test_asset_center.experiment_protocols_base import (
    _NUMERIC_NEGATIVE_FIELDS,
    _semantic_invalid_value,
    compile_family_protocol,
)
from ai_test_asset_center.behavior_ir import empty_behavior_ir
from ai_test_asset_center.obligation_compiler import (
    compile_obligations_from_behavior_ir,
)
from ai_test_asset_center.experiment_compiler_obligation import (
    compile_experiment_for_obligation,
)


def _op(
    path: str,
    method: str = "POST",
    *,
    summary: str = "",
    description: str = "",
    body_props: dict | None = None,
    example: dict | None = None,
) -> dict:
    return {
        "id": f"op_{method.lower()}_{path.replace('/','_').replace('{','_').replace('}','')}",
        "method": method.upper(),
        "path": path,
        "raw_path": path,
        "read_write": "write" if method.upper() != "GET" else "read",
        "summary": summary,
        "description": description,
        "parameters": [],
        "request_schema": {
            "type": "object",
            "required": list((body_props or {}).keys()),
            "properties": dict(body_props or {}),
        },
        "request_example": dict(example or {}),
        "source_refs": [{
            "source_id": "api_spec",
            "locator": f"{method.upper()} {path}",
            "kind": "api_operation",
        }],
    }


def _actors() -> list[dict]:
    return [
        {"id": "actor-buyer-a", "role": "buyer", "account_ref": "buyer01@example.com", "account_id": "id1", "account_status": "active", "credential_secret_ref": "secret_ref:test_accounts:buyer01"},
        {"id": "actor-buyer-b", "role": "buyer", "account_ref": "buyer02@example.com", "account_id": "id2", "account_status": "active", "credential_secret_ref": "secret_ref:test_accounts:buyer02"},
        {"id": "actor-admin", "role": "admin", "account_ref": "admin@example.com", "account_id": "id3", "account_status": "active", "credential_secret_ref": "secret_ref:test_accounts:admin"},
    ]


# ── 1) guard obligation generation ──

def test_guard_generates_password_reset_obligation():
    reset = _op(
        "/api/auth/password/reset",
        summary="重置登录密码",
        description="权限：公开。业务约束：必须完成验证码或等价身份校验；不得仅凭邮箱重置。",
        body_props={
            "email": {"type": "string", "example": "buyer01@example.com"},
            "newPassword": {"type": "string", "example": "NewTest@123456"},
        },
        example={"email": "buyer01@example.com", "newPassword": "NewTest@123456"},
    )
    ir = empty_behavior_ir(project_id="t3")
    ir["operations"] = [reset]
    ir["actors"] = _actors()
    ir["relations"] = []
    guards = build_credential_gated_write_guard_obligations(ir)
    assert len(guards) == 1
    g = guards[0]
    assert g["risk_family"] == "authorization"
    assert g["required_actors"] == ["anonymous"]
    assert g["property"]["template"] == "credential_gated_write"
    assert g["property"]["identity_locator_field"] == "email"


def test_guard_generates_callback_obligation_without_locator():
    cb = _op(
        "/api/payments/callback/mock",
        summary="模拟支付渠道回调",
        description="权限：支付渠道回调（需签名校验）。业务约束：仅测试支付渠道使用；必须校验渠道签名并保证回调幂等。",
        body_props={
            "amount": {"type": "number", "example": 6899},
            "orderId": {"type": "string", "example": "order-1"},
            "status": {"type": "string", "example": "ACTIVE"},
        },
        example={"amount": 6899, "orderId": "order-1", "status": "ACTIVE"},
    )
    ir = empty_behavior_ir(project_id="t3")
    ir["operations"] = [cb]
    ir["actors"] = _actors()
    ir["relations"] = []
    guards = build_credential_gated_write_guard_obligations(ir)
    assert len(guards) == 1
    assert guards[0]["property"]["callback_surface"] is True


def test_guard_excludes_role_declared_operation():
    login = _op(
        "/api/auth/login",
        summary="登录",
        description="账号密码登录",
        body_props={"email": {"type": "string"}, "password": {"type": "string"}},
        example={"email": "buyer01@example.com", "password": "x"},
    )
    # a permits relation declares the operation role-restricted
    rel = {
        "id": "rel-permit", "relation_type": "permits", "from_ref": "actor-buyer-a",
        "to_ref": login["id"], "operation_ref": login["id"], "actor_ref": "actor-buyer-a",
        "preconditions": [], "effects": [], "permission_decision": "allow",
        "source_relationship_ref": "", "source_refs": [], "confidence": 0.9,
        "derivation": "explicit", "status": "accepted",
    }
    ir = empty_behavior_ir(project_id="t3")
    ir["operations"] = [login]
    ir["actors"] = _actors()
    ir["relations"] = [rel]
    guards = build_credential_gated_write_guard_obligations(ir)
    assert guards == []


def test_guard_excludes_plain_write_without_verification_contract():
    op = _op(
        "/api/cart/items",
        summary="加入购物车",
        description="登录用户添加商品到购物车",
        body_props={"sku": {"type": "string"}, "qty": {"type": "integer"}},
        example={"sku": "S1", "qty": 1},
    )
    ir = empty_behavior_ir(project_id="t3")
    ir["operations"] = [op]
    ir["actors"] = _actors()
    ir["relations"] = []
    guards = build_credential_gated_write_guard_obligations(ir)
    assert guards == []


def test_guard_excludes_read_operations():
    me = _op(
        "/api/auth/me", method="GET",
        summary="当前用户",
        description="必须完成身份校验后返回当前账号",
    )
    ir = empty_behavior_ir(project_id="t3")
    ir["operations"] = [me]
    ir["actors"] = _actors()
    ir["relations"] = []
    guards = build_credential_gated_write_guard_obligations(ir)
    assert guards == []


# ── 2) credential_gated_write protocol template ──

def test_credential_gated_write_protocol_aims_real_account_and_success_state():
    reset = _op(
        "/api/auth/password/reset",
        summary="重置登录密码",
        description="必须完成验证码或等价身份校验",
        body_props={
            "email": {"type": "string", "example": "buyer01@example.com"},
            "newPassword": {"type": "string", "example": "NewTest@123456"},
        },
        example={"email": "buyer01@example.com", "newPassword": "NewTest@123456"},
    )
    cb = _op(
        "/api/payments/callback/mock",
        summary="模拟支付渠道回调",
        description="必须校验渠道签名",
        body_props={
            "amount": {"type": "number", "example": 6899},
            "orderId": {"type": "string", "example": "order-1"},
            "status": {"type": "string", "example": "ACTIVE"},
        },
        example={"amount": 6899, "orderId": "order-1", "status": "ACTIVE"},
    )
    ir = empty_behavior_ir(project_id="t3")
    ir["operations"] = [reset, cb]
    ir["actors"] = _actors()
    ir["relations"] = []

    proto = compile_family_protocol(
        risk_family="authorization",
        operation=reset,
        operation_ref=reset["id"],
        control_actor_ref="anonymous",
        treatment_actor_ref="anonymous",
        property_spec={
            "template": "credential_gated_write",
            "identity_locator_field": "email",
            "description": "未验证写请求必须被拒绝",
        },
        behavior_ir=ir,
    )
    assert proto["status"] == "COMPILED"
    assert proto["control_plan"] == []
    step = proto["treatment_plan"][0]
    assert step["actor_ref"] == "anonymous"
    # identity locator aimed at a REAL runtime account (never synthesized)
    assert step["body"]["email"] in {"buyer01@example.com", "buyer02@example.com", "admin@example.com"}
    assert proto["assertion"]["kind"] == "http_status_class"
    assert proto["assertion"]["expected_class"] == 4

    proto_cb = compile_family_protocol(
        risk_family="authorization",
        operation=cb,
        operation_ref=cb["id"],
        control_actor_ref="anonymous",
        treatment_actor_ref="anonymous",
        property_spec={
            "template": "credential_gated_write",
            "callback_surface": True,
            "description": "未验证写请求必须被拒绝",
        },
        behavior_ir=ir,
    )
    assert proto_cb["status"] == "COMPILED"
    # callback status carries the success literal (forged-success shape)
    assert proto_cb["treatment_plan"][0]["body"]["status"] == "SUCCESS"


# ── 3) write-side own-scope owns derivation (cart merge) ──

def _merge_ir() -> dict:
    merge = _op(
        "/api/cart/merge",
        summary="合并购物车",
        description="权限：登录用户，仅限本人数据。业务约束：只能合并当前登录用户的匿名/临时购物车，禁止跨用户转移。",
        body_props={
            "fromUserId": {"type": "string", "example": "id1"},
            "toUserId": {"type": "string", "example": "id2"},
        },
        example={"fromUserId": "id1", "toUserId": "id2"},
    )
    cart_get = _op("/api/cart/items", method="GET", summary="购物车列表")
    ir = empty_behavior_ir(project_id="t3")
    ir["operations"] = [merge, cart_get]
    ir["actors"] = _actors()
    ir["entities"] = [
        {"id": "ent-carts", "name": "carts", "fields": ["id", "user_id"], "status": "accepted"},
    ]
    ir["relations"] = [{
        "id": "rel-obs", "relation_type": "observes", "from_ref": cart_get["id"],
        "to_ref": "ent-carts", "operation_ref": cart_get["id"], "actor_ref": "",
        "preconditions": [], "effects": [], "permission_decision": "",
        "source_relationship_ref": "", "source_refs": [], "confidence": 0.9,
        "derivation": "explicit", "status": "accepted",
    }]
    return ir


def test_write_own_scope_derives_owns_for_merge():
    ir = _merge_ir()
    merge = next(o for o in ir["operations"] if o["path"] == "/api/cart/merge")
    owns = [
        r for r in ir["relations"]
        if r["relation_type"] == "owns" and r["operation_ref"] == merge["id"]
    ]
    # before compilation the write has no owns
    assert owns == []
    pack = compile_obligations_from_behavior_ir(
        ir, root=str(REPO), project="benchmark_mall"
    )
    obligations = pack.get("obligations") or []
    merge_obls = [
        o for o in obligations
        if merge["id"] in [str(x) for x in (o.get("required_operations") or [])]
    ]
    isolation = [o for o in merge_obls if o.get("risk_family") == "isolation"]
    assert isolation, "merge write own-scope must produce isolation obligations"
    prop = isolation[0].get("property") or {}
    assert prop.get("ownership_param") == "fromUserId"
    # the isolation experiment must compile (two-arm owner/viewer)
    exp = compile_experiment_for_obligation(
        isolation[0], behavior_ir=ir, environment_type="test",
        available_adapters={"http_api"},
    )
    assert exp.get("compile_receipt", {}).get("status") == "COMPILED", exp.get("compile_receipt")
    # the treatment arm must carry the cross-user identity placeholder on fromUserId
    for step in exp.get("treatment_plan") or []:
        body = step.get("body") or {}
        if body.get("fromUserId") is not None:
            assert body["fromUserId"] != body.get("toUserId") or "{" in str(body["fromUserId"])
            break
    else:
        pytest.fail("treatment plan has no cross-user fromUserId body")


# ── 4) percent vocabulary negative mutation ──

def test_percent_field_negative_mutation():
    result = _semantic_invalid_value(
        "deltaPercent", "number", {"type": "number"},
        semantic_text="价格调整百分比",
    )
    assert result is not None
    assert result[0] == -1
    assert "negative" in result[1]
    assert _NUMERIC_NEGATIVE_FIELDS.search("deltaPercent")
    assert _NUMERIC_NEGATIVE_FIELDS.search("price_adjust_percent")

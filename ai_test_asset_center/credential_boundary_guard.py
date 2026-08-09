"""Credential-boundary guard: executable obligations for authentication surfaces.

Authentication/backdoor defects share one structural shape: an endpoint that
issues or changes credentials (password reset, token issuance, impersonation,
verification-code login) whose RUNTIME enforcement is weaker than its own
declared contract. The source material states the correct contract (权限：公开
+ 必须完成验证码校验, or 权限：管理员), so no rule→operation binding can surface
the defect — the defect IS a divergence between the declared contract and the
observed runtime behavior. This planner derives executable obligations from IR
structure and the operation's OWN contract text only (no endpoint names, no
industry terms, no benchmark data):

Arm A — role-declared credential surfaces must reject anonymous callers.
    A WRITE operation that declares a role restriction (permits relations
    present — 权限：管理员 / x-required-roles) AND sits on an
    identity/credential surface (auth/token/password/login/session/otp/
    impersonate/debug-sign …) must REJECT the no-credential request. A
    role-declared surface that accepts an anonymous call lets any caller mint
    a token / impersonate an identity / change a credential — the
    industry-universal backdoor shape on authentication surfaces. Reuses the
    credential_gated_write single-arm protocol channel (anonymous actor,
    rejection assertion) — no new protocol needed.

Arm B — declared verification-code logins must actually verify the code.
    A WRITE operation whose own contract names a verification-code mechanism
    in a login/verification context (验证码登录 / otp login) must REJECT a
    request carrying a code that was never issued. The treatment replaces the
    verification-value field (code/otp/验证码) with a deterministic wrong code;
    accepting it is the credential-verification weakness (any-code login).
    Flows through the validation channel's semantic invalid-value machinery.

Both arms are rejection-only by construction (a 4xx passes, an accepted
request violates), so no cleanup is required and no state can be left behind.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .behavior_ir import BehaviorIRError  # noqa: F401  (parity with sibling planners)
from .test_obligation import make_obligation

GUARD_SOURCE_KIND = "credential_boundary_guard"

# Identity/credential surface vocabulary: the operation's own path or contract
# names an authentication/credential mechanism. Generic enterprise-security
# language, never an industry term.
_CREDENTIAL_SURFACE_TOKENS = (
    "auth", "token", "password", "passwd", "credential", "credentials",
    "login", "session", "otp", "impersonate", "impersonation",
    "sign", "signing", "signed", "signature", "refresh", "logout",
    "密码", "登录", "登出", "令牌", "凭据", "凭证", "验证码", "校验码",
    "伪装", "签发", "调试", "签名", "验签", "认证",
)

# Verification-code mechanism vocabulary: the operation contract states that
# a verification code gates the identity exchange.
_VERIFICATION_LOGIN_TOKENS = (
    "验证码", "校验码", "短信码", "otp", "verification code", "sms code",
)

# Verification-code login context vocabulary: the operation is an identity
# exchange (login/verification), not a generic code-carrying operation.
_VERIFICATION_LOGIN_CONTEXT_TOKENS = (
    "登录", "登入", "验证", "verify", "login", "sign in", "signin",
)

# Verification-value request-body field vocabulary (generic field names any
# verification-code system documents).
_VERIFICATION_VALUE_FIELD_KEYS = (
    "code", "otp", "verifycode", "verificationcode", "smscode",
    "验证码", "校验码", "短信码",
)

# Write methods the guards apply to.
_WRITE_METHODS = {"POST", "PUT", "PATCH"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _operation_is_anonymous(
    operation_ref: str,
    relations: list[dict[str, Any]],
) -> bool:
    """Anonymous-reachable = no permits/denies declaration for the operation.

    A role-restricted operation always carries permits relations (the
    permission matrix is the runtime projection of the declared roles); an
    undeclared operation is reachable by anonymous callers by definition.
    (Shared semantics with the account-enumeration and credential-gated-write
    guards.)
    """
    for relation in relations:
        if _text(relation.get("operation_ref")) != operation_ref:
            continue
        if _text(relation.get("relation_type")) in {"permits", "denies"}:
            return False
    return True


def _surface_text(operation: dict[str, Any]) -> str:
    return " ".join((
        _text(operation.get("path") or operation.get("raw_path")),
        _text(operation.get("summary")),
        _text(operation.get("description")),
        _text(operation.get("contract")),
    )).casefold()


def _is_credential_surface(operation: dict[str, Any]) -> bool:
    """Whether the operation is an identity/credential surface.

    The operation's own path/contract names an authentication mechanism
    (token/password/login/verification-code/impersonate/debug-sign …). This is
    the authentication-surface vocabulary any enterprise system documents —
    it never names a specific endpoint.
    """
    surface = _surface_text(operation)
    return any(token in surface for token in _CREDENTIAL_SURFACE_TOKENS)


def _declares_verification_login(operation: dict[str, Any]) -> bool:
    """Whether the operation contract names a verification-code login.

    The mechanism vocabulary (验证码/otp/verification code) AND the identity
    exchange context (登录/login/verify) must BOTH be present: a coupon
    code-validation surface never declares a verification-code login.
    """
    surface = _surface_text(operation)
    return any(
        token in surface for token in _VERIFICATION_LOGIN_TOKENS
    ) and any(
        token in surface for token in _VERIFICATION_LOGIN_CONTEXT_TOKENS
    )


def _request_body_schema(operation: dict[str, Any]) -> dict[str, Any]:
    schema = _dict(operation.get("request_schema"))
    if _dict(schema.get("properties")) or _text(schema.get("type")):
        return schema
    for media in _dict(schema.get("content")).values():
        nested = _dict(_dict(media).get("schema"))
        if nested:
            return nested
    return schema


def _request_body_example(operation: dict[str, Any]) -> dict[str, Any]:
    example = operation.get("request_example")
    if isinstance(example, dict) and example:
        return example
    schema = _dict(operation.get("request_schema"))
    for media in _dict(schema.get("content")).values():
        media_dict = _dict(media)
        value = media_dict.get("example")
        if isinstance(value, dict) and value:
            return value
        for row in _dict(media_dict.get("examples")).values():
            nested = _dict(_dict(row).get("value"))
            if nested:
                return nested
    return {}


def _verification_value_field(operation: dict[str, Any]) -> str:
    """A request-body field that carries the verification value (code/otp/…)."""
    schema = _request_body_schema(operation)
    for field_name in (schema.get("properties") or {}):
        if _normalize_key(str(field_name)) in _VERIFICATION_VALUE_FIELD_KEYS:
            return str(field_name)
    example = _request_body_example(operation)
    for field_name in example:
        if _normalize_key(str(field_name)) in _VERIFICATION_VALUE_FIELD_KEYS:
            return str(field_name)
    for field_name in (schema.get("required") or []):
        if _normalize_key(str(field_name)) in _VERIFICATION_VALUE_FIELD_KEYS:
            return str(field_name)
    return ""


def _identity_locator_field(operation: dict[str, Any]) -> str:
    """A request-body field that addresses an account (email/phone/username…)."""
    schema = _request_body_schema(operation)
    for field_name in (schema.get("properties") or {}):
        if _normalize_key(str(field_name)) in {
            "email", "mail", "username", "login", "account", "phone",
            "mobile", "userid", "user_id", "手机号", "账号", "邮箱", "用户名",
        }:
            return str(field_name)
    example = _request_body_example(operation)
    for field_name in example:
        if _normalize_key(str(field_name)) in {
            "email", "mail", "username", "login", "account", "phone",
            "mobile", "userid", "user_id", "手机号", "账号", "邮箱", "用户名",
        }:
            return str(field_name)
    return ""


def _ensure_anonymous_actor(ir: dict[str, Any]) -> None:
    """Ensure the anonymous actor exists in the IR actor pool (idempotent).

    The executor resolves anonymous/public actors to an empty token, so the
    request leaves the Authorization header off — exactly what "anonymous
    reachable" means at runtime.
    """
    actors = _list(ir.get("actors"))
    if not any(
        isinstance(actor, dict)
        and _text(actor.get("id")) == "anonymous"
        for actor in actors
    ):
        ir["actors"] = [*actors, {
            "id": "anonymous",
            "name": "anonymous",
            "role": "anonymous",
            "account_status": "active",
            "credential_secret_ref": "",
        }]


def _source_refs(operation: dict[str, Any], method: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(operation.get("source_refs"))
        if isinstance(row, dict)
    ] or [{
        "source_id": "api_spec",
        "locator": f"{method} {_text(operation.get('path') or operation.get('raw_path'))}",
        "kind": "api_operation",
    }]


def _operation_path_prefix(operation: dict[str, Any]) -> str:
    path = _text(operation.get("path") or operation.get("raw_path")).split("?", 1)[0].rstrip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2:
        return "/" + "/".join(parts[:2])
    if parts:
        return "/" + parts[0]
    return ""


def _arm_a_obligation(
    operation: dict[str, Any],
    op_id: str,
    obligation_id: str,
) -> dict[str, Any]:
    """Anonymous-rejection obligation for a role-declared credential surface."""
    method = _text(operation.get("method")).upper()
    contract = _surface_text(operation)
    mechanism = "签名" if any(
        token in contract for token in ("signature", "签名", "验签", "signing")
    ) else "身份"
    locator = _identity_locator_field(operation)
    statement = (
        f"角色限定的认证凭据接口必须拒绝匿名调用：{mechanism}校验缺失的调用"
        f"不得签发凭据或改变凭据状态（匿名请求不得通过）"
    )
    property_spec: dict[str, Any] = {
        "template": "credential_gated_write",
        "operation_ref": op_id,
        "operation_path_prefix": _operation_path_prefix(operation),
        "expression": {
            "kind": "authorization",
            "operator": "must_hold",
            "operands": [],
            "raw": statement,
        },
        "description": statement,
        "credential_gate": True,
        "rejection_expected": True,
        "credential_boundary": "role_declared_anonymous_rejection",
    }
    if locator:
        property_spec["identity_locator_field"] = locator
    return make_obligation(
        risk_family="authorization",
        subject_refs=[op_id, "anonymous"],
        property_spec=property_spec,
        required_actors=["anonymous"],
        required_operations=[op_id],
        required_observers=["http_response"],
        cleanup_requirement={"required": False, "mode": "not_required_read"},
        source_refs=_source_refs(operation, method),
        confidence=0.72,
        obligation_id=obligation_id,
    )


def _arm_b_obligation(
    operation: dict[str, Any],
    op_id: str,
    code_field: str,
    obligation_id: str,
) -> dict[str, Any]:
    """Verification-must-verify obligation for a declared verification login."""
    method = _text(operation.get("method")).upper()
    statement = (
        f"验证码登录必须真实校验验证码：{code_field} 携带未签发验证码时"
        f"必须拒绝登录（任意验证码不得通过）"
    )
    return make_obligation(
        risk_family="validation",
        subject_refs=[op_id, "anonymous"],
        property_spec={
            "template": "validation_rejection",
            "operation_ref": op_id,
            "operation_path_prefix": _operation_path_prefix(operation),
            "expression": {
                "kind": "validation",
                "operator": "must_hold",
                "operands": [],
                "raw": statement,
            },
            "description": statement,
            "field": code_field,
            "field_path": code_field,
            "field_tokens": [code_field],
            "json_path": f"$.{code_field}",
            "parameter_location": "body",
            "expected_rejection_status_class": 4,
            "expected_treatment_effect_count": 0,
            "validation_constraint": "verification_code_mismatch",
            "validation_constraint_value": "000000",
            "validation_constraint_source": "source_invariant",
            "semantic_validation_source": "credential_boundary_guard",
            "credential_boundary": "verification_code_must_be_verified",
        },
        required_actors=["anonymous"],
        required_operations=[op_id],
        required_observers=["http_response"],
        cleanup_requirement={"required": False, "mode": "not_required_read"},
        source_refs=_source_refs(operation, method),
        confidence=0.72,
        obligation_id=obligation_id,
    )


def build_credential_boundary_guard_obligations(
    behavior_ir: dict[str, Any],
    *,
    max_obligations: int = 20,
) -> list[dict[str, Any]]:
    """Generate executable auth/backdoor obligations from IR structure.

    Arm A: role-declared credential surfaces → anonymous-rejection check.
    Arm B: declared verification-code logins → wrong-code-rejection check.
    """
    ir = _dict(behavior_ir)
    operations = _list(ir.get("operations"))
    relations = _list(ir.get("relations"))
    operations_by_id = {
        _text(op.get("id")): op
        for op in operations
        if isinstance(op, dict) and _text(op.get("id"))
    }
    _ensure_anonymous_actor(ir)

    obligations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        op_id = _text(operation.get("id"))
        if not op_id or op_id not in operations_by_id:
            continue
        method = _text(operation.get("method")).upper()
        if method not in _WRITE_METHODS:
            continue
        surface = _is_credential_surface(operation)
        anonymous = _operation_is_anonymous(op_id, relations)

        # Arm A: role-declared (permits present) credential surface.
        if surface and not anonymous:
            material = "|".join(["credential_boundary_guard_arm_a", op_id])
            obligation_id = "obl_" + hashlib.sha256(
                material.encode("utf-8")
            ).hexdigest()[:20]
            if obligation_id not in seen:
                seen.add(obligation_id)
                if len(obligations) >= max_obligations:
                    break
                obligations.append(_arm_a_obligation(
                    operation, op_id, obligation_id,
                ))
        # Arm B: declared verification-code login (public or role-declared).
        if _declares_verification_login(operation):
            code_field = _verification_value_field(operation)
            if not code_field:
                continue
            material = "|".join(["credential_boundary_guard_arm_b", op_id])
            obligation_id = "obl_" + hashlib.sha256(
                material.encode("utf-8")
            ).hexdigest()[:20]
            if obligation_id not in seen:
                seen.add(obligation_id)
                if len(obligations) >= max_obligations:
                    break
                obligations.append(_arm_b_obligation(
                    operation, op_id, code_field, obligation_id,
                ))
    return obligations

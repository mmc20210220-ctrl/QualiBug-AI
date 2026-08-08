"""Credential-gated write guard: unauthenticated state-changing surfaces.

A write operation that is reachable WITHOUT authentication (no bearer security
declared, no permits/denies relation in the permission matrix) but whose own
contract demands verification-based authentication (回调必须验签 / 必须完成验证码
或等价身份校验 / must verify signature / credential required) must reject the
unauthenticated request. Accepting it lets an anonymous caller forge a state
change (mark a payment paid, reset another account's password) — the
industry-universal credential-gating defect on payment/identity surfaces.

This channel derives the guard from structure and the operation's own
contract text:

* the operation is a write (POST/PUT/PATCH);
* the permission matrix declares NO permits/denies relation for it — an
  undeclared operation is anonymous-reachable by definition;
* the operation's own contract declares verification-based authentication:
  signature/verification vocabulary (签名/验签/校验/验证码/身份校验/signature/
  verification/credential/otp) — the operation itself states that a caller
  must prove something the anonymous request does not carry;
* an identity locator field (email/username/account) in the request body lets
  the guard aim the probe at a real account from the runtime catalogue — the
  "任意账号" shape of a password-reset / identity-write surface.

The obligation is a single-arm authorization check: an anonymous (no-credential)
write is the treatment and the rejection assertion expects the unverified
request to be refused (4xx). A compliant surface rejects → passes; a surface
that accepts the unsigned write violates. It flows through the existing
authorization protocol channel with a dedicated template; the anonymous actor
pattern is shared with the account-enumeration guard.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .behavior_ir import BehaviorIRError  # noqa: F401  (parity with sibling planners)
from .test_obligation import make_obligation

# Verification-based authentication vocabulary: the operation contract states
# that a caller must present a signature / verification code / identity proof
# the anonymous request cannot carry. Generic enterprise-security language,
# never an industry term.
_VERIFICATION_AUTH_TOKENS = (
    "签名", "验签", "校验", "验证码", "身份校验", "凭证", "凭据",
    "signature", "verify", "verification", "credential", "otp",
    "signed", "signing", "authenticated",
)

# Identity-locator field vocabulary for the probe target: the request body
# field that addresses an account (邮箱/email/username/手机号). Generic
# identity-field language (mirrors the account-enumeration guard).
_IDENTITY_LOCATOR_FIELDS = (
    "email", "mail", "username", "login", "account",
    "phone", "mobile", "userid", "user_id",
    "邮箱", "账号", "用户名", "手机号",
)

# Sensitive state-change surfaces the guard may apply to: an operation whose
# contract documents signature/verification auth on ANY write is covered —
# no additional surface vocabulary is required (a "must verify" contract on
# a write is the guard's own source authority).

GUARD_SOURCE_KIND = "credential_gated_write_guard"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _operation_is_anonymous(
    operation_ref: str,
    relations: list[dict[str, Any]],
) -> bool:
    """Anonymous-reachable = no permits/denies declaration for the operation.

    A role-restricted operation always carries permits relations (the
    permission matrix is the runtime projection of the declared roles); an
    undeclared operation is reachable by anonymous callers by definition.
    """
    for relation in relations:
        if _text(relation.get("operation_ref")) != operation_ref:
            continue
        if _text(relation.get("relation_type")) in {"permits", "denies"}:
            return False
    return True


def _contract_text(operation: dict[str, Any]) -> str:
    return " ".join((
        _text(operation.get("summary")),
        _text(operation.get("description")),
        _text(operation.get("contract")),
    ))


def _declares_verification_auth(operation: dict[str, Any]) -> bool:
    """The operation's own contract demands verification-based authentication.

    The vocabulary is the verification mechanism itself (签名/验证码/身份校验/
    signature/verification/credential/otp) — never an industry term; any
    system that gates a write behind a signature or verification code
    documents the same language in the same way.
    """
    corpus = _contract_text(operation).casefold()
    return any(token.casefold() in corpus for token in _VERIFICATION_AUTH_TOKENS)


def _identity_locator_field(operation: dict[str, Any]) -> str:
    """A request-body field that addresses an account (email/username/…)."""
    schema = _request_schema(operation)
    for field_name in (schema.get("properties") or {}):
        if _is_identity_locator(str(field_name)):
            return str(field_name)
    example = _dict(operation.get("request_example"))
    for field_name in example:
        if _is_identity_locator(str(field_name)):
            return str(field_name)
    for field_name in (schema.get("required") or []):
        if _is_identity_locator(str(field_name)):
            return str(field_name)
    return ""


def _request_schema(operation: dict[str, Any]) -> dict[str, Any]:
    schema = _dict(operation.get("request_schema"))
    if _dict(schema.get("properties")):
        return schema
    for media in _dict(schema.get("content")).values():
        nested = _dict(_dict(media).get("schema"))
        if nested and _dict(nested.get("properties")):
            return nested
    return schema


def _is_identity_locator(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", str(name).lower())
    if not normalized:
        return False
    if normalized in {"userid", "memberid", "accountid", "loginid", "user_id"}:
        return True
    return any(
        normalized.endswith(key) or normalized == key
        for key in ("email", "mail", "username", "login", "account", "phone", "mobile")
    )


def _guard_statement(operation: dict[str, Any], locator_field: str) -> str:
    """Rule text naming the verification the anonymous write must not bypass.

    The statement mirrors the operation's own contract vocabulary (签名/验证码
    /身份校验) so the compiled obligation carries a verifiable source anchor
    in the finding blob; the forbidden outcome (接受未验证写请求) is generic
    security language, never an industry term.
    """
    contract = _contract_text(operation)
    mechanism = "签名" if "签名" in contract or "signature" in contract.casefold() else "身份校验"
    return (
        f"未验证写请求必须被拒绝：{mechanism}未校验的写操作不得生效"
        f"（{locator_field} 指向真实账号时不得接受匿名请求）"
    )


def _is_callback_surface(operation: dict[str, Any]) -> bool:
    """Callback/webhook surfaces (回调/通知/callback/notify) forge a state
    change on an EXISTING entity addressed by its reference field (orderId/
    paymentId) — the locator is the entity reference, not an account. The
    operation's own verification contract is the gate; no email-like locator
    is required."""
    surface = (
        f"{_text(operation.get('path') or operation.get('raw_path'))} "
        f"{_text(operation.get('summary'))} {_text(operation.get('description'))}"
    ).casefold()
    return any(
        token in surface
        for token in ("callback", "回调", "notify", "通知", "webhook")
    )


def build_credential_gated_write_guard_obligations(
    behavior_ir: dict[str, Any],
    *,
    max_obligations: int = 20,
) -> list[dict[str, Any]]:
    """Generate single-arm authorization guard obligations for
    verification-gated write surfaces reachable without authentication."""
    ir = _dict(behavior_ir)
    operations = _list(ir.get("operations"))
    relations = _list(ir.get("relations"))
    operations_by_id = {
        _text(op.get("id")): op
        for op in operations
        if isinstance(op, dict) and _text(op.get("id"))
    }

    # The guard's treatment is an ANONYMOUS request (no credential): the
    # executor resolves anonymous/public actors to an empty token, so the
    # write leaves the Authorization header off. Ensure the anonymous actor
    # exists in the IR actor pool (idempotent).
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

    obligations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        op_id = _text(operation.get("id"))
        if not op_id or op_id not in operations_by_id:
            continue
        method = _text(operation.get("method")).upper()
        if method not in {"POST", "PUT", "PATCH"}:
            continue
        if not _operation_is_anonymous(op_id, relations):
            continue
        if not _declares_verification_auth(operation):
            continue
        locator_field = _identity_locator_field(operation)
        if not locator_field and not _is_callback_surface(operation):
            continue
        material = "|".join(["credential_gated_write_guard", op_id])
        obligation_id = "obl_" + hashlib.sha256(
            material.encode("utf-8")
        ).hexdigest()[:20]
        if obligation_id in seen:
            continue
        seen.add(obligation_id)
        if len(obligations) >= max_obligations:
            break
        statement = _guard_statement(operation, locator_field or "entity_reference")
        obligations.append(make_obligation(
            risk_family="authorization",
            subject_refs=[op_id, "anonymous"],
            property_spec={
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
                "identity_locator_field": locator_field,
                "callback_surface": _is_callback_surface(operation),
                "rejection_expected": True,
            },
            required_actors=["anonymous"],
            required_operations=[op_id],
            required_observers=["http_response"],
            cleanup_requirement={"required": False, "mode": "not_required_read"},
            source_refs=[
                dict(row)
                for row in _list(operation.get("source_refs"))
                if isinstance(row, dict)
            ] or [{
                "source_id": "api_spec",
                "locator": f"{method} {_text(operation.get('path') or operation.get('raw_path'))}",
                "kind": "api_operation",
            }],
            confidence=0.7,
            obligation_id=obligation_id,
        ))
    return obligations


def _operation_path_prefix(operation: dict[str, Any]) -> str:
    path = _text(operation.get("path") or operation.get("raw_path")).split("?", 1)[0].rstrip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2:
        return "/" + "/".join(parts[:2])
    if parts:
        return "/" + parts[0]
    return ""

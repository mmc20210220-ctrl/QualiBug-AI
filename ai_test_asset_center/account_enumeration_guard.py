"""Account-enumeration guard: anonymous identity-query surfaces.

An identity-locator read endpoint (phone/email/username check) that is
reachable WITHOUT authentication must not return account attributes
(email/phone/status/role): revealing existence AND attributes of an account
to an anonymous caller is the industry-universal account-enumeration defect
(any industry's identity/onboarding/auth surface documents the same risk).

This channel derives the guard from structure only:

* the operation is a read (GET/HEAD);
* the permission matrix declares NO permits/denies relation for it — an
  undeclared operation is anonymous-reachable by definition (a declared role
  contract would have produced permits relations);
* at least one query/path parameter is an identity locator (phone/email/
  mobile/username/login/account/user_id — generic identity vocabulary);
* the rule text names the account attributes the response must not carry
  (邮箱/email、手机号/phone、状态/status、角色/role — the same generic
  account-field concepts the response-side privacy protocol matches).

The obligation is a single-arm privacy field check: an anonymous (no-credential)
read is the observation and the absent policy asserts the declared account
attributes never appear in the response. It flows through the existing
response-side privacy channel (pairing keeps single-arm obligations,
field_tokens are extracted from the rule text, the privacy_field_policy
evaluator scans nested field names). A compliant surface (boolean-only
existence answer, or a 401 for undeclared surfaces) passes; a surface leaking
attributes violates.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .behavior_ir import BehaviorIRError  # noqa: F401  (kept for parity with sibling planners)
from .test_obligation import make_obligation

# Identity-locator parameter vocabulary — generic identity field names any
# system documents (mirrors experiment_protocols_base._IDENTITY_LOCATOR_KEYS).
_IDENTITY_LOCATOR_KEYS = (
    "email", "phone", "mobile", "username", "login", "account",
    "user_id", "userid", "member_id", "memberid",
)

# Generic account-attribute concepts the guard rule forbids in the anonymous
# response (mirrors experiment_protocols_base._ACCOUNT_FIELD_CONCEPTS).
_ACCOUNT_ATTRIBUTE_CONCEPTS = (
    "邮箱", "邮件", "email",
    "手机号", "手机", "电话", "phone", "mobile",
    "状态", "status",
    "角色", "role",
    "姓名", "name",
)

GUARD_SOURCE_KIND = "account_enumeration_guard"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _is_identity_locator(name: str) -> bool:
    normalized = _normalize_key(name)
    if not normalized:
        return False
    if normalized in {"userid", "memberid", "accountid", "loginid"}:
        return True
    return any(normalized.endswith(key) or normalized == key for key in _IDENTITY_LOCATOR_KEYS)


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


def _identity_locator_params(operation: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for parameter in _list(operation.get("parameters")):
        if not isinstance(parameter, dict):
            continue
        name = _text(parameter.get("name"))
        if name and _is_identity_locator(name):
            found.append(name)
    return found


def _guard_statement(operation: dict[str, Any], locator: str) -> str:
    """Rule text naming the account attributes the anonymous response must not
    carry. The response-side vocabulary (响应/返回) and account-attribute
    concepts (邮箱/手机号/状态/角色) are what the response-side privacy
    channel extracts forbidden field tokens from — same generic business
    language, never industry terms."""
    return (
        f"匿名身份查询（{locator}）响应不得返回账号属性："
        f"邮箱 email、手机号 phone、状态 status、角色 role"
    )


def build_account_enumeration_guard_obligations(
    behavior_ir: dict[str, Any],
    *,
    max_obligations: int = 20,
) -> list[dict[str, Any]]:
    """Generate single-arm privacy guard obligations for anonymous identity
    query surfaces. Read-only by construction (the observation is the GET)."""
    ir = _dict(behavior_ir)
    operations = _list(ir.get("operations"))
    relations = _list(ir.get("relations"))
    operations_by_id = {
        _text(op.get("id")): op
        for op in operations
        if isinstance(op, dict) and _text(op.get("id"))
    }

    # The guard's observation is an ANONYMOUS request (no credential): the
    # executor resolves anonymous/public actors to an empty token, so the GET
    # leaves the Authorization header off. Ensure the anonymous actor exists
    # in the IR actor pool (idempotent) so the obligation's required_actors
    # can name it; the runtime actor exploration creates the same identity at
    # execution time when it is absent.
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
        if method not in {"GET", "HEAD"}:
            continue
        if not _operation_is_anonymous(op_id, relations):
            continue
        locators = _identity_locator_params(operation)
        if not locators:
            continue
        locator = locators[0]
        material = "|".join(["account_enumeration_guard", op_id])
        obligation_id = "obl_" + hashlib.sha256(
            material.encode("utf-8")
        ).hexdigest()[:20]
        if obligation_id in seen:
            continue
        seen.add(obligation_id)
        if len(obligations) >= max_obligations:
            break
        statement = _guard_statement(operation, locator)
        obligations.append(make_obligation(
            risk_family="privacy",
            subject_refs=[op_id, "anonymous"],
            property_spec={
                "template": "account_enumeration_guard",
                "operation_ref": op_id,
                "operation_path_prefix": _operation_path_prefix(operation),
                "expression": {
                    "kind": "privacy",
                    "operator": "must_hold",
                    "operands": [],
                    "raw": statement,
                },
                "description": statement,
                "privacy_test_mode": "field_policy",
                "privacy_policy": "absent",
                "field_tokens": [
                    "email", "phone", "mobile", "status", "role",
                ],
                "match_field_names": True,
                "privacy_field_source": "account_enumeration_guard",
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

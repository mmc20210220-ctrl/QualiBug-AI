"""Step-scoped ownership-query binding authority.

Query ownership parameters such as ``?userId={userId}`` describe the caller's
own identity for that exact control/treatment step. Modern compilation seals
such placeholders as ``actor_identity_ref:<actor_ref>:<target>`` and removes the
target from the experiment-global binding plan. Legacy ``{target}`` query
placeholders remain supported only by the same step-local actor authority.

Identity authority is:
1. one explicit actor account/user identity field; otherwise
2. one typed JWT identity claim (user/account/id); otherwise
3. JWT ``sub`` only when no typed identity exists.

Multiple typed identities are conflict, not a ranking problem. Missing/conflict
becomes a dedicated unresolved placeholder so the existing query gate blocks
before transport. No actor order and no global binding value participates.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from .obligation_compiler_base import (
    _ownership_binder_location,
    _ownership_params_declared_on_operation,
)
from .experiment_runtime_support import _resolve_token
from .ownership_binding_scope_authority import ACTOR_IDENTITY_REF_PREFIX

SCHEMA_VERSION = "qualibug.actor-scoped-query-binding.v1"
UNRESOLVED_TOKEN = "QUALIBUG_ACTOR_IDENTITY_UNRESOLVED"
UNRESOLVED_PLACEHOLDER = "{" + UNRESOLVED_TOKEN + "}"
_QUERY_TOKEN_RE = re.compile(r"^\s*\{([A-Za-z_][A-Za-z0-9_]*)\}\s*$")
_ACTOR_REF_RE = re.compile(
    r"^actor_identity_ref:([^:\s]+):([A-Za-z_][A-Za-z0-9_]*)$"
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(_text(value) for value in values if _text(value)))


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = _text(token).split(".")
    if len(parts) < 2:
        return {}
    padding = "=" * (-len(parts[1]) % 4)
    try:
        raw = base64.urlsafe_b64decode((parts[1] + padding).encode("ascii"))
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _fingerprint(value: str) -> str:
    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()[:16]


def resolve_actor_runtime_identity(
    actor_ref: str,
    *,
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
) -> dict[str, Any]:
    actor = _dict(actors.get(_text(actor_ref)))
    base = {
        "schema_version": SCHEMA_VERSION,
        "actor_ref": _text(actor_ref),
        "status": "UNRESOLVED",
        "reason_code": "",
        "identity_fingerprint": "",
        "identity_value_persisted": False,
    }
    if not actor:
        return {**base, "reason_code": "ACTOR_IDENTITY_ACTOR_MISSING"}

    explicit = _unique(
        [
            actor.get("account_id"),
            actor.get("accountId"),
            actor.get("user_id"),
            actor.get("userId"),
        ]
    )
    if len(explicit) == 1:
        return {
            **base,
            "status": "RESOLVED",
            "reason_code": "",
            "identity_value": explicit[0],
            "identity_fingerprint": _fingerprint(explicit[0]),
            "authority": "actor_declared_account_identity",
        }
    if len(explicit) > 1:
        return {
            **base,
            "reason_code": "ACTOR_IDENTITY_EXPLICIT_CONFLICT",
            "candidate_count": len(explicit),
        }

    token = _resolve_token(actor, tokens)
    payload = _decode_jwt_payload(token)
    typed = _unique(
        [
            payload.get("user_id"),
            payload.get("userId"),
            payload.get("account_id"),
            payload.get("accountId"),
            payload.get("uid"),
            payload.get("id"),
        ]
    )
    if len(typed) == 1:
        return {
            **base,
            "status": "RESOLVED",
            "reason_code": "",
            "identity_value": typed[0],
            "identity_fingerprint": _fingerprint(typed[0]),
            "authority": "jwt_typed_identity_claim",
        }
    if len(typed) > 1:
        return {
            **base,
            "reason_code": "ACTOR_IDENTITY_JWT_TYPED_CLAIM_CONFLICT",
            "candidate_count": len(typed),
        }

    subject = _text(payload.get("sub"))
    if subject:
        return {
            **base,
            "status": "RESOLVED",
            "reason_code": "",
            "identity_value": subject,
            "identity_fingerprint": _fingerprint(subject),
            "authority": "jwt_subject_identity",
        }
    return {
        **base,
        "reason_code": "ACTOR_IDENTITY_RUNTIME_CLAIM_MISSING",
    }


def _query_coordinate(
    raw_value: str,
    *,
    step_actor_ref: str,
) -> tuple[str, str, str]:
    """Return (coordinate_actor, target, source_kind) for one governed query value."""

    actor_match = _ACTOR_REF_RE.match(_text(raw_value))
    if actor_match:
        return _text(actor_match.group(1)), _text(actor_match.group(2)), "sealed_actor_identity_ref"
    legacy = _QUERY_TOKEN_RE.match(raw_value)
    if legacy:
        return _text(step_actor_ref), _text(legacy.group(1)), "legacy_step_placeholder"
    return "", "", ""


def project_actor_scoped_query_bindings(
    *,
    control_plan: list[Any],
    treatment_plan: list[Any],
    ops: dict[str, dict[str, Any]],
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
) -> tuple[list[Any], list[Any], dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def project_plan(plan: list[Any], phase: str) -> list[Any]:
        projected: list[Any] = []
        for raw_step in plan:
            if not isinstance(raw_step, dict):
                projected.append(raw_step)
                continue
            operation_ref = _text(raw_step.get("operation_ref"))
            operation = _dict(ops.get(operation_ref))
            query = _dict(raw_step.get("query"))
            if not operation or not query:
                # No query projection is needed; keep the exact step object so
                # identity-based ``consumed_barrier_steps`` filtering still sees
                # the same object the barrier executor consumed.
                projected.append(raw_step)
                continue
            ownership_params = set(
                _ownership_params_declared_on_operation(operation)
            )
            if not ownership_params:
                projected.append(raw_step)
                continue

            # Only deep-copy the step that actually gets its query projected:
            # the projection mutates ``step["query"]`` and must not touch the
            # sealed experiment plan.
            step = deepcopy(raw_step)
            step_actor_ref = _text(step.get("actor_ref"))
            new_query = dict(query)
            projected_targets: list[str] = []
            unresolved_targets: list[str] = []
            step_rows: list[dict[str, Any]] = []
            for key, raw_value in query.items():
                if not isinstance(raw_value, str):
                    continue
                coordinate_actor, target, coordinate_source = _query_coordinate(
                    raw_value,
                    step_actor_ref=step_actor_ref,
                )
                if not target:
                    continue
                if (
                    target not in ownership_params
                    or _ownership_binder_location(operation, name=target) != "query"
                ):
                    continue

                if coordinate_actor != step_actor_ref:
                    new_query[key] = UNRESOLVED_PLACEHOLDER
                    unresolved_targets.append(target)
                    step_rows.append(
                        {
                            "target": target,
                            "status": "UNRESOLVED",
                            "reason_code": "ACTOR_IDENTITY_REF_STEP_ACTOR_MISMATCH",
                            "coordinate_source": coordinate_source,
                        }
                    )
                    continue

                identity_receipt = resolve_actor_runtime_identity(
                    coordinate_actor,
                    actors=actors,
                    tokens=tokens,
                )
                if _text(identity_receipt.get("status")) == "RESOLVED":
                    new_query[key] = _text(identity_receipt.get("identity_value"))
                    projected_targets.append(target)
                else:
                    new_query[key] = UNRESOLVED_PLACEHOLDER
                    unresolved_targets.append(target)
                step_rows.append(
                    {
                        "target": target,
                        "status": _text(identity_receipt.get("status")),
                        "reason_code": _text(identity_receipt.get("reason_code")),
                        "identity_authority": _text(identity_receipt.get("authority")),
                        "identity_fingerprint": _text(
                            identity_receipt.get("identity_fingerprint")
                        ),
                        "coordinate_source": coordinate_source,
                    }
                )

            if projected_targets or unresolved_targets:
                step["query"] = new_query
                step["actor_query_binding_projection"] = {
                    "schema_version": SCHEMA_VERSION,
                    "actor_ref": step_actor_ref,
                    "projected_targets": list(dict.fromkeys(projected_targets)),
                    "unresolved_targets": list(dict.fromkeys(unresolved_targets)),
                    "target_receipts": step_rows,
                    "identity_value_persisted": False,
                    "global_binding_fallback_allowed": False,
                }
                rows.append(
                    {
                        "phase": phase,
                        "step_id": _text(step.get("step_id") or step.get("id")),
                        "operation_ref": operation_ref,
                        "actor_ref": step_actor_ref,
                        **step["actor_query_binding_projection"],
                    }
                )
            projected.append(step)
        return projected

    control = project_plan(list(control_plan or []), "control")
    treatment = project_plan(list(treatment_plan or []), "treatment")
    unresolved = [row for row in rows if row.get("unresolved_targets")]
    return control, treatment, {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED" if unresolved else "PROJECTED",
        "row_count": len(rows),
        "projected_step_count": sum(
            1 for row in rows if row.get("projected_targets")
        ),
        "unresolved_step_count": len(unresolved),
        "rows": rows,
        "global_binding_fallback_allowed": False,
        "identity_value_persisted": False,
    }


__all__ = [
    "SCHEMA_VERSION",
    "UNRESOLVED_TOKEN",
    "UNRESOLVED_PLACEHOLDER",
    "resolve_actor_runtime_identity",
    "project_actor_scoped_query_bindings",
]

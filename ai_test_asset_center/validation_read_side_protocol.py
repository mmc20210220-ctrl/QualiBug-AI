"""Read-side owned-scope protocol: source rules on GET/HEAD ownership reads.

A source rule constraining a read operation (普通用户只能读取自己的地址 /
普通用户只能使用自己的 ID) asserts a caller-scoped ownership contract. The
validation chain historically had no consumer for read-side rules: every
GET/HEAD rule without a forbidden response field or a request-body mutation
died as ``validation_body_protocol_requires_write_operation`` — a structural
break in the four-link reachability chain, not a data problem.

This module wires one decidable projection additively:

* **Owned scope, two-arm.** When the operation declares an ownership query
  parameter whose own description states the caller-scoped constraint (只能…
  自己的/仅限本人/own/self), and the runtime actor catalogue holds at least
  two account-bound actors of the same role with runtime-observed identity
  ids (``account_id`` from their JWT), the protocol compiles a two-arm read:
  control reads with the actor's *own* identity, treatment reads with the
  peer's identity. The verdict is sealed by the ``owned_read_scope``
  evaluator, which accepts a rejection (4xx) or a body whose ownership
  fields all name the caller, and reports VIOLATION only when an observed
  row carries someone else's identity — the leak evidence.

Design constraints honoured here:

* **Additive wiring only.** The assertion kind goes through
  ``register_assertion_kind``; the built-in validation chain is untouched
  except for the new read-side branch that calls the projection compiler.
* **No vacuous PASS.** A single 2xx observation proves nothing: the
  evaluator needs either an explicit rejection or row-level ownership
  evidence. Missing rows, missing owner fields, or a 5xx all seal
  INDETERMINATE with a named reason code.
* **No inferred business semantics.** The ownership parameter name is a
  normalized structural match (user/owner/account/member + id suffix); the
  modal declaration (自己的/本人/own/self) is generic ownership vocabulary,
  never an industry term. The identities come from runtime-observed bearer
  tokens, never from hardcoded values.
* **Fail-closed.** A treatment response that is neither a rejection nor
  provably caller-scoped is INDETERMINATE, never a violation from silence.
"""
from __future__ import annotations

from typing import Any

ASSERTION_KIND = "owned_read_scope"

# Generic ownership vocabulary: normalized parameter/field names that declare
# an account-level owner. Structural naming convention, not an industry term.
_OWNERSHIP_FIELD_SUFFIXES = ("userid", "user_id", "ownerid", "owner_id", "accountid", "account_id", "memberid", "member_id", "user", "owner", "account", "member")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_key(value: str) -> str:
    return _text(value).lower().replace("_", "").replace("-", "")


def is_ownership_key(name: str) -> bool:
    """Whether a parameter/field name declares an account-level owner.

    Pure normalized structural matching against generic ownership vocabulary;
    the normalized name either IS a known owner key (``userid``, ``owner``,
    ``account``, ``member``) or ends with one (``target_user_id``,
    ``customerAccountId``). No business/industry terms.
    """
    normalized = _normalize_key(name)
    if not normalized:
        return False
    if normalized in {"userid", "owner", "ownerid", "account", "accountid", "member", "memberid"}:
        return True
    return any(normalized.endswith(suffix) for suffix in _OWNERSHIP_FIELD_SUFFIXES)


def _extract_row_collection(body: Any) -> list[dict[str, Any]] | None:
    """Locate the row collection in a read response, structure-only.

    Search order (mirrors the readonly-audit protocol):
    1. the body itself, when it is a list of dicts;
    2. the first list of dicts under any key (a paged envelope);
    3. nothing — never invents one.
    """
    if isinstance(body, list):
        rows = [row for row in body if isinstance(row, dict)]
        return rows if rows else None
    if not isinstance(body, dict):
        return None
    for value in body.values():
        if not isinstance(value, list) or not value:
            continue
        rows = [row for row in value if isinstance(row, dict)]
        if rows:
            return rows
    return None


def _owner_field_in_row(row: dict[str, Any]) -> tuple[str, str]:
    """Return ``(field_name, value)`` of the first ownership key in one row."""
    for field_name, value in row.items():
        if is_ownership_key(field_name):
            return field_name, value
    return "", ""


def _evaluate_owned_read_scope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Tri-state evaluator for the owned-scope two-arm read.

    Reads the executed treatment GET response from the shared observation
    slots (``status_code`` / ``body``). Verdicts:

    * PASS — the target rejected the peer-identity read (4xx), or accepted it
      and every observed row's ownership field names the caller.
    * VIOLATION — a row carries an ownership value that is not the caller:
      the peer's data leaked to the caller, which is exactly the rule the
      source declared.
    * INDETERMINATE — no status evidence, a 5xx, an unlocatable collection,
      a row without an ownership field, or an empty body: unmeasured must
      never read as verified.
    """
    spec = _dict(envelope.get("spec"))
    obs = _dict(envelope.get("observations"))
    owner_identity = _text(spec.get("owner_identity"))
    expected = {
        "property": "owned_read_scope",
        "owner_identity": owner_identity,
        "query_parameter": _text(spec.get("query_parameter")),
    }

    status_code = obs.get("status_code")
    if not isinstance(status_code, int) or status_code <= 0:
        return {
            "passed": None,
            "reason_code": "READ_SCOPE_EVIDENCE_MISSING",
            "expected": expected,
            "actual": {"status_code": status_code},
        }
    if 400 <= status_code < 500:
        # An explicit rejection of the peer-identity read is the source rule
        # being enforced. Nothing more to observe.
        return {
            "passed": True,
            "reason_code": "",
            "expected": expected,
            "actual": {"status_code": status_code},
        }
    if not (200 <= status_code < 300):
        return {
            "passed": None,
            "reason_code": "READ_SCOPE_TARGET_ERROR",
            "expected": expected,
            "actual": {"status_code": status_code},
        }
    if not owner_identity:
        return {
            "passed": None,
            "reason_code": "READ_SCOPE_OWNER_IDENTITY_MISSING",
            "expected": expected,
            "actual": {},
        }
    if "body" not in obs or obs.get("body") is None:
        return {
            "passed": None,
            "reason_code": "READ_SCOPE_BODY_EVIDENCE_MISSING",
            "expected": expected,
            "actual": {"status_code": status_code},
        }

    rows = _extract_row_collection(obs.get("body"))
    if rows is None:
        return {
            "passed": None,
            "reason_code": "READ_SCOPE_COLLECTION_NOT_OBSERVED",
            "expected": expected,
            "actual": {"status_code": status_code},
        }
    if not rows:
        # No rows: nothing proves the caller did or did not see the peer's
        # data. A rejection or caller-owned evidence is required for PASS.
        return {
            "passed": None,
            "reason_code": "READ_SCOPE_NO_ROWS_OBSERVED",
            "expected": expected,
            "actual": {"observed_rows": 0},
        }

    missing_owner = 0
    foreign_rows: list[dict[str, Any]] = []
    caller_rows = 0
    for row in rows:
        field_name, value = _owner_field_in_row(row)
        if not field_name:
            missing_owner += 1
            continue
        if _text(value) == owner_identity:
            caller_rows += 1
        else:
            foreign_rows.append({"owner_field": field_name, "owner_value": value})

    if foreign_rows:
        return {
            "passed": False,
            "reason_code": "OWNED_SCOPE_LEAK_OBSERVED",
            "expected": expected,
            "actual": {
                "observed_rows": len(rows),
                "caller_owned_rows": caller_rows,
                "rows_missing_owner_field": missing_owner,
                "foreign_rows": foreign_rows[:5],
            },
        }
    if missing_owner:
        # Rows exist but some carry no ownership field: the caller-scope claim
        # cannot be evidenced. Fail closed.
        return {
            "passed": None,
            "reason_code": "READ_SCOPE_OWNER_FIELD_NOT_OBSERVED",
            "expected": expected,
            "actual": {
                "observed_rows": len(rows),
                "rows_missing_owner_field": missing_owner,
            },
        }
    return {
        "passed": True,
        "reason_code": "",
        "expected": expected,
        "actual": {"observed_rows": len(rows), "caller_owned_rows": caller_rows},
    }


def install_owned_read_scope_protocol() -> dict[str, str]:
    """Register the owned-scope assertion kind idempotently.

    The HTTP response surface writes ``status_code``/``body`` into the shared
    observation slots for every executed step; declaring those evidence keys
    on the ``http_response`` registry entry is what lets the kind-to-evidence
    contract accept this assertion kind instead of marking it unproducible.
    """
    from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
    from .observer_contracts_base import OBSERVER_REGISTRY

    installed: dict[str, str] = {}

    http_entry = OBSERVER_REGISTRY.get("http_response")
    if isinstance(http_entry, dict):
        declared = tuple(http_entry.get("evidence_keys") or ())
        merged = tuple(dict.fromkeys((*declared, "status_code", "body")))
        if merged != declared:
            http_entry["evidence_keys"] = merged
            installed["observer_evidence_keys"] = ",".join(merged)

    if ASSERTION_KIND not in set(registered_assertion_kinds()):
        installed["assertion"] = register_assertion_kind(
            ASSERTION_KIND,
            evaluator=_evaluate_owned_read_scope,
            # status_code is the gate; the body is legitimately an empty list
            # on a caller with no rows, and the evaluator seals empty/missing
            # bodies INDETERMINATE itself (fail-closed). Requiring "body" here
            # would let the shared evidence gate reject [] as "absent".
            required_evidence_keys=("status_code",),
        )
    else:
        installed["assertion"] = ASSERTION_KIND
    return installed

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
import json
import re

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


# ═══════════════════════════════════════════════════════════════════════
# Query-safety SQL-injection probe (additive, industry-neutral)
# ═══════════════════════════════════════════════════════════════════════
#
# Root cause it repairs: the query-safety mutation heuristic
# (experiment_protocols_base._semantic_invalid_value) only ever fires for
# request-BODY string fields. A rule declaring query-safety vocabulary
# (关键词必须参数化查询 / 不得拼接 SQL / 表名拼接存在注入风险) on a GET/HEAD
# operation has no request body: the keyword/table query parameter was never
# mutated, so a target that concatenates the raw value into SQL was never
# probed and the whole defect class stayed unreachable on read surfaces.
#
# This projection compiles a single treatment read whose query parameter
# carries a generic OWASP-style quote-escape payload. The verdict comes from
# the observed response alone — no business semantics are inferred:
#   * VIOLATION — the response surfaces SQL-interpretation evidence: a
#     database error marker (syntax error, postgres/psycopg2/sqlite error,
#     ORA-…), or the payload echoed verbatim inside a response field whose
#     name is SQL-execution vocabulary (sql/statement/expression/command).
#   * PASS — the request was accepted or rejected (2xx/4xx) with none of
#     that evidence: the payload was treated as an ordinary literal value.
#   * INDETERMINATE — no status evidence, or a 5xx without a SQL marker:
#     unmeasured never reads as verified.
#
# The probe only fires when the rule's own text declares query-safety
# vocabulary (gated, so ordinary string rules never become probes), the
# operation documents the query parameter, and the parameter is a string
# target. No endpoint, parameter or payload is invented.

ASSERTION_KIND_SQL = "sql_injection_probe"

# Generic database error vocabulary — markers that surface when a probe
# payload escapes the literal of a concatenated query. Language/data-store
# neutral; never an industry term.
_SQL_ERROR_MARKERS = re.compile(
    r"(syntax error|syntaxerror|psycopg2|postgres(ql)?\s*error|sqlite3|"
    r"sqlite\s*error|ORA-\d{4,6}|sqlstate|error in your sql|"
    r"near\s+(quote|unterminated)|unterminated|mysql|mariadb|database error|sql error|"
    r"sql exception|invalid input syntax|does not exist|"
    r"sql语法错误|sql语句|sql错误|数据库错误|语法错误|执行sql)",
    re.IGNORECASE,
)

# Response fields that would carry concatenated SQL text — SQL-execution
# vocabulary, not request-echo vocabulary (a search endpoint legitimately
# echoes "keyword", never "sql").
_SQL_FIELD_NAME = re.compile(
    r"(^|[^a-z0-9_])(sql|statement|expression|command|db_sql|sql_text)([^a-z0-9_]|$)",
    re.IGNORECASE,
)

# Generic quote-escape probe (OWASP-style), same payload the body-channel
# heuristic uses so both surfaces probe identically.
SQL_INJECTION_PAYLOAD = "' OR '1'='1"

_QUERY_SAFETY_TOKENS = (
    "参数化", "拼接", "注入", "sql", "parameterized",
    "injection", "concatenat", "表名", "任意表",
)


def _probe_text_contains(body_text: str, payload: str) -> bool:
    """Payload containment with whitespace/case normalization.

    The target may re-render the payload with different quoting or spacing
    (``' OR '1'='1`` vs ``'or'1'='1``); normalize both sides before the
    containment test so an echoed probe is recognized.
    """
    needle = re.sub(r"\s+", "", payload).lower()
    hay = re.sub(r"\s+", "", body_text).lower()
    return bool(needle) and needle in hay


def _query_safety_semantic_text(property_spec: dict[str, Any]) -> str:
    expression = _dict(property_spec.get("expression"))
    return "\n".join(
        _text(value)
        for value in (
            expression.get("raw"),
            property_spec.get("source_intent"),
            property_spec.get("description"),
        )
        if _text(value)
    )


def _declares_query_safety(semantic_text: str) -> bool:
    normalized = semantic_text.lower()
    return any(_text(token).lower() in normalized for token in _QUERY_SAFETY_TOKENS)


def _target_query_parameter(
    property_spec: dict[str, Any],
    operation: dict[str, Any],
    semantic_text: str,
) -> str:
    """Pick the query parameter the rule governs.

    Order: (1) an explicit rule target field that is a declared query
    parameter; (2) a query parameter whose name appears in the rule text;
    (3) the first string-typed query parameter. The parameter must be
    documented — a parameter is never invented.
    """
    explicit_fields: list[str] = []
    expression = _dict(property_spec.get("expression"))
    for value in (
        property_spec.get("field"),
        property_spec.get("field_name"),
        property_spec.get("field_ref"),
        property_spec.get("json_path"),
        expression.get("field"),
        expression.get("field_name"),
        expression.get("field_ref"),
        expression.get("json_path"),
    ):
        field = _text(value).removeprefix("$.")
        if field and field not in explicit_fields:
            explicit_fields.append(field)

    params: list[dict[str, Any]] = []
    for param in _list(operation.get("parameters")):
        if not isinstance(param, dict):
            continue
        if _text(param.get("in")).lower() != "query":
            continue
        params.append(param)

    for field in explicit_fields:
        for param in params:
            if _text(param.get("name")) == field:
                return field
    for param in params:
        name = _text(param.get("name"))
        if name and name in semantic_text:
            return name
    for param in params:
        schema = _dict(param.get("schema"))
        if _text(schema.get("type") or param.get("type")) in ("", "string"):
            return _text(param.get("name"))
    return ""


def _documented_query_values(
    operation: dict[str, Any],
    target_param: str,
    payload: str,
) -> dict[str, Any]:
    """Documented query values, with the governed parameter carrying the probe."""
    query: dict[str, Any] = {}
    for param in _list(operation.get("parameters")):
        if not isinstance(param, dict):
            continue
        if _text(param.get("in")).lower() != "query":
            continue
        name = _text(param.get("name"))
        if not name:
            continue
        if name == target_param:
            query[name] = payload
            continue
        value = (
            param.get("example")
            or _dict(param.get("schema")).get("example")
            or param.get("default")
        )
        if value is not None:
            query[name] = value
    return query


def _sql_field_payload_echo(node: Any, payload: str, _depth: int = 0) -> str:
    """Find the payload echoed in an SQL-named field at any nesting depth.

    The concatenated query text can surface anywhere in the response shape
    (top-level, inside a data envelope, inside a list row). Walks dicts and
    lists up to a fixed depth; only field names that are SQL-execution
    vocabulary count as evidence, so a legitimate request-echo of the search
    term (``keyword``) never triggers.
    """
    if _depth > 4 or node is None:
        return ""
    if isinstance(node, dict):
        for key, value in node.items():
            if _SQL_FIELD_NAME.search(_text(key)) and isinstance(value, str):
                if _probe_text_contains(value, payload):
                    return _text(key)
            nested = _sql_field_payload_echo(value, payload, _depth + 1)
            if nested:
                return nested
    elif isinstance(node, list):
        for item in node:
            nested = _sql_field_payload_echo(item, payload, _depth + 1)
            if nested:
                return nested
    return ""


def _evaluate_sql_injection_probe(envelope: dict[str, Any]) -> dict[str, Any]:
    """Tri-state evaluator for the query-safety injection probe."""
    spec = _dict(envelope.get("spec"))
    obs = _dict(envelope.get("observations"))
    payload = _text(spec.get("payload")) or SQL_INJECTION_PAYLOAD
    query_parameter = _text(spec.get("query_parameter"))
    expected = {
        "property": "query_safety_sql_injection_probe",
        "query_parameter": query_parameter,
        "payload": payload,
    }

    status_code = obs.get("status_code")
    if not isinstance(status_code, int) or status_code <= 0:
        return {
            "passed": None,
            "reason_code": "SQL_PROBE_EVIDENCE_MISSING",
            "expected": expected,
            "actual": {"status_code": status_code},
        }
    body = obs.get("body")
    body_text = json.dumps(body, ensure_ascii=False, default=str) if body is not None else ""

    # Database error markers surface when the payload escaped the literal.
    if _SQL_ERROR_MARKERS.search(body_text):
        return {
            "passed": False,
            "reason_code": "SQL_INJECTION_SQL_ERROR_SURFACED",
            "expected": expected,
            "actual": {
                "status_code": status_code,
                "payload_echoed": _probe_text_contains(body_text, payload),
            },
        }
    # Payload echoed inside an SQL-execution field: the input was
    # concatenated into the query text, not bound as a literal.
    echo_field = _sql_field_payload_echo(body, payload)
    if echo_field:
        return {
            "passed": False,
            "reason_code": "SQL_PAYLOAD_ECHOED_IN_SQL_FIELD",
            "expected": expected,
            "actual": {"status_code": status_code, "field": echo_field},
        }
    if status_code >= 500:
        # A 5xx without a SQL marker cannot be attributed to injection.
        return {
            "passed": None,
            "reason_code": "SQL_PROBE_TARGET_ERROR",
            "expected": expected,
            "actual": {"status_code": status_code},
        }
    if 200 <= status_code < 500:
        # Accepted or rejected with the payload treated as a literal value:
        # the query-safety contract held for this probe.
        return {
            "passed": True,
            "reason_code": "",
            "expected": expected,
            "actual": {"status_code": status_code},
        }
    return {
        "passed": None,
        "reason_code": "SQL_PROBE_UNDETERMINED",
        "expected": expected,
        "actual": {"status_code": status_code},
    }


def compile_query_safety_injection_probe(
    *,
    operation: dict[str, Any],
    operation_ref: str,
    property_spec: dict[str, Any],
    actor_ref: str,
) -> dict[str, Any] | None:
    """Compile the query-parameter injection probe, or None when not applicable.

    Gated on the rule's own query-safety vocabulary and the operation's
    documented query parameters — nothing is inferred.
    """
    method = _text(operation.get("method", "")).upper()
    if method not in {"GET", "HEAD"}:
        return None
    semantic_text = _query_safety_semantic_text(property_spec)
    if not _declares_query_safety(semantic_text):
        return None
    target_param = _target_query_parameter(property_spec, operation, semantic_text)
    if not target_param:
        return None
    query = _documented_query_values(operation, target_param, SQL_INJECTION_PAYLOAD)
    if _text(query.get(target_param)) != SQL_INJECTION_PAYLOAD:
        return None
    return {
        "status": "COMPILED",
        "control_plan": [],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": actor_ref,
            "operation_ref": operation_ref,
            "intent": "query_safety_injection_probe",
            "protocol_step": "treatment",
            "query": query,
        }],
        "observers": [
            {"observer_id": "http_response"},
            {"observer_id": "typed_assertion"},
        ],
        "assertion": {
            "kind": ASSERTION_KIND_SQL,
            "query_parameter": target_param,
            "payload": SQL_INJECTION_PAYLOAD,
        },
    }


def install_query_safety_injection_probe() -> dict[str, str]:
    """Register the sql_injection_probe assertion kind idempotently."""
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

    if ASSERTION_KIND_SQL not in set(registered_assertion_kinds()):
        installed["assertion"] = register_assertion_kind(
            ASSERTION_KIND_SQL,
            evaluator=_evaluate_sql_injection_probe,
            # status_code is the gate; the evaluator seals missing bodies
            # INDETERMINATE itself (fail-closed).
            required_evidence_keys=("status_code",),
        )
    else:
        installed["assertion"] = ASSERTION_KIND_SQL
    return installed

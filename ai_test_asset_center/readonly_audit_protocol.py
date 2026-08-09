"""Read-only state-audit protocol: source-declared invariants checked via GET.

The ``state_audit_planner`` maps unbound invariants to GET endpoints and emits
``audit_mode: read_only`` obligations (template ``readonly_audit_validation``).
Until this module existed those obligations had no protocol consumer: the
validation family chain requires a write operation, so every audit obligation
died as ``validation_body_protocol_requires_write_operation`` — a structural
break in the four-link reachability chain (obligation → assertion kind →
observer → protocol), not a data problem.

Design constraints honoured here:

* **Additive wiring only.** The assertion kind goes through
  ``register_assertion_kind`` and the protocol through
  ``register_family_protocol`` — the built-in validation chain is untouched.
* **No vacuous PASS.** A uniqueness audit over a single row proves nothing;
  fewer than two observed rows return INDETERMINATE, never PASS. Unmeasured
  must not read as verified.
* **No inferred business semantics.** The checked field name and its qualifier
  come verbatim from the source-declared ``field_ref`` operand; collection
  extraction is pure structural search over the response body (a top-level
  list, or a list-valued key whose normalized name matches the qualifier, or
  the first list of dicts carrying the field). No entity/table knowledge is
  baked in.
* **Fail-closed.** Missing read evidence, a rejected read, an unlocatable
  collection, or a row without the field all seal INDETERMINATE with a named
  reason code.
"""
from __future__ import annotations

from typing import Any

PROTOCOL_TEMPLATE = "readonly_audit_validation"
ASSERTION_KIND = "readonly_uniqueness_audit"
ASSERTION_KIND_NUMERIC = "readonly_numeric_audit"
RISK_FAMILY = "validation"
MIN_AUDIT_ROWS = 2
_NUMERIC_BOUNDARY_KINDS = frozenset({
    "numeric_boundary", "non_negative", "positive", "numeric_non_negative",
})
_NUMERIC_OPERATORS = frozenset({"non_negative", "positive"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_key(value: str) -> str:
    return value.lower().replace("_", "").replace("-", "")


def split_field_ref(field_ref: str) -> tuple[str, str]:
    """Split a source-declared ``field:<qualifier>.<column>`` reference.

    Returns ``(qualifier, column)``. A ref without a dot yields an empty
    qualifier. The split is purely structural; both parts are used only as
    search keys against the observed response.
    """
    raw = _text(field_ref)
    if raw.startswith("field:"):
        raw = raw[len("field:"):]
    if "." in raw:
        qualifier, _, column = raw.rpartition(".")
        return _text(qualifier), _text(column)
    return "", raw


def uniqueness_field_from_expression(expression: dict[str, Any]) -> tuple[str, str]:
    """Extract ``(qualifier, column)`` from a ``validation_uniqueness`` expression.

    The planner emits operands carrying ``field_ref``/``field_id``. Only the
    first field operand is used; an expression without one yields empty
    strings so the protocol compiler blocks visibly instead of guessing.
    """
    expr = _dict(expression)
    for operand in _list(expr.get("operands")):
        if not isinstance(operand, dict):
            continue
        ref = _text(operand.get("field_ref") or operand.get("field_id") or operand.get("field"))
        if ref:
            return split_field_ref(ref)
    return "", ""


def _extract_collection(body: Any, qualifier: str, column: str) -> list[Any] | None:
    """Locate the row collection the uniqueness statement applies to.

    Search order (deterministic, structure-only):
    1. the body itself, when it is a list of dicts;
    2. a list-valued key whose normalized name matches the qualifier;
    3. the first list of dicts whose elements carry the column.

    Returns ``None`` when no candidate exists — never invents one.
    """
    if isinstance(body, list):
        rows = [row for row in body if isinstance(row, dict)]
        if rows:
            return rows
        return None
    if not isinstance(body, dict):
        return None
    want = _normalize_key(qualifier) if qualifier else ""
    if want:
        for key, value in body.items():
            if isinstance(value, list) and _normalize_key(_text(key)) == want:
                rows = [row for row in value if isinstance(row, dict)]
                if rows:
                    return rows
    for value in body.values():
        if not isinstance(value, list) or not value:
            continue
        rows = [row for row in value if isinstance(row, dict)]
        if rows and all(column in row for row in rows):
            return rows
    return None


def _extract_numeric_rows(body: Any, qualifier: str, column: str) -> list[Any] | None:
    """Locate numeric-boundary rows, including a single-row detail response.

    A collection read returns rows for a set-level boundary audit; a DETAIL
    read (GET /api/entity/{id}) returns the entity itself — the body dict
    carrying the field is the single row to judge. A single row is decidable
    for a numeric boundary (one row already below zero is a violation), which
    is not true for a uniqueness claim. Falls back to
    ``_extract_collection``; returns ``None`` when no candidate exists.
    """
    rows = _extract_collection(body, qualifier, column)
    if rows is not None:
        return rows
    if isinstance(body, dict) and column in body:
        return [body]
    return None


def _evaluate_readonly_uniqueness_audit(envelope: dict[str, Any]) -> dict[str, Any]:
    """Tri-state evaluator for read-only uniqueness audits.

    Reads the executed GET response from the shared observation slots
    (``status_code`` / ``body``) written by the step executor.
    """
    spec = _dict(envelope.get("spec"))
    obs = _dict(envelope.get("observations"))
    column = _text(spec.get("field"))
    qualifier = _text(spec.get("field_qualifier"))
    expected = {
        "property": "uniqueness",
        "field": column,
        "field_qualifier": qualifier,
        "min_observed_rows": MIN_AUDIT_ROWS,
    }

    status_code = obs.get("status_code")
    if not isinstance(status_code, int) or status_code <= 0:
        return {
            "passed": None,
            "reason_code": "AUDIT_READ_EVIDENCE_MISSING",
            "expected": expected,
            "actual": {"status_code": status_code},
        }
    if not (200 <= status_code < 300):
        return {
            "passed": None,
            "reason_code": "AUDIT_READ_NOT_ACCEPTED",
            "expected": expected,
            "actual": {"status_code": status_code},
        }
    if not column:
        return {
            "passed": None,
            "reason_code": "AUDIT_FIELD_NOT_DECLARED",
            "expected": expected,
            "actual": {},
        }
    if "body" not in obs or obs.get("body") is None:
        return {
            "passed": None,
            "reason_code": "AUDIT_BODY_EVIDENCE_MISSING",
            "expected": expected,
            "actual": {"status_code": status_code},
        }

    rows = _extract_collection(obs.get("body"), qualifier, column)
    if rows is None:
        return {
            "passed": None,
            "reason_code": "AUDIT_COLLECTION_NOT_OBSERVED",
            "expected": expected,
            "actual": {"status_code": status_code},
        }
    if len(rows) < MIN_AUDIT_ROWS:
        # A uniqueness claim cannot be evidenced by fewer than two rows; a
        # PASS here would be a vacuous verdict fabricated from silence.
        return {
            "passed": None,
            "reason_code": "AUDIT_COLLECTION_TOO_SMALL",
            "expected": expected,
            "actual": {"observed_rows": len(rows)},
        }
    missing_field = sum(1 for row in rows if column not in row)
    if missing_field:
        return {
            "passed": None,
            "reason_code": "AUDIT_FIELD_NOT_OBSERVED",
            "expected": expected,
            "actual": {"observed_rows": len(rows), "rows_missing_field": missing_field},
        }

    seen: set[Any] = set()
    duplicates: list[Any] = []
    for row in rows:
        value = row.get(column)
        key = value if isinstance(value, (str, int, float, bool)) or value is None else repr(value)
        if key in seen:
            if key not in duplicates:
                duplicates.append(key)
        else:
            seen.add(key)
    if duplicates:
        return {
            "passed": False,
            "reason_code": "UNIQUENESS_DUPLICATE_VALUES_OBSERVED",
            "expected": expected,
            "actual": {
                "observed_rows": len(rows),
                "distinct_values": len(seen),
                "duplicate_values": duplicates[:10],
            },
        }
    return {
        "passed": True,
        "reason_code": "",
        "expected": expected,
        "actual": {"observed_rows": len(rows), "distinct_values": len(seen)},
    }


def _numeric_boundary_from_expression(expression: dict[str, Any]) -> tuple[str, str, str]:
    """Extract (qualifier, column, operator) from a numeric boundary expression.

    The planner emits operands carrying ``entity_ref`` + ``field``. Only the
    first field operand is used; an expression without one yields empty
    strings so the protocol compiler blocks visibly instead of guessing.

    Boundary precedence is structural: the equation's declared operator
    (``equation.operator``: positive / non_negative) is the precise boundary;
    the expression-level operator and kind are fallbacks. Falling back to
    ``non_negative`` for every ``numeric_boundary`` kind would silently weaken
    a positive boundary (qty > 0) into qty >= 0 and let a zero quantity pass
    as clean — a fabricated pass, not a precision trade-off.
    """
    expr = _dict(expression)
    operator = _text(expr.get("operator") or expr.get("boundary_operator")).lower()
    equation = _dict(expr.get("equation"))
    equation_operator = _text(equation.get("operator")).lower()
    if equation_operator in _NUMERIC_OPERATORS:
        resolved_operator = equation_operator
    elif operator in _NUMERIC_OPERATORS:
        resolved_operator = operator
    elif _text(expr.get("kind")).lower() in _NUMERIC_BOUNDARY_KINDS:
        resolved_operator = (
            "positive"
            if _text(expr.get("kind")).lower() == "positive"
            else "non_negative"
        )
    else:
        resolved_operator = ""
    for operand in _list(expr.get("operands")):
        if not isinstance(operand, dict):
            continue
        field = _text(
            operand.get("field") or operand.get("field_ref") or operand.get("field_id")
        )
        if not field:
            continue
        qualifier = _text(operand.get("entity_ref") or operand.get("entity"))
        return qualifier, field, resolved_operator
    return "", "", resolved_operator


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            return None
    return None


def _evaluate_readonly_numeric_audit(envelope: dict[str, Any]) -> dict[str, Any]:
    """Tri-state evaluator for read-only numeric boundary audits.

    Reads the executed GET response from the shared observation slots and
    checks every observed row's field against the declared boundary
    (non_negative: value >= 0; positive: value > 0). A row outside the
    boundary is a violation — the schema omits the guard the invariant
    asserts. Missing evidence, non-numeric values or too few rows seal
    INDETERMINATE, never PASS.
    """
    spec = _dict(envelope.get("spec"))
    obs = _dict(envelope.get("observations"))
    column = _text(spec.get("field"))
    qualifier = _text(spec.get("field_qualifier"))
    operator = _text(spec.get("operator") or "non_negative").lower()
    expected = {
        "property": "numeric_boundary",
        "field": column,
        "field_qualifier": qualifier,
        "operator": operator,
        "min_observed_rows": MIN_AUDIT_ROWS,
    }

    status_code = obs.get("status_code")
    if not isinstance(status_code, int) or status_code <= 0:
        return {
            "passed": None,
            "reason_code": "AUDIT_READ_EVIDENCE_MISSING",
            "expected": expected,
            "actual": {"status_code": status_code},
        }
    if not (200 <= status_code < 300):
        return {
            "passed": None,
            "reason_code": "AUDIT_READ_NOT_ACCEPTED",
            "expected": expected,
            "actual": {"status_code": status_code},
        }
    if not column or operator not in _NUMERIC_OPERATORS:
        return {
            "passed": None,
            "reason_code": "AUDIT_NUMERIC_SPEC_INCOMPLETE",
            "expected": expected,
            "actual": {"column": column, "operator": operator},
        }
    if "body" not in obs or obs.get("body") is None:
        return {
            "passed": None,
            "reason_code": "AUDIT_BODY_EVIDENCE_MISSING",
            "expected": expected,
            "actual": {"status_code": status_code},
        }

    rows = _extract_numeric_rows(obs.get("body"), qualifier, column)
    if rows is None:
        return {
            "passed": None,
            "reason_code": "AUDIT_COLLECTION_NOT_OBSERVED",
            "expected": expected,
            "actual": {"status_code": status_code},
        }
    # A numeric boundary is decidable on a single row: one observed row
    # already below the boundary is violation evidence (unlike a uniqueness
    # claim, which needs two rows to exhibit a duplicate). A collection read
    # is still preferred, but a detail read must not starve the audit.
    if not rows:
        return {
            "passed": None,
            "reason_code": "AUDIT_COLLECTION_EMPTY",
            "expected": expected,
            "actual": {"observed_rows": 0},
        }
    missing_field = sum(1 for row in rows if column not in row)
    if missing_field:
        return {
            "passed": None,
            "reason_code": "AUDIT_FIELD_NOT_OBSERVED",
            "expected": expected,
            "actual": {"observed_rows": len(rows), "rows_missing_field": missing_field},
        }

    violations: list[dict[str, Any]] = []
    non_numeric = 0
    for row in rows:
        value = _numeric_value(row.get(column))
        if value is None:
            non_numeric += 1
            continue
        violated = value < 0 if operator == "non_negative" else value <= 0
        if violated:
            violations.append({"field_value": value})
    if non_numeric and not violations:
        return {
            "passed": None,
            "reason_code": "AUDIT_FIELD_NOT_NUMERIC",
            "expected": expected,
            "actual": {"observed_rows": len(rows), "non_numeric_rows": non_numeric},
        }
    if violations:
        return {
            "passed": False,
            "reason_code": "NUMERIC_BOUNDARY_VIOLATION_OBSERVED",
            "expected": expected,
            "actual": {
                "observed_rows": len(rows),
                "violating_rows": violations[:10],
            },
        }
    return {
        "passed": True,
        "reason_code": "",
        "expected": expected,
        "actual": {"observed_rows": len(rows)},
    }


def _compile_readonly_validation_audit(envelope: dict[str, Any]) -> dict[str, Any]:
    """Protocol compiler for ``(validation, readonly_audit_validation)``.

    Emits a single GET-only treatment step — no writes, no cleanup, no second
    actor. Expressions the audit layer cannot judge return a visible BLOCKED
    rather than falling through to the write-oriented validation chain.
    """
    operation = _dict(envelope.get("operation"))
    operation_ref = _text(envelope.get("operation_ref"))
    property_spec = _dict(envelope.get("property_spec"))
    method = _text(operation.get("method")).upper()
    if method not in {"GET", "HEAD"}:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_OPERATION",
            "detail": "readonly_audit_requires_read_operation",
        }
    actor = _text(
        envelope.get("treatment_actor_ref")
        or envelope.get("control_actor_ref")
        or property_spec.get("actor_ref")
    )
    if not actor:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "readonly_audit_actor",
        }
    expression = _dict(property_spec.get("expression"))
    expression_kind = _text(expression.get("kind")).lower()
    operator = _text(expression.get("operator")).lower()
    if expression_kind == "validation_uniqueness" or operator == "unique":
        qualifier, column = uniqueness_field_from_expression(expression)
        if not column:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "readonly_audit_field_ref_missing",
            }
        return {
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": actor,
                "operation_ref": operation_ref,
                "intent": "readonly_state_audit",
                "protocol_step": "readonly_audit_read",
                "property_template": PROTOCOL_TEMPLATE,
            }],
            "assertion": {
                "kind": ASSERTION_KIND,
                "field": column,
                "field_qualifier": qualifier,
                "invariant_ref": _text(property_spec.get("invariant_ref")),
            },
        }
    if (
        expression_kind in _NUMERIC_BOUNDARY_KINDS
        or operator in _NUMERIC_OPERATORS
    ):
        qualifier, column, boundary_operator = (
            _numeric_boundary_from_expression(expression)
        )
        if not column or boundary_operator not in _NUMERIC_OPERATORS:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "readonly_numeric_field_ref_missing",
            }
        return {
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": actor,
                "operation_ref": operation_ref,
                "intent": "readonly_numeric_audit",
                "protocol_step": "readonly_audit_read",
                "property_template": PROTOCOL_TEMPLATE,
            }],
            "assertion": {
                "kind": ASSERTION_KIND_NUMERIC,
                "field": column,
                "field_qualifier": qualifier,
                "operator": boundary_operator,
                "invariant_ref": _text(property_spec.get("invariant_ref")),
            },
        }
    # The audit layer currently judges uniqueness and numeric boundaries only.
    # Anything else must stay a visible gap, not a silently repurposed write
    # protocol.
    return {
        "status": "BLOCKED",
        "reason_code": "BLOCKED_UNSUPPORTED_AUDIT_EXPRESSION",
        "detail": f"readonly_audit_expression:{expression_kind or operator or 'unknown'}",
    }


def install_readonly_audit_protocol() -> dict[str, str]:
    """Register the audit assertion kind and protocol idempotently.

    The HTTP response surface writes ``status_code``/``body`` into the shared
    observation slots for every executed step; declaring those evidence keys on
    the ``http_response`` registry entry is what lets the kind-to-evidence
    contract accept this assertion kind instead of marking it unproducible.
    """
    from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
    from .experiment_protocol_registry import (
        register_family_protocol,
        resolve_family_protocol,
    )
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
            evaluator=_evaluate_readonly_uniqueness_audit,
            required_evidence_keys=("status_code", "body"),
        )
    else:
        installed["assertion"] = ASSERTION_KIND

    if ASSERTION_KIND_NUMERIC not in set(registered_assertion_kinds()):
        installed["assertion_numeric"] = register_assertion_kind(
            ASSERTION_KIND_NUMERIC,
            evaluator=_evaluate_readonly_numeric_audit,
            required_evidence_keys=("status_code", "body"),
        )
    else:
        installed["assertion_numeric"] = ASSERTION_KIND_NUMERIC

    if resolve_family_protocol(RISK_FAMILY, PROTOCOL_TEMPLATE) is None:
        installed["protocol"] = register_family_protocol(
            RISK_FAMILY,
            PROTOCOL_TEMPLATE,
            compiler=_compile_readonly_validation_audit,
            observers=("http_response",),
            assertion_kind=ASSERTION_KIND,
            emits_control=False,
        )
    else:
        installed["protocol"] = f"{RISK_FAMILY}:{PROTOCOL_TEMPLATE}"
    return installed

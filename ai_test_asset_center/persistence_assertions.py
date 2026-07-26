"""Assertion kinds over the persistence surface.

``persistence_observer`` can now measure database state, but an observer without an
assertion kind produces evidence nobody judges — the four-link chain still stops one link
short. These kinds close it for the first batch of persistence defect classes.

EVERY EXPECTATION IS SOURCE-DECLARED
====================================
None of these kinds infers what "correct" means. A state enumeration, a field bound and a
referenced table all have to arrive on the assertion property, having come from enterprise
material through Behavior IR. That is not a formality: an inferred enumeration would let the
product mark a legitimate state value as a defect, and an inferred bound would manufacture
violations out of ordinary data. When the declaration is absent the kind returns
INDETERMINATE with a named reason and judges nothing.

The registration order matters and is enforced by construction: ``register_assertion_kind``
refuses a kind whose required evidence key no registered observer declares it produces, so
``install_persistence_surface`` installs the observer first. Getting that backwards is
exactly the failure mode that left three built-in kinds permanently indeterminate.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from .persistence_observer import EVIDENCE_KEY, OBSERVER_ID, install_persistence_observer

KIND_STATE_ENUMERATION = "persisted_state_enumeration"
KIND_FIELD_BOUND = "persisted_field_bound"

# The risk family that routes an obligation to this surface (link 1 of the chain).
RISK_FAMILY = "persistence_integrity"
# Relation types an obligation in this family must join to in Behavior IR. A persistence
# check observes entity state produced by an operation, so it needs both.
RISK_FAMILY_RELATIONS = {"produces", "observes"}

# Cap on how many offending rows are named in a receipt. A violation needs enough evidence
# to be actionable, not the whole table.
MAX_REPORTED_OFFENDERS = 20


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _declared(spec: dict[str, Any], key: str) -> Any:
    """Read a declared value from either shape the compiler produces.

    ``evaluate_assertion`` passes the WHOLE assertion dict as ``spec``. The compiler
    spreads protocol assertion keys at that top level but nests the source-derived property
    spec under ``property``, so a declaration can legitimately live in either place.
    Reading only one of the two silently turns every declaration into "not declared".
    """
    source = _dict(spec)
    if key in source:
        return source[key]
    return _dict(source.get("property")).get(key)


def _observed_rows(observations: dict[str, Any]) -> tuple[list[dict[str, Any]], bool, list[str]]:
    """Flatten the per-module observations. Returns (rows, coverage_complete, modules)."""
    payload = _dict(_dict(observations).get(EVIDENCE_KEY))
    rows: list[dict[str, Any]] = []
    modules: list[str] = []
    for observation in _list(payload.get("observations")):
        entry = _dict(observation)
        module = _text(entry.get("module"))
        modules.append(module)
        for row in _list(entry.get("rows")):
            if isinstance(row, dict):
                rows.append({**row, "_module": module})
    truncated = any(
        bool(_dict(item).get("rows_truncated")) for item in _list(payload.get("observations"))
    )
    complete = bool(_dict(observations).get("coverage_complete")) and not truncated
    return rows, complete, modules


def _indeterminate(reason_code: str, **detail: Any) -> dict[str, Any]:
    return {"passed": None, "reason_code": reason_code, "expected": None, "actual": dict(detail)}


def evaluate_persisted_state_enumeration(envelope: dict[str, Any]) -> dict[str, Any]:
    """Every observed value of a declared field must be inside a declared enumeration.

    Refuses when the enumeration is not declared: without it there is no way to tell an
    illegal state from one this product has simply never seen.
    """
    spec = _dict(envelope.get("spec"))
    field = _text(_declared(spec, "persistence_state_field"))
    allowed_raw = _list(_declared(spec, "persistence_allowed_states"))
    allowed = {_text(value) for value in allowed_raw if _text(value)}

    if not field:
        return _indeterminate("PERSISTED_STATE_FIELD_NOT_DECLARED")
    if not allowed:
        return _indeterminate("PERSISTED_STATE_ENUMERATION_NOT_DECLARED", field=field)

    rows, complete, modules = _observed_rows(envelope.get("observations") or {})
    if not rows:
        return _indeterminate("PERSISTED_ROWS_NOT_OBSERVED", modules=modules)

    offenders: list[dict[str, Any]] = []
    for row in rows:
        if field not in row:
            # The observer was not asked to read the field the assertion judges. That is a
            # compile-time mismatch, not a defect in the target.
            return _indeterminate(
                "PERSISTED_STATE_FIELD_NOT_OBSERVED", field=field, observed_fields=sorted(
                    key for key in row if not key.startswith("_")
                )
            )
        value = _text(row.get(field))
        if value not in allowed:
            if len(offenders) < MAX_REPORTED_OFFENDERS:
                offenders.append({"module": row.get("_module"), field: value})

    if offenders:
        return {
            "passed": False,
            "reason_code": "",
            "expected": {"field": field, "allowed_states": sorted(allowed)},
            "actual": {
                "offending_row_count": len(offenders),
                "offending_rows": offenders,
                "modules_observed": modules,
            },
        }
    if not complete:
        # No offender found, but the reading was partial. "Nothing wrong in what we saw" is
        # not "nothing wrong", so this must not report PASS.
        return _indeterminate(
            "PERSISTED_COVERAGE_INCOMPLETE", field=field, modules_observed=modules
        )
    return {
        "passed": True,
        "reason_code": "",
        "expected": {"field": field, "allowed_states": sorted(allowed)},
        "actual": {"offending_row_count": 0, "modules_observed": modules},
    }


def evaluate_persisted_field_bound(envelope: dict[str, Any]) -> dict[str, Any]:
    """A declared numeric field must stay within a declared inclusive bound.

    Either bound may be omitted, but not both: a bound check with no bound declared is not
    a check.
    """
    spec = _dict(envelope.get("spec"))
    field = _text(_declared(spec, "persistence_bounded_field"))
    minimum = _declared(spec, "persistence_min")
    maximum = _declared(spec, "persistence_max")

    if not field:
        return _indeterminate("PERSISTED_BOUNDED_FIELD_NOT_DECLARED")
    if minimum is None and maximum is None:
        return _indeterminate("PERSISTED_BOUND_NOT_DECLARED", field=field)

    rows, complete, modules = _observed_rows(envelope.get("observations") or {})
    if not rows:
        return _indeterminate("PERSISTED_ROWS_NOT_OBSERVED", modules=modules)

    offenders: list[dict[str, Any]] = []
    for row in rows:
        if field not in row:
            return _indeterminate(
                "PERSISTED_BOUNDED_FIELD_NOT_OBSERVED", field=field, observed_fields=sorted(
                    key for key in row if not key.startswith("_")
                )
            )
        raw = row.get(field)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            # A non-numeric value in a field declared numeric is itself unjudgeable here.
            # Reporting it as out of bounds would be a fabricated verdict.
            return _indeterminate(
                "PERSISTED_FIELD_NOT_NUMERIC", field=field, observed_value=_text(raw)[:60]
            )
        below = minimum is not None and value < float(minimum)
        above = maximum is not None and value > float(maximum)
        if (below or above) and len(offenders) < MAX_REPORTED_OFFENDERS:
            offenders.append({"module": row.get("_module"), field: raw})

    bound = {"field": field, "min": minimum, "max": maximum}
    if offenders:
        return {
            "passed": False,
            "reason_code": "",
            "expected": bound,
            "actual": {
                "offending_row_count": len(offenders),
                "offending_rows": offenders,
                "modules_observed": modules,
            },
        }
    if not complete:
        return _indeterminate(
            "PERSISTED_COVERAGE_INCOMPLETE", field=field, modules_observed=modules
        )
    return {
        "passed": True,
        "reason_code": "",
        "expected": bound,
        "actual": {"offending_row_count": 0, "modules_observed": modules},
    }


def install_persistence_surface() -> dict[str, str]:
    """Install the persistence observer and its assertion kinds, in the required order.

    One entry point for both, because ``register_assertion_kind`` validates that some
    registered observer declares the evidence key each kind reads. Installing the kinds
    first would be refused — correctly — so the order is enforced here rather than left to
    the caller to remember.

    Idempotent: re-installing an already-registered surface is a no-op.
    """
    from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
    from .observer_contracts_base import OBSERVER_REGISTRY

    installed: dict[str, str] = {}
    if OBSERVER_ID not in OBSERVER_REGISTRY:
        installed["observer"] = install_persistence_observer()
    else:
        installed["observer"] = OBSERVER_ID

    already = set(registered_assertion_kinds())
    for kind, evaluator in (
        (KIND_STATE_ENUMERATION, evaluate_persisted_state_enumeration),
        (KIND_FIELD_BOUND, evaluate_persisted_field_bound),
    ):
        if kind in already:
            installed[kind] = kind
            continue
        installed[kind] = register_assertion_kind(
            kind, evaluator=evaluator, required_evidence_keys=(EVIDENCE_KEY,)
        )

    # Link 1: the risk family that routes an obligation here. Registered through the
    # descriptor entry point, so this adds a genuinely new bug class without editing any
    # by-family map by hand -- which is the whole point of that entry point.
    #
    # protocol_template reuses "state_transition": compile_family_protocol has no branch
    # for this family name and falls through to its generic single control + single
    # treatment plan, which is correct for a read-side check. An N-step persistence
    # protocol is a separate increment.
    from .test_obligation import canonical_risk_families, register_risk_family

    if RISK_FAMILY not in canonical_risk_families():
        installed["risk_family"] = register_risk_family(
            RISK_FAMILY,
            relation_types=RISK_FAMILY_RELATIONS,
            protocol_template="state_transition",
            observers=["http_response", OBSERVER_ID],
            assertion_kind=KIND_STATE_ENUMERATION,
        )
    else:
        installed["risk_family"] = RISK_FAMILY

    logger.info("persistence surface installed: %s", installed)
    return installed

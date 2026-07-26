"""Registry for experiment protocols, keyed by (risk family, template).

``compile_family_protocol`` is the last of the four capability registries that was still a
hardcoded if-chain: a linear ``if family == ...`` over six families plus a terminal
fallback, where every branch emits at most one control step and exactly one treatment step.
So a multi-step business process could not be expressed at all, and a family registered
through ``register_risk_family`` inherited the generic single-step fallback whether or not
that was the right shape for it.

WHY (family, template) IS THE RIGHT KEY, AND WHY IT NEEDS NO NEW PLUMBING
========================================================================
The hook already exists and is already exercised. ``compile_family_protocol`` reads
``template = property_spec.get("template")`` and dispatches on it for
``permitted_operation_invocation`` BEFORE the family chain — a template-keyed dispatch that
predates and outranks family dispatch.

And the template is source-declared end to end: ``register_risk_family`` writes
``_TEMPLATE_BY_FAMILY[family]``, which ``_planning_property`` applies with
``setdefault("template", ...)`` — setdefault, so an obligation carrying its own
``property.template`` keeps it. Nothing is inferred to reach a registered protocol; an
obligation selects one by declaring its template.

ADDITIVE, LIKE THE OTHER THREE
==============================
The consult lives in the OUTERMOST facade. On a miss the existing body runs verbatim, so all
six family branches, both actor guards and the terminal fallback behave exactly as today.
Two reasons it is the outermost facade and not the base:

* it leaves the 626-line if-chain unedited, and
* it bypasses the middle privacy facade, whose validation rewrite hard-requires exactly one
  control and one treatment step — an N-step registered protocol routed through it would be
  blocked by a guard written for a different purpose.

REGISTRATION VALIDATES UP FRONT
===============================
Every check happens at registration, not at compile or run time. This is the same lesson the
other three registries record: ``write_observer`` shipped as an OBSERVER_REGISTRY entry with
no dispatch branch, so it compiled, spent real target requests, and only then reported
UNSUPPORTED. A protocol that cannot produce a usable plan must be refused at the point
someone registers it.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Families whose protocol is implemented by the built-in if-chain. A registration may add a
# NEW template for one of these, but never take over the template the family compiles with
# today -- that would silently change behaviour for every existing obligation in it.
BUILTIN_PROTOCOL_FAMILIES = frozenset({
    "idempotency", "concurrency", "conservation", "temporal", "validation", "state",
})

# Template names the built-in chain already dispatches on, ahead of the family chain.
BUILTIN_TEMPLATES = frozenset({"permitted_operation_invocation"})

# The default template register_risk_family writes for each built-in family, which is the
# pair a registration must not shadow.
BUILTIN_FAMILY_TEMPLATES: dict[str, str] = {
    "idempotency": "idempotent_effect_cardinality",
    "concurrency": "concurrent_final_invariant",
    "conservation": "invariant_conservation",
    "temporal": "source_declared_temporal",
    "validation": "source_declared_validation",
    "state": "state_transition",
}

_REGISTERED_FAMILY_PROTOCOLS: dict[tuple[str, str], dict[str, Any]] = {}


class ProtocolRegistryError(ValueError):
    """A protocol registration cannot produce a usable experiment plan."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def register_family_protocol(
    family: str,
    template: str,
    *,
    compiler: Any,
    observers: "tuple[str, ...] | list[str]" = (),
    assertion_kind: str = "",
    emits_control: bool = False,
    per_step_evidence: bool = False,
) -> str:
    """Register a protocol for one (family, template) pair. Returns its id.

    ``compiler(envelope) -> dict`` receives ONE typed envelope (risk_family, operation,
    operation_ref, control_actor_ref, treatment_actor_ref, property_spec, behavior_ir) --
    the same single-envelope shape the observer and assertion-kind registries use -- and
    must return a protocol dict with ``status`` COMPILED or BLOCKED.

    ``emits_control`` declares whether the protocol produces a control leg. It defaults to
    False because an empty control leg is exempt from the delivery gate's
    control/treatment operation-symmetry comparison, so an N-step protocol stays out of a
    check written for the 1+1 shape unless it opts in.

    ``per_step_evidence`` declares that the protocol relies on per-step observation. The
    compiler refuses a multi-step plan that claims this without an observer able to supply
    it, rather than executing and losing the middle steps.

    Raises ProtocolRegistryError rather than deferring any failure:

    * a built-in (family, template) pair, because taking over the template a family
      compiles with today would silently change every existing obligation in it
    * an unregistered/unimplemented observer id, which would compile and then return
      UNSUPPORTED at observation time
    * an assertion kind whose required evidence no observer produces, which would execute
      and then die as a permanent INDETERMINATE
    """
    resolved_family = _text(family).lower()
    resolved_template = _text(template)
    if not resolved_family:
        raise ProtocolRegistryError("register_family_protocol requires a family")
    if not resolved_template:
        raise ProtocolRegistryError(
            f"protocol for family {resolved_family!r} requires a template; the template is "
            "how a source obligation selects a protocol"
        )
    if not callable(compiler):
        raise ProtocolRegistryError(
            f"protocol {resolved_family}:{resolved_template} requires a callable compiler"
        )
    if resolved_template in BUILTIN_TEMPLATES:
        raise ProtocolRegistryError(
            f"template {resolved_template!r} is dispatched by the built-in chain ahead of "
            "the family chain; choose a distinct template name"
        )
    if BUILTIN_FAMILY_TEMPLATES.get(resolved_family) == resolved_template:
        raise ProtocolRegistryError(
            f"({resolved_family}, {resolved_template}) is the built-in pair for that "
            "family; registering it would silently change every existing obligation in it. "
            "Register a new template name instead."
        )

    observer_ids = [_text(item) for item in observers if _text(item)]
    if observer_ids:
        from .observer_contracts_base import OBSERVER_REGISTRY

        unusable = [
            observer_id
            for observer_id in observer_ids
            if not isinstance(OBSERVER_REGISTRY.get(observer_id), dict)
            or OBSERVER_REGISTRY[observer_id].get("implemented") is not True
        ]
        if unusable:
            raise ProtocolRegistryError(
                f"protocol {resolved_family}:{resolved_template} declares observers that "
                f"are not registered and implemented: {sorted(unusable)}"
            )

    kind = _text(assertion_kind)
    if kind:
        from .assertion_dsl_base import unproducible_assertion_evidence

        missing = unproducible_assertion_evidence(kind)
        if missing:
            raise ProtocolRegistryError(
                f"protocol {resolved_family}:{resolved_template} declares assertion kind "
                f"{kind!r} whose required evidence {missing!r} no observer produces"
            )

    protocol_id = f"{resolved_family}:{resolved_template}"
    _REGISTERED_FAMILY_PROTOCOLS[(resolved_family, resolved_template)] = {
        "protocol_id": protocol_id,
        "compiler": compiler,
        "observers": list(observer_ids),
        "assertion_kind": kind,
        "emits_control": bool(emits_control),
        "per_step_evidence": bool(per_step_evidence),
    }
    return protocol_id


def resolve_family_protocol(family: str, template: str) -> dict[str, Any] | None:
    """The registered protocol for this pair, or None."""
    return _REGISTERED_FAMILY_PROTOCOLS.get((_text(family).lower(), _text(template)))


def registered_family_protocols() -> tuple[str, ...]:
    """Ids of every registered protocol."""
    return tuple(entry["protocol_id"] for entry in _REGISTERED_FAMILY_PROTOCOLS.values())


def validate_registered_protocol_result(
    result: Any, *, registration: dict[str, Any]
) -> dict[str, Any]:
    """Validate a registered compiler's output, or raise ProtocolRegistryError.

    A registered protocol is not trusted more than a built-in one. The plan shape is checked
    here so a malformed result becomes a visible BLOCKED at the call site rather than an
    exception escaping into the compile loop or, worse, a plan that executes and loses steps.
    """
    if not isinstance(result, dict):
        raise ProtocolRegistryError(
            f"returned {type(result).__name__}, expected a protocol dict"
        )
    status = _text(result.get("status")).upper()
    if status == "BLOCKED":
        # A registered protocol is allowed to refuse; that is fail-closed, not an error.
        if not _text(result.get("reason_code")):
            raise ProtocolRegistryError("BLOCKED result carries no reason_code")
        return dict(result)
    if status != "COMPILED":
        raise ProtocolRegistryError(f"status {status!r} is neither COMPILED nor BLOCKED")

    treatment = [row for row in _list(result.get("treatment_plan")) if isinstance(row, dict)]
    if not treatment:
        raise ProtocolRegistryError("COMPILED result has no treatment step")
    control = [row for row in _list(result.get("control_plan")) if isinstance(row, dict)]
    if control and not registration.get("emits_control"):
        raise ProtocolRegistryError(
            "emitted a control leg but was registered with emits_control=False; the "
            "delivery gate exempts an empty control leg from operation-symmetry checks, so "
            "the declaration and the plan must agree"
        )

    # Step identity is load-bearing downstream: contract subjects are derived from step_id
    # and de-duplicated, so an empty or repeated id silently collapses the required-subject
    # set and shifts every positional lookup after it.
    for phase, plan in (("control", control), ("treatment", treatment)):
        seen: set[str] = set()
        for row in plan:
            step_id = _text(row.get("step_id"))
            if not step_id:
                raise ProtocolRegistryError(f"{phase} step has no step_id")
            if step_id in seen:
                raise ProtocolRegistryError(f"{phase} step_id {step_id!r} is repeated")
            seen.add(step_id)
            if not _text(row.get("operation_ref")):
                raise ProtocolRegistryError(f"{phase} step {step_id!r} has no operation_ref")
            if not _text(row.get("actor_ref")):
                raise ProtocolRegistryError(f"{phase} step {step_id!r} has no actor_ref")

    if len(treatment) > 1 and not registration.get("per_step_evidence"):
        raise ProtocolRegistryError(
            f"emitted {len(treatment)} treatment steps but was registered with "
            "per_step_evidence=False; a multi-step plan without per-step observation loses "
            "every step except the first and last, and would report a verdict anyway"
        )
    return dict(result)

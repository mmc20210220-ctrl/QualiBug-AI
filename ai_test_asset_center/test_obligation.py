"""Test Obligation model — minimal planning unit for discovery.

Obligations are compiled from Behavior IR only. They never embed customer
instance answers or benchmark ground truth.
"""
from __future__ import annotations

import hashlib
from typing import Any


SCHEMA_VERSION = "qualibug.test-obligation.v1"

# ── Risk family registry ────────────────────────────────────────────────────
#
# The bug-type list is OPEN by contract (AGENTS.md, Enterprise Business
# Comprehension Contract): no fixed detector list, no closed taxonomy. So this is
# a seeded registry with a registration entry point, not a closed enumeration.
#
# Two rules make it lossless:
#   1. A declared family is NEVER rewritten in place. ``resolve_risk_family``
#      returns the canonical family used for compilation *alongside* the original
#      and a reason code, and ``make_obligation`` records all three. An
#      unrecognized family stays declared, is marked unregistered, and
#      ``make_obligation`` sets compile_status=BLOCKED — never coerced to
#      "validation" (that made the capability gap invisible in the data).
#   2. A family is canonical only when it is compilable END TO END. That means
#      obligation_source_adapter has an entry for it in all three of
#      _RELATION_TYPES_BY_FAMILY, _TEMPLATE_BY_FAMILY and _OBSERVERS_BY_FAMILY,
#      experiment_compiler_obligation._FAMILY_ASSERTION_KIND maps it to an
#      assertion kind, and the assertion_dsl facade chain can evaluate that kind.
#      Anything short of that is an alias with a reason code, never a canonical
#      family -- a family that reaches those maps without an entry raises KeyError,
#      and one that reaches the evaluator without a kind can never yield a verdict.
CANONICAL_RISK_FAMILIES = (
    "authorization",
    "isolation",
    "state",
    "conservation",
    "idempotency",
    "concurrency",
    "validation",
    "visibility",
    "temporal",
    "privacy",
)

# Alias map: an incoming family name -> the canonical family used to compile it.
# Aliasing is a deliberate, recorded narrowing, not a fallback. The keys here are
# the vocabularies actually produced elsewhere in the product -- primarily
# bug_ontology_registry.RISK_FAMILIES (12 families / 88 subtypes) and
# behavior_slice_gen. tests/test_risk_family_registry.py cross-checks that every
# ontology family resolves, so the two can never silently drift again.
# Every value here MUST be a member of CANONICAL_RISK_FAMILIES: an alias whose
# target is not canonical would reach obligation_source_adapter's by-family maps
# without an entry and raise KeyError. registry_self_check() enforces this.
RISK_FAMILY_ALIASES = {
    # --- generic synonyms, carried over from obligation_source_adapter ---
    "access_control": "authorization",
    "auth": "authorization",
    "permission": "authorization",
    "tenant": "isolation",
    "multi_tenant": "isolation",
    "money": "conservation",
    "inventory": "conservation",
    "stock": "conservation",
    "race": "concurrency",
    "business_rules": "validation",
    "cache": "validation",
    # --- bug_ontology_registry family ids ---
    # tenant_isolation was absent from the old alias map even though "isolation"
    # is a first-class family, so every tenant-isolation obligation compiled as a
    # generic validation check -- one of the product's headline defect classes,
    # silently downgraded. Expect the compiled count to move: "isolation" requires
    # owns/scopes relations where "validation" accepted almost anything, so an
    # obligation without that IR relation now becomes visibly
    # BLOCKED_MISSING_IR_RELATION instead of compiling into a check that could
    # never have detected an isolation defect. Visibly blocked beats wrongly
    # compiled.
    "tenant_isolation": "isolation",
    "state_machine": "state",
    # input_boundary genuinely IS validation semantics -- this one was already
    # landing correctly, by accident rather than by declaration.
    "input_boundary": "validation",
}

# Families the COMPILER and EVALUATOR understand -- entry in
# experiment_compiler_obligation._FAMILY_ASSERTION_KIND and an assertion_dsl
# evaluator alias -- but which obligation_source_adapter's three by-family maps do
# not cover. They resolve to a canonical family (preserving today's execution) and
# are tagged so the gap is countable. Promoting one means adding its relation
# types, protocol template and observer set to those three maps; the counts this
# tagging produces are what should decide the order.
# NOTE ON TARGETS: each canonical target below is deliberately the SAME family the
# old coercion produced, so this change adds visibility without moving execution.
# "state_integrity" is the exception: it was a promotion candidate that measured
# exactly the failure this tag existed to surface. Its obligations come from
# source-declared state-machine transition edges (entity_relation:transitions),
# and the state protocol compiles them end to end (transitions relation type,
# state_transition assertion kind, before/after_state observers) -- the same
# shape the "state_machine" alias already resolves to. Retargeting it from
# validation to state was previously deferred because it narrows the required
# IR relations; the transition edges it is generated from satisfy that
# requirement, and the validation fallback could only compile the edge as a
# field-mutation probe against a guessed operation -- a wrong operation (the
# entity-co-reference fallback) under a wrong protocol (no example/schema
# material for a transition edge). The remaining candidates stay on their
# historical targets until their own counts justify promotion.
PROMOTION_CANDIDATE_FAMILIES = {
    "state_integrity": "state",
    "lifecycle": "state",
    "invariant": "validation",
    "consistency": "validation",
    "data_integrity": "validation",
    "eventual_consistency": "validation",
}

# Declared and recognized, but the evidence cannot be observed at all: no
# assertion kind and no implemented observer exist. Resolves to a canonical family
# so existing execution is preserved, with the gap recorded rather than hidden.
# Closing one requires all four links from AGENTS.md, observer included.
CAPABILITY_GAP_FAMILIES = {
    "audit_trail": "validation",
}

RISK_FAMILY_UNREGISTERED_REASON = "RISK_FAMILY_NOT_REGISTERED"
RISK_FAMILY_ALIASED_REASON = "RISK_FAMILY_ALIASED"
RISK_FAMILY_PROMOTION_CANDIDATE_REASON = "RISK_FAMILY_PROMOTION_CANDIDATE"
RISK_FAMILY_CAPABILITY_GAP_REASON = "RISK_FAMILY_OBSERVER_CAPABILITY_MISSING"

_REGISTERED_RISK_FAMILIES: dict[str, str] = {}


# Families promoted to canonical at runtime through a full descriptor. Kept separate
# from the CANONICAL_RISK_FAMILIES literal so the built-in set stays readable.
_RUNTIME_CANONICAL_FAMILIES: dict[str, dict[str, Any]] = {}


def canonical_risk_families() -> tuple[str, ...]:
    """Built-in canonical families plus any registered at runtime."""
    return CANONICAL_RISK_FAMILIES + tuple(sorted(_RUNTIME_CANONICAL_FAMILIES))


def register_risk_family(
    name: str,
    *,
    canonical: str | None = None,
    relation_types: "set[str] | frozenset[str] | list[str] | None" = None,
    protocol_template: str | None = None,
    observers: "list[str] | None" = None,
    assertion_kind: str | None = None,
) -> str:
    """Register a risk family at runtime. This is the open entry point.

    Two modes:

    * ALIAS -- pass ``canonical``. The new name compiles as an existing canonical
      family. Cheap, and correct when the new label is a synonym or a narrower case.

    * DESCRIPTOR -- pass ``relation_types`` + ``protocol_template`` + ``observers``
      (and optionally ``assertion_kind``). The family becomes canonical in its own
      right and this function writes the downstream by-family maps for it, so adding
      a genuinely new bug class needs no edit to core code. That is the point: the
      bug-type list is open by contract, and a taxonomy that requires editing five
      hand-maintained maps per addition is a structural ceiling wearing a registry's
      clothes.

    Every link is validated HERE rather than deferred, because each deferred failure
    has a known bad shape in this codebase:

    * an unregistered observer id compiles to BLOCKED_MISSING_OBSERVER, which silently
      killed 3 of the 10 built-in families ("resource_visibility", "clock",
      "privacy_surface" were never registered)
    * a missing by-family map entry raises KeyError deep inside compilation
    * an assertion kind whose evidence nothing produces executes and then dies as a
      permanent INDETERMINATE

    Imports are deferred to call time: obligation_source_adapter imports this module,
    so a module-level import here would be circular, and AGENTS.md requires importing
    the package to stay side-effect free.
    """
    family = _text(name).lower()
    if not family:
        raise ValueError("register_risk_family requires a non-empty name")

    descriptor_given = any(
        value is not None for value in (relation_types, protocol_template, observers)
    )
    if descriptor_given and canonical:
        raise ValueError(
            "register_risk_family takes either canonical= (alias mode) or a full "
            "descriptor, not both"
        )

    if not descriptor_given:
        target = _text(canonical).lower() or family
        if target not in canonical_risk_families():
            raise ValueError(
                f"canonical risk family {target!r} is not canonical; either register "
                "it first with a full descriptor (relation_types, protocol_template, "
                "observers) or alias onto an existing canonical family"
            )
        _REGISTERED_RISK_FAMILIES[family] = target
        return target

    # ── Descriptor mode: validate all four links before anything is written ──
    relations = {_text(item) for item in (relation_types or set()) if _text(item)}
    if not relations:
        raise ValueError(
            f"risk family {family!r} needs at least one IR relation type; without one "
            "every obligation in it blocks with BLOCKED_MISSING_IR_RELATION"
        )
    template = _text(protocol_template)
    if not template:
        raise ValueError(f"risk family {family!r} needs a protocol_template")
    observer_ids = [_text(item) for item in (observers or []) if _text(item)]
    if not observer_ids:
        raise ValueError(
            f"risk family {family!r} needs at least one observer; an obligation with "
            "no observer cannot produce evidence"
        )

    from .observer_contracts_base import OBSERVER_REGISTRY

    unknown = [
        observer_id
        for observer_id in observer_ids
        if not isinstance(OBSERVER_REGISTRY.get(observer_id), dict)
        or OBSERVER_REGISTRY[observer_id].get("implemented") is not True
    ]
    if unknown:
        raise ValueError(
            f"risk family {family!r} declares observers that are not registered and "
            f"implemented in OBSERVER_REGISTRY: {sorted(unknown)}"
        )

    kind = _text(assertion_kind)
    if kind:
        from .assertion_dsl_base import unproducible_assertion_evidence

        missing = unproducible_assertion_evidence(kind)
        if missing:
            raise ValueError(
                f"risk family {family!r} declares assertion kind {kind!r} whose "
                f"required evidence {missing!r} no observer produces; implement the "
                "producer first or the family can never return a verdict"
            )

    from . import obligation_source_adapter as _adapter

    _adapter._RELATION_TYPES_BY_FAMILY[family] = relations
    _adapter._TEMPLATE_BY_FAMILY[family] = template
    _adapter._OBSERVERS_BY_FAMILY[family] = list(observer_ids)
    if kind:
        from . import experiment_compiler_obligation as _compiler

        _compiler._FAMILY_ASSERTION_KIND.setdefault(family, kind)

    _RUNTIME_CANONICAL_FAMILIES[family] = {
        "relation_types": sorted(relations),
        "protocol_template": template,
        "observers": list(observer_ids),
        "assertion_kind": kind,
    }
    return family


def registered_risk_families() -> tuple[str, ...]:
    """Every family name that resolves today, canonical plus alias plus runtime."""
    return tuple(sorted(
        set(CANONICAL_RISK_FAMILIES)
        | set(RISK_FAMILY_ALIASES)
        | set(PROMOTION_CANDIDATE_FAMILIES)
        | set(CAPABILITY_GAP_FAMILIES)
        | set(_REGISTERED_RISK_FAMILIES)
        | set(_RUNTIME_CANONICAL_FAMILIES)
    ))


def registry_self_check() -> None:
    """Fail fast if any resolution target is not compilable.

    Every alias / promotion-candidate / capability-gap entry must point at a
    canonical family. A non-canonical target would reach
    obligation_source_adapter's _RELATION_TYPES_BY_FAMILY, _TEMPLATE_BY_FAMILY and
    _OBSERVERS_BY_FAMILY without an entry and raise KeyError deep inside
    compilation, which is exactly the kind of late, opaque failure the fail-fast
    rule exists to prevent. Called by tests/test_risk_family_registry.py.
    """
    bad: list[str] = []
    for label, mapping in (
        ("RISK_FAMILY_ALIASES", RISK_FAMILY_ALIASES),
        ("PROMOTION_CANDIDATE_FAMILIES", PROMOTION_CANDIDATE_FAMILIES),
        ("CAPABILITY_GAP_FAMILIES", CAPABILITY_GAP_FAMILIES),
        ("_REGISTERED_RISK_FAMILIES", _REGISTERED_RISK_FAMILIES),
    ):
        _canonical = canonical_risk_families()
        for name, target in mapping.items():
            if target not in _canonical:
                bad.append(f"{label}[{name!r}] -> {target!r}")
    if bad:
        raise ValueError(
            "risk family resolution targets must be canonical: " + "; ".join(sorted(bad))
        )


def resolve_risk_family(risk_family: Any) -> dict[str, Any]:
    """Resolve a declared family losslessly.

    Returns ``{declared, canonical, registered, reason_code}``. The declared value
    is always preserved. ``reason_code`` is "" for a first-class family, and
    otherwise names why the canonical family differs -- so aliasing, capability
    gaps and genuinely unknown families are all countable in the run data instead
    of collapsing indistinguishably into "validation".
    """
    declared = _text(risk_family).lower()
    if declared in canonical_risk_families():
        return {"declared": declared, "canonical": declared, "registered": True, "reason_code": ""}
    if declared in _REGISTERED_RISK_FAMILIES:
        return {
            "declared": declared,
            "canonical": _REGISTERED_RISK_FAMILIES[declared],
            "registered": True,
            "reason_code": RISK_FAMILY_ALIASED_REASON,
        }
    if declared in RISK_FAMILY_ALIASES:
        return {
            "declared": declared,
            "canonical": RISK_FAMILY_ALIASES[declared],
            "registered": True,
            "reason_code": RISK_FAMILY_ALIASED_REASON,
        }
    if declared in PROMOTION_CANDIDATE_FAMILIES:
        return {
            "declared": declared,
            "canonical": PROMOTION_CANDIDATE_FAMILIES[declared],
            "registered": True,
            "reason_code": RISK_FAMILY_PROMOTION_CANDIDATE_REASON,
        }
    if declared in CAPABILITY_GAP_FAMILIES:
        return {
            "declared": declared,
            "canonical": CAPABILITY_GAP_FAMILIES[declared],
            "registered": True,
            "reason_code": RISK_FAMILY_CAPABILITY_GAP_REASON,
        }
    # Never coerce unknown families to "validation" — that made breadth loss
    # invisible. Preserve the declared name and mark unregistered so
    # make_obligation can BLOCK visibly.
    return {
        "declared": declared,
        "canonical": declared,
        "registered": False,
        "reason_code": RISK_FAMILY_UNREGISTERED_REASON,
    }


# Backward compatibility. Existing call sites test ``family in RISK_FAMILIES``;
# they keep working and now accept the promoted families too. New code should call
# resolve_risk_family() so the declared value and reason code are not discarded.
RISK_FAMILIES = CANONICAL_RISK_FAMILIES

COMPILE_STATUSES = ("PENDING", "COMPILED", "BLOCKED", "UNSUPPORTED")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def stable_obligation_id(*parts: Any) -> str:
    raw = "|".join(_text(p) for p in parts if _text(p))
    return f"obl_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def make_obligation(
    *,
    risk_family: str,
    subject_refs: list[str],
    property_spec: dict[str, Any],
    required_actors: list[str] | None = None,
    required_operations: list[str] | None = None,
    required_fixtures: list[str] | None = None,
    required_observers: list[str] | None = None,
    cleanup_requirement: dict[str, Any] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    relation_refs: list[str] | None = None,
    confidence: float = 0.5,
    compile_status: str = "PENDING",
    obligation_id: str | None = None,
) -> dict[str, Any]:
    resolution = resolve_risk_family(risk_family)
    family = resolution["canonical"]
    status = compile_status if compile_status in COMPILE_STATUSES else "PENDING"
    # Unknown families fail visibly as BLOCKED — never rewrite compile authority
    # to validation (AGENTS.md Enterprise Business Comprehension Contract).
    if (
        resolution.get("registered") is False
        and _text(resolution.get("reason_code")) == RISK_FAMILY_UNREGISTERED_REASON
    ):
        family = resolution["declared"] or _text(risk_family).lower()
        status = "BLOCKED"
    oid = _text(obligation_id) or stable_obligation_id(
        family,
        ",".join(sorted(_text(x) for x in subject_refs if _text(x))),
        json_fingerprint(property_spec),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "obligation_id": oid,
        "risk_family": family,
        # The declared family and the reason it was narrowed are retained so a
        # breadth gap is measurable. Only ``risk_family`` drives compilation.
        "declared_risk_family": resolution["declared"],
        "risk_family_resolution": dict(resolution),
        "subject_refs": [ _text(x) for x in subject_refs if _text(x) ],
        "property": dict(property_spec or {}),
        "required_actors": [ _text(x) for x in (required_actors or []) if _text(x) ],
        "required_operations": [ _text(x) for x in (required_operations or []) if _text(x) ],
        "required_fixtures": [ _text(x) for x in (required_fixtures or []) if _text(x) ],
        "required_observers": [ _text(x) for x in (required_observers or []) if _text(x) ],
        "cleanup_requirement": dict(cleanup_requirement or {}),
        "source_refs": list(source_refs or []),
        "relation_refs": [_text(x) for x in (relation_refs or []) if _text(x)],
        "confidence": max(0.0, min(1.0, float(confidence))),
        "compile_status": status,
        "block_reason": (
            RISK_FAMILY_UNREGISTERED_REASON
            if status == "BLOCKED"
            and _text(resolution.get("reason_code")) == RISK_FAMILY_UNREGISTERED_REASON
            else ""
        ),
    }


def json_fingerprint(value: Any) -> str:
    import json

    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def dedupe_obligations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _text(item.get("obligation_id")) or json_fingerprint(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out

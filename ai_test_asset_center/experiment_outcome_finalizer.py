"""Canonical outcome-aware experiment finalization authority.

Exact process-step scoping remains in the existing compatibility facade. One execution may
prove several independent outcome violations; this module fans them out into deterministic
finding occurrences while preserving the aggregate Oracle receipt for audit.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from . import observer_contracts as _outcome_observers  # noqa: F401
from . import assertion_dsl as _outcome_assertions  # noqa: F401
from . import contract_oracles as _outcome_oracles
from . import _experiment_outcome_finalizer_scope_mechanics as _scope
from ._experiment_outcome_finalizer_scope_mechanics import *  # noqa: F401,F403

_original_finalize_experiment_execution = _scope.finalize_experiment_execution
# Supported composition points. Scope mechanics remains the sole implementation
# and these callables are synchronized for the duration of finalization.
observe_experiment_requirements = _scope._original_observe_experiment_requirements
evaluate_contract_oracle = _outcome_oracles.evaluate_contract_oracle
evaluate_cleanup_equivalence = _scope._original_evaluate_cleanup_equivalence

FinalizerContinuation = Callable[
    [tuple[Any, ...], dict[str, Any]], dict[str, Any]
]
FinalizerHook = Callable[
    [FinalizerContinuation, tuple[Any, ...], dict[str, Any]], dict[str, Any]
]
EvaluatorContinuation = Callable[..., dict[str, Any]]
EvaluatorHook = Callable[
    [EvaluatorContinuation, tuple[Any, ...], dict[str, Any]], dict[str, Any]
]

_FINALIZER_HOOKS: dict[str, FinalizerHook] = {}
_CONTRACT_ORACLE_HOOKS: dict[str, EvaluatorHook] = {}
_CLEANUP_EQUIVALENCE_HOOKS: dict[str, EvaluatorHook] = {}


def _register_hook(
    registry: dict[str, Callable[..., Any]],
    name: str,
    hook: Callable[..., Any] | None,
    *,
    registry_name: str,
) -> None:
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise ValueError(f"{registry_name} name must not be empty")
    if hook is None:
        registry.pop(normalized_name, None)
        return
    if not callable(hook):
        raise TypeError(f"{registry_name} hook must be callable")
    existing = registry.get(normalized_name)
    if existing is not None and existing is not hook:
        raise RuntimeError(
            f"{registry_name} hook already registered: {normalized_name}"
        )
    registry[normalized_name] = hook


def register_finalizer_hook(name: str, hook: FinalizerHook | None) -> None:
    """Register one explicit around-finalization hook on the canonical Finalizer.

    A hook receives ``(next_call, args, kwargs)`` and must return the result of
    that call, optionally enriched with evidence that it actually observed.
    Hook registration is the only supported bridge-composition mechanism; no
    bridge may replace the public Finalizer or Executor symbol.
    """
    _register_hook(
        _FINALIZER_HOOKS,
        name,
        hook,
        registry_name="finalizer",
    )


def register_contract_oracle_hook(name: str, hook: EvaluatorHook | None) -> None:
    """Register an explicit Contract Oracle composition hook."""
    _register_hook(
        _CONTRACT_ORACLE_HOOKS,
        name,
        hook,
        registry_name="contract_oracle",
    )


def register_cleanup_equivalence_hook(
    name: str, hook: EvaluatorHook | None
) -> None:
    """Register an explicit cleanup-equivalence composition hook."""
    _register_hook(
        _CLEANUP_EQUIVALENCE_HOOKS,
        name,
        hook,
        registry_name="cleanup_equivalence",
    )


def finalizer_hook_names() -> tuple[str, ...]:
    return tuple(sorted(_FINALIZER_HOOKS))


def contract_oracle_hook_names() -> tuple[str, ...]:
    return tuple(sorted(_CONTRACT_ORACLE_HOOKS))


def cleanup_equivalence_hook_names() -> tuple[str, ...]:
    return tuple(sorted(_CLEANUP_EQUIVALENCE_HOOKS))


def _compose_evaluator_hooks(
    base: EvaluatorContinuation,
    registry: dict[str, EvaluatorHook],
) -> EvaluatorContinuation:
    continuation = base
    for name in reversed(sorted(registry)):
        hook = registry[name]
        next_call = continuation

        def invoke(
            *args: Any,
            _hook: EvaluatorHook = hook,
            _next_call: EvaluatorContinuation = next_call,
            **kwargs: Any,
        ) -> dict[str, Any]:
            result = _hook(_next_call, tuple(args), dict(kwargs))
            if not isinstance(result, dict):
                raise TypeError(
                    f"evaluator hook returned non-dict result: {type(result)!r}"
                )
            return result

        continuation = invoke
    return continuation


def _run_finalizer_hooks(
    base: FinalizerContinuation,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    continuation = base
    for name in reversed(sorted(_FINALIZER_HOOKS)):
        hook = _FINALIZER_HOOKS[name]
        next_call = continuation

        def invoke(
            call_args: tuple[Any, ...],
            call_kwargs: dict[str, Any],
            _hook: FinalizerHook = hook,
            _next_call: FinalizerContinuation = next_call,
        ) -> dict[str, Any]:
            result = _hook(_next_call, call_args, call_kwargs)
            if not isinstance(result, dict):
                raise TypeError(
                    f"finalizer hook returned non-dict result: {type(result)!r}"
                )
            return result

        continuation = invoke
    return continuation(args, kwargs)


def __getattr__(name: str) -> Any:
    return getattr(_scope, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _source_rule_identity(exp: dict[str, Any]) -> dict[str, str]:
    """Return the compiled source rule identity carried by one experiment.

    The Contract Oracle deliberately derives canonical identity from receipts,
    not titles.  Customer-visible findings still need the source business rule
    that made the experiment meaningful; otherwise several distinct rules on
    one endpoint collapse to the same generic ``kind + method + path`` wording.

    Only compiler-carried rule identity plus grounded source references are
    accepted.  No response text, runtime guess, or evaluator data participates.
    """

    source_refs = [
        row
        for row in _list(_dict(exp).get("source_refs"))
        if isinstance(row, dict)
        and _text(
            row.get("locator")
            or row.get("source_locator")
            or row.get("source_id")
            or row.get("id")
        )
    ]
    if not source_refs:
        return {}

    for raw_assertion in _list(_dict(exp).get("assertions")):
        assertion = _dict(raw_assertion)
        prop = _dict(assertion.get("property"))
        field_binding = _dict(prop.get("field_rule_binding"))
        rule_ref = _text(
            field_binding.get("rule_id")
            or prop.get("invariant_ref")
            or assertion.get("rule_id")
            or assertion.get("invariant_ref")
        )
        expression = _dict(
            prop.get("expression")
            or field_binding.get("typed_expression")
        )
        statement = _text(
            expression.get("raw")
            or field_binding.get("statement")
            or prop.get("description")
            or assertion.get("description")
        )
        statement = " ".join(statement.split())[:240]
        if rule_ref and statement:
            return {
                "source_rule_ref": rule_ref,
                "source_rule_statement": statement,
                "source_rule_identity_basis": (
                    "compiled_property_expression_and_source_refs"
                ),
            }
    return {}


def _attach_source_rule_identity(
    result: dict[str, Any],
    exp: dict[str, Any],
) -> dict[str, Any]:
    """Project a grounded rule identity onto final finding occurrences.

    This changes only customer-readable identity and evidence metadata.  Oracle
    status, assertion receipts, canonical defect derivation, and delivery gates
    remain untouched.
    """

    identity = _source_rule_identity(exp)
    governed = dict(_dict(result))

    def _enrich(source: dict[str, Any]) -> dict[str, Any]:
        # ── Source contract + runtime evidence paragraphs ──
        # The single-statement identity above is the canonical-title contract
        # and stays.  Every bound rule statement (all assertions, not only the
        # first) is additionally carried as a 源契约 paragraph, and the
        # observed runtime evidence (actor, path, dual-arm outcome,
        # reproduction, observed response) as a 运行时证据 paragraph.
        # Obligations without bound rules contribute no 源契约 text — nothing
        # is ever invented.
        from .finding_source_contract import enrich_governed_result

        return enrich_governed_result(source, exp=exp)

    if not identity:
        return _enrich(governed)

    findings = [
        dict(row)
        for row in _list(governed.get("findings"))
        if isinstance(row, dict)
    ]
    if not findings and _dict(governed.get("finding")):
        findings = [dict(_dict(governed.get("finding")))]
    if not findings:
        return governed

    statement = identity["source_rule_statement"]
    enriched: list[dict[str, Any]] = []
    for raw_finding in findings:
        finding = dict(raw_finding)
        evidence = dict(_dict(finding.get("evidence")))
        request = _text(evidence.get("request"))
        category = _text(finding.get("category") or "contract")
        technical_identity = " ".join(
            value for value in (category, request) if value
        )
        finding.update(identity)
        finding["title"] = (
            f"[ContractOracle] {statement}: "
            f"{technical_identity or identity['source_rule_ref']}"
        )
        existing_description = _text(finding.get("description"))
        prefix = f"Source rule violated: {statement}."
        finding["description"] = (
            f"{prefix} {existing_description}"
            if existing_description and not existing_description.startswith(prefix)
            else existing_description or prefix
        )
        evidence.update(identity)
        finding["evidence"] = evidence
        enriched.append(finding)

    governed["findings"] = enriched
    if _dict(governed.get("finding")):
        governed["finding"] = enriched[0]
    return _enrich(governed)


def _finding_for_assertion(
    base_finding: dict[str, Any],
    assertion: dict[str, Any],
    *,
    outcome_ref: str,
    oracle_receipt: dict[str, Any],
    occurrence_index: int,
    occurrence_count: int,
) -> dict[str, Any]:
    finding = deepcopy(base_finding)
    kind = _text(assertion.get("kind") or "contract")
    title = _text(finding.get("title"))
    suffix = title.split(":", 1)[1].strip() if ":" in title else title
    reason = _text(assertion.get("error") or assertion.get("reason_code"))
    description = reason or (
        f"mandatory outcome {outcome_ref} violated typed assertion {kind}"
    )
    finding.update(
        {
            "title": f"[ContractOracle] {kind}: {suffix or outcome_ref}",
            "description": description,
            "category": kind,
            "risk_family": (
                "authorization" if kind == "owner_tenant_visibility" else kind
            ),
            "outcome_ref": outcome_ref,
            "oracle_template_ref": _text(assertion.get("oracle_template_ref")),
            "assertion_requirement_ref": _text(
                assertion.get("assertion_requirement_ref")
            ),
            "assertion_receipt_id": _text(assertion.get("receipt_id")),
            "canonical_outcome_identity_bound": True,
            "outcome_occurrence_index": occurrence_index,
            "outcome_occurrence_count": occurrence_count,
            "expected": assertion.get("expected"),
            "actual": assertion.get("actual"),
            "failed_assertions": [dict(assertion)],
        }
    )
    oracle_summary = dict(_dict(finding.get("oracle")))
    oracle_summary.update(
        {
            "outcome_ref": outcome_ref,
            "assertion_receipt_id": _text(assertion.get("receipt_id")),
            "receipt_id": _text(oracle_receipt.get("receipt_id")),
            "activation_receipt_id": _text(
                oracle_receipt.get("activation_receipt_id")
            ),
            "canonical_outcome_identity_bound": True,
            "parent_oracle_receipt_id": _text(
                oracle_receipt.get("parent_oracle_receipt_id")
            ),
        }
    )
    finding["oracle"] = oracle_summary
    finding["oracle_receipt_id"] = _text(oracle_receipt.get("receipt_id"))
    finding["activation_receipt_id"] = _text(
        oracle_receipt.get("activation_receipt_id")
    )

    evidence = dict(_dict(finding.get("evidence")))
    evidence.update(
        {
            "outcome_ref": outcome_ref,
            "assertion_receipt_id": _text(assertion.get("receipt_id")),
            "assertion": dict(assertion),
            "oracle_receipt_id": _text(oracle_receipt.get("receipt_id")),
        }
    )
    finding["evidence"] = evidence

    raw_evidence = dict(_dict(finding.get("raw_evidence")))
    raw_evidence.update(
        {
            "outcome_ref": outcome_ref,
            "assertion_receipt_id": _text(assertion.get("receipt_id")),
            "oracle_receipt_id": _text(oracle_receipt.get("receipt_id")),
        }
    )
    db_snapshot = dict(_dict(raw_evidence.get("db_snapshot")))
    db_snapshot["assertion"] = dict(assertion)
    raw_evidence["db_snapshot"] = db_snapshot
    finding["raw_evidence"] = raw_evidence
    return finding


def _fanout_finding_outcomes(result: dict[str, Any]) -> dict[str, Any]:
    governed = dict(result)
    aggregate = _dict(governed.get("oracle_verdict"))
    base_finding = _dict(governed.get("finding"))
    if not bool(aggregate.get("canonical_outcome_identity_required")):
        governed["findings"] = [dict(base_finding)] if base_finding else []
        return governed

    if _text(aggregate.get("status")) != "VIOLATION":
        governed["finding"] = None
        governed["findings"] = []
        return governed

    violation_refs = sorted(
        {
            _text(value)
            for value in _list(aggregate.get("violation_outcome_refs"))
            if _text(value)
        }
    )
    if not violation_refs or not base_finding:
        governed.update(
            {
                "finding": None,
                "findings": [],
                "status": "BLOCKED",
                "reason_code": "BLOCKED_CANONICAL_OUTCOME_IDENTITY_INCOMPLETE",
                "detail": "violated outcome refs or finding template missing",
            }
        )
        return governed

    assertions = [
        _dict(row)
        for row in _list(aggregate.get("assertions"))
        if isinstance(row, dict) and _text(_dict(row).get("status")) == "VIOLATION"
    ]
    oracle_receipts: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for index, outcome_ref in enumerate(violation_refs, start=1):
        matches = [
            row for row in assertions if _text(row.get("outcome_ref")) == outcome_ref
        ]
        if len(matches) != 1:
            governed.update(
                {
                    "finding": None,
                    "findings": [],
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_AMBIGUOUS_OUTCOME_FINDING",
                    "detail": (
                        "each violated outcome requires exactly one assertion receipt"
                    ),
                }
            )
            return governed
        oracle = _outcome_oracles.project_contract_oracle_for_outcome(
            aggregate, outcome_ref
        )
        oracle_receipts.append(oracle)
        findings.append(
            _finding_for_assertion(
                base_finding,
                matches[0],
                outcome_ref=outcome_ref,
                oracle_receipt=oracle,
                occurrence_index=index,
                occurrence_count=len(violation_refs),
            )
        )

    governed["aggregate_oracle_verdict"] = aggregate
    governed["outcome_oracle_receipts"] = oracle_receipts
    governed["oracle_verdict"] = oracle_receipts[0]
    governed["findings"] = findings
    governed["finding"] = findings[0]
    governed["outcome_fanout"] = {
        "status": "FANNED_OUT" if len(findings) > 1 else "SINGLE",
        "occurrence_count": len(findings),
        "outcome_refs": violation_refs,
        "aggregate_oracle_receipt_id": _text(aggregate.get("receipt_id")),
        "oracle_receipt_ids": [
            _text(row.get("receipt_id")) for row in oracle_receipts
        ],
        "legacy_finding_is_projection": True,
    }
    return governed


def _normalize_experiment_outcome_identity(exp: dict[str, Any]) -> dict[str, Any]:
    governed = dict(_dict(exp))
    assertions = [
        dict(row) for row in _list(governed.get("assertions")) if isinstance(row, dict)
    ]
    explicit_refs = sorted(
        {
            _text(row.get("outcome_ref"))
            for row in assertions
            if row.get("mandatory") is not False and _text(row.get("outcome_ref"))
        }
    )
    if not explicit_refs:
        return governed
    governed["canonical_outcome_identity_required"] = True
    governed["mandatory_outcome_refs"] = explicit_refs
    observer_to_refs: dict[str, set[str]] = {}
    normalized_assertions: list[dict[str, Any]] = []
    for row in assertions:
        assertion = dict(row)
        outcome_ref = _text(assertion.get("outcome_ref"))
        if outcome_ref:
            assertion["canonical_outcome_identity_required"] = True
            assertion.setdefault("semantic_role", "MANDATORY_OUTCOME")
            direct_observer = _text(assertion.get("observer_id"))
            if direct_observer:
                observer_to_refs.setdefault(direct_observer, set()).add(outcome_ref)
            for requirement in _list(assertion.get("observer_requirements")):
                requirement_row = _dict(requirement)
                observer_id = _text(requirement_row.get("observer_id"))
                if observer_id:
                    observer_to_refs.setdefault(observer_id, set()).add(outcome_ref)
        normalized_assertions.append(assertion)
    governed["assertions"] = normalized_assertions
    normalized_observers: list[dict[str, Any]] = []
    for raw in _list(governed.get("observers")):
        if not isinstance(raw, dict):
            continue
        observer = dict(raw)
        observer_id = _text(observer.get("observer_id"))
        refs = observer_to_refs.get(observer_id, set())
        if not _text(observer.get("outcome_ref")) and len(refs) == 1:
            observer["outcome_ref"] = next(iter(refs))
            observer.setdefault("semantic_role", "MANDATORY_OUTCOME")
        normalized_observers.append(observer)
    governed["observers"] = normalized_observers
    return governed


def finalize_experiment_execution(*args: Any, **kwargs: Any) -> dict[str, Any]:
    # Synchronize supported public composition points without introducing a
    # second Finalizer implementation.
    _scope._original_observe_experiment_requirements = (
        observe_experiment_requirements
    )
    _scope._original_evaluate_contract_oracle = _compose_evaluator_hooks(
        evaluate_contract_oracle,
        _CONTRACT_ORACLE_HOOKS,
    )
    _scope._original_evaluate_cleanup_equivalence = (
        _compose_evaluator_hooks(
            evaluate_cleanup_equivalence,
            _CLEANUP_EQUIVALENCE_HOOKS,
        )
    )
    call_kwargs = dict(kwargs)
    if isinstance(call_kwargs.get("exp"), dict):
        call_kwargs["exp"] = _normalize_experiment_outcome_identity(call_kwargs["exp"])

    def base_finalize(
        call_args: tuple[Any, ...], call_kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        result = _original_finalize_experiment_execution(
            *call_args,
            **call_kwargs,
        )
        governed = _fanout_finding_outcomes(_dict(result))
        return _attach_source_rule_identity(
            governed,
            _dict(call_kwargs.get("exp")),
        )

    return _run_finalizer_hooks(base_finalize, tuple(args), call_kwargs)


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name
    not in {
        "_scope",
        "_outcome_observers",
        "_outcome_assertions",
        "_outcome_oracles",
    }
)

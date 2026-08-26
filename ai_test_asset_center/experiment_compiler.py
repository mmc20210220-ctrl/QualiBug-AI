"""Scope source conflicts and preserve single-actor privacy field checks."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import experiment_compiler_conflict_base as _base
from .authorization_comparison_contract import (
    attach_authorization_comparison_contract,
)
from .database_numeric_experiment_projection import (
    project_database_numeric_assertions,
)
from .database_numeric_finding_bridge import (
    install_database_numeric_finding_bridge,
)
from .database_observer_experiment_projection import (
    project_database_observers_to_experiment_pack,
)
from .database_relation_delta_causality_finding_bridge import (
    install_database_relation_causal_delta_finding_bridge,
)
from .database_relation_delta_causality_mainline import (
    project_database_relation_delta_causality,
)
from .database_relation_delta_projection_gate import (
    project_database_relation_delta_assertions,
)
from .database_relation_delta_finding_bridge import (
    install_database_relation_delta_finding_bridge,
)
from .database_relation_experiment_bridge import (
    attach_captured_database_relation_contracts,
    install_database_relation_asset_capture,
)
from .database_relation_numeric_experiment_projection import (
    project_database_relation_numeric_assertions,
)
from .database_relation_numeric_finding_bridge import (
    install_database_relation_numeric_finding_bridge,
)
from .database_state_transition_experiment_projection import (
    project_database_state_transition_assertions,
)
from .database_state_transition_finding_bridge import (
    install_database_state_transition_finding_bridge,
)
from .compile_batch_context import get_batch_indexes
from .experiment_compiler_conflict_base import *  # noqa: F401,F403
from .experiment_compiler_sod import attach_sod_fixture_owner_binding
from .runtime_materialization_experiment_bridge import (
    bind_experiment_pack_to_captured_materializations,
    install_runtime_materialization_execution_bridge,
)
from .runtime_materialization_operation_matching import (
    install_runtime_materialization_operation_matching,
)


_original_compile_experiment = _base._original_compile_experiment

# Additive only: capture the existing knowledge asset, bind its governed materialization and
# approved database relation contracts to experiments, then extend the existing finalizer.
install_runtime_materialization_execution_bridge()
install_database_relation_asset_capture()
install_database_state_transition_finding_bridge()
install_database_numeric_finding_bridge()
install_database_relation_numeric_finding_bridge()
install_database_relation_delta_finding_bridge()
install_database_relation_causal_delta_finding_bridge()
install_runtime_materialization_operation_matching()


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _add_refs(target: set[str], value: Any) -> None:
    if isinstance(value, (str, int)) and _text(value):
        target.add(_text(value))
    elif isinstance(value, list):
        for item in value:
            _add_refs(target, item)


def _refs_from_mapping(value: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    explicit_keys = {
        "subject_ref", "subject_refs", "operation_ref", "operation_refs",
        "operation_id", "operation_ids", "actor_ref", "actor_refs",
        "invariant_ref", "invariant_refs", "rule_ref", "rule_refs",
        "entity_ref", "entity_refs", "state_ref", "state_refs",
        "from_ref", "to_ref", "node_ref", "node_refs",
        "relation_ref", "relation_refs",
    }
    for key, child in value.items():
        normalized = _text(key).lower()
        if normalized in {"source_ref", "source_refs", "secret_ref", "credential_secret_ref"}:
            continue
        if (
            normalized in explicit_keys
            or normalized.endswith("_ref")
            or normalized.endswith("_refs")
        ):
            _add_refs(refs, child)
        elif isinstance(child, dict):
            refs.update(_refs_from_mapping(child))
    return refs


def _obligation_scope_refs(obligation: dict[str, Any]) -> set[str]:
    row = _dict(obligation)
    refs = _refs_from_mapping(row)
    _add_refs(refs, row.get("subject_refs"))
    _add_refs(refs, row.get("required_operations"))
    _add_refs(refs, row.get("required_actors"))
    _add_refs(refs, row.get("relation_refs"))
    return refs


def _conflict_is_relevant(
    conflict: dict[str, Any],
    *,
    obligation_refs: set[str],
    obligation_actor_refs: set[str] | None,
    obligation_actor_roles: set[str] | None,
    obligation_operation_refs: set[str] | None,
    precomputed_refs: frozenset[str] | None = None,
) -> bool:
    if _text(conflict.get("status")) != "conflicting":
        return True
    permission_scope_present = False
    if _text(conflict.get("conflict_type")) == "permission_decision_conflict":
        conflict_actor_refs: set[str] = set()
        _add_refs(conflict_actor_refs, conflict.get("actor_ref"))
        _add_refs(conflict_actor_refs, conflict.get("actor_refs"))
        if (
            conflict_actor_refs
            and obligation_actor_refs is not None
            and conflict_actor_refs.isdisjoint(obligation_actor_refs)
        ):
            return False
        conflict_role = _text(conflict.get("role_key")).lower()
        if (
            conflict_role
            and obligation_actor_roles is not None
            and conflict_role not in obligation_actor_roles
        ):
            return False
        conflict_operation_refs: set[str] = set()
        _add_refs(conflict_operation_refs, conflict.get("operation_ref"))
        _add_refs(conflict_operation_refs, conflict.get("operation_refs"))
        permission_scope_present = bool(
            conflict_actor_refs or conflict_role or conflict_operation_refs
        )
        if (
            conflict_operation_refs
            and obligation_operation_refs is not None
            and conflict_operation_refs.isdisjoint(obligation_operation_refs)
        ):
            return False
    if permission_scope_present:
        return True
    if precomputed_refs is not None:
        conflict_refs = precomputed_refs
    else:
        conflict_refs = _refs_from_mapping(_dict(conflict))
    if not conflict_refs or not obligation_refs:
        return True
    return bool(conflict_refs.intersection(obligation_refs))


def _obligation_node_refs(
    behavior_ir: dict[str, Any],
    obligation: dict[str, Any],
    collection: str,
) -> set[str] | None:
    indexes = get_batch_indexes()
    if indexes is not None:
        node_ids = (
            indexes.actor_ids if collection == "actors" else indexes.operation_ids
        )
    else:
        node_ids = {
            _text(node.get("id"))
            for node in _list(_dict(behavior_ir).get(collection))
            if isinstance(node, dict) and _text(node.get("id"))
        }
    refs = _refs_from_mapping(_dict(obligation)).intersection(node_ids)
    return refs or None


def _obligation_actor_roles(
    behavior_ir: dict[str, Any],
    actor_refs: set[str] | None,
) -> set[str] | None:
    indexes = get_batch_indexes()
    if indexes is not None:
        actors = indexes.actor_roles_by_id
    else:
        actors = {
            _text(actor.get("id")): _text(
                actor.get("role_key") or actor.get("role")
            ).lower()
            for actor in _list(_dict(behavior_ir).get("actors"))
            if isinstance(actor, dict) and _text(actor.get("id"))
        }
    if not actor_refs:
        return None
    roles = {actors[actor_ref] for actor_ref in actor_refs if actors[actor_ref]}
    return roles or None


def _scoped_behavior_ir(
    behavior_ir: dict[str, Any],
    obligation: dict[str, Any],
) -> dict[str, Any]:
    # NOTE: a full `deepcopy` of the whole IR per obligation used to be done
    # here. With the semantic-binding mechanisms (rule→interface edges,
    # state-machine transitions, conservation equations) the IR grew large,
    # and per-obligation full deepcopy became O(obligations × IR) — measured
    # stuck in `copy.deepcopy` for >50 minutes in the compile phase. The
    # compile chain only READS behavior_ir (the only product write site,
    # obligation_compiler.py:559, runs earlier during IR building), so a
    # top-level shallow copy plus a freshly filtered conflicts list keeps
    # each obligation's view independent without the quadratic copy cost.
    ir = dict(_dict(behavior_ir))
    obligation_refs = _obligation_scope_refs(obligation)
    obligation_actor_refs = _obligation_node_refs(behavior_ir, obligation, "actors")
    obligation_actor_roles = _obligation_actor_roles(behavior_ir, obligation_actor_refs)
    obligation_operation_refs = _obligation_node_refs(
        behavior_ir, obligation, "operations"
    )
    indexes = get_batch_indexes()
    conflicts: list[Any]
    conflict_refs: tuple[frozenset[str], ...] | None
    if indexes is not None:
        conflicts = list(indexes.conflicts)
        conflict_refs = indexes.conflict_refs
    else:
        conflicts = _list(ir.get("conflicts"))
        conflict_refs = None
    ir["conflicts"] = [
        dict(conflict)
        for index, conflict in enumerate(conflicts)
        if isinstance(conflict, dict)
        and _conflict_is_relevant(
            conflict,
            obligation_refs=obligation_refs,
            obligation_actor_refs=obligation_actor_refs,
            obligation_actor_roles=obligation_actor_roles,
            obligation_operation_refs=obligation_operation_refs,
            precomputed_refs=(
                conflict_refs[index] if conflict_refs is not None else None
            ),
        )
    ]
    return ir


def _privacy_field_mode(obligation: dict[str, Any]) -> bool:
    row = _dict(obligation)
    prop = _dict(row.get("property"))
    return bool(
        _text(row.get("risk_family")) == "privacy"
        and _text(prop.get("privacy_test_mode")) == "field_policy"
        and _text(prop.get("privacy_policy")) in {"absent", "masked"}
    )


def _attach_source_observed_mutations(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Block writes whose exact request body is absent from source material."""
    receipt = _dict(experiment.get("compile_receipt"))
    if _text(receipt.get("status")).upper() != "COMPILED":
        return experiment
    operations = {
        _text(operation.get("id")): operation
        for operation in _list(_dict(behavior_ir).get("operations"))
        if isinstance(operation, dict) and _text(operation.get("id"))
    }
    siblings = list(operations.values())
    for step in _list(experiment.get("control_plan")) + _list(experiment.get("treatment_plan")):
        if not isinstance(step, dict):
            continue
        operation_ref = _text(step.get("operation_ref"))
        operation = _dict(operations.get(operation_ref))
        method = _text(operation.get("method")).upper()
        body = step.get("body") if "body" in step else None
        if body in (None, {}):
            from .experiment_compiler_support import _source_request_example

            body = _source_request_example(
                operation,
                sibling_operations=siblings,
            )
        if method not in {"PATCH", "PUT"} or body not in (None, {}):
            continue
        obligation_id = _text(experiment.get("obligation_id")) or "unknown_obligation"
        return _base._base.blocked_experiment(
            obligation_id,
            "BLOCKED_MISSING_BINDING",
            f"source_declared_request_body_missing:{operation_ref or 'unknown_operation'}",
        )
    return experiment


def compile_experiment_for_obligation(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: set[str] | None = None,
) -> dict[str, Any]:
    if not _privacy_field_mode(obligation):
        problem = _base._runtime_pair_problem(obligation, behavior_ir)
        if problem:
            obligation_id = _text(_dict(obligation).get("obligation_id")) or "unknown_obligation"
            return _base._base.blocked_experiment(
                obligation_id,
                "BLOCKED_RUNTIME_ACTOR_PAIR_NOT_DISTINCT",
                f"runtime_actor_pair_not_distinct:{problem}",
            )

    experiment = _original_compile_experiment(
        obligation,
        behavior_ir=_scoped_behavior_ir(behavior_ir, obligation),
        environment_type=environment_type,
        policy_version=policy_version,
        available_adapters=available_adapters,
    )
    experiment = attach_sod_fixture_owner_binding(
        experiment, obligation, behavior_ir
    )
    experiment = _attach_source_observed_mutations(experiment, behavior_ir)
    governed, reason, detail = attach_authorization_comparison_contract(
        experiment,
        obligation,
        behavior_ir,
    )
    if reason:
        obligation_id = _text(_dict(obligation).get("obligation_id")) or "unknown_obligation"
        return _base._base.blocked_experiment(
            obligation_id,
            reason,
            detail,
        )
    return governed


def compile_experiments(
    obligations: list[dict[str, Any]],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: "set[str] | frozenset[str] | None" = None,
    planning_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile and project one governed database observation/oracle chain."""
    from .rescue_dedupe import compile_rescue_cache_stats

    _compile_rescue_before = compile_rescue_cache_stats()
    # Concurrent compile base — NOW WIRED: the wrapper installs the same
    # per-batch Behavior-IR index bundle the serial path establishes (via
    # _submit_with_batch_indexes context snapshots), which was the root cause
    # of the earlier semantics drift (rescued 42→0) that kept this path
    # unwired and compile single-threaded (~1-2s/obligation dominating the
    # planning budget). Kill-switch: QUALIBUG_COMPILE_CONCURRENCY=1 forces
    # the exact serial path; the concurrent entry itself also delegates to
    # serial for <=1 obligation / concurrency<=1.
    import os as _os

    _cc = _os.environ.get("QUALIBUG_COMPILE_CONCURRENCY", "").strip()
    if _cc.isdigit() and int(_cc) > 1 and len(obligations) >= 50:
        from .experiment_compile_concurrent import compile_experiments_concurrent

        pack = compile_experiments_concurrent(
            obligations,
            behavior_ir=behavior_ir,
            environment_type=environment_type,
            policy_version=policy_version,
            compile_one=compile_experiment_for_obligation,
            available_adapters=available_adapters,
        )
    else:
        # Serial compile base (batch-context aware).
        pack = _base._base.compile_experiments(
            obligations,
            behavior_ir=behavior_ir,
            environment_type=environment_type,
            policy_version=policy_version,
            compile_one=compile_experiment_for_obligation,
            available_adapters=available_adapters,
        )
    # Abstract → Runtime Materialization → concrete recompile (serial,
    # content-addressed rescue dedupe inside the materialization loop).
    from .experiment_runtime_materialization import (
        materialize_and_recompile_abstract_pack,
    )

    pack = materialize_and_recompile_abstract_pack(
        pack,
        obligations=obligations,
        behavior_ir=behavior_ir,
        compile_one=compile_experiment_for_obligation,
        environment_type=environment_type,
        policy_version=policy_version,
        available_adapters=available_adapters,
        planning_context=planning_context,
    )
    bridged = bind_experiment_pack_to_captured_materializations(
        pack,
        behavior_ir=behavior_ir,
        obligations=obligations,
    )
    observer_bound = project_database_observers_to_experiment_pack(bridged)
    relation_contract_bound = attach_captured_database_relation_contracts(
        observer_bound,
        behavior_ir=behavior_ir,
    )
    state_bound = project_database_state_transition_assertions(relation_contract_bound)
    # Delta relations require exact BEFORE/AFTER root pairs and must bind before
    # causality, final-value relation rules or the legacy same-row numeric projector.
    relation_delta_bound = project_database_relation_delta_assertions(state_bound)
    causal_delta_bound = project_database_relation_delta_causality(
        relation_delta_bound
    )
    relation_numeric_bound = project_database_relation_numeric_assertions(
        causal_delta_bound
    )
    pack = project_database_numeric_assertions(relation_numeric_bound)
    # Compile-time rescue dedupe stats for this compile invocation (delta over
    # the process-scoped counters; additive receipt, no behavior change).
    _compile_rescue_after = compile_rescue_cache_stats()
    pack["compile_rescue_stats"] = {
        key: int(_compile_rescue_after.get(key) or 0)
        - int(_compile_rescue_before.get(key) or 0)
        for key in _compile_rescue_after
    }
    return pack

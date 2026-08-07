"""Scenario-level fixture DAG for executable experiments.

Builds a dependency-ordered setup graph from experiment requirements, IR
relations, and optional SQL foreign keys. Cleanup runs in reverse order.
Unconstructible fixtures block the experiment — fake IDs are forbidden.
"""
from __future__ import annotations

import hashlib
from typing import Any


SCHEMA_VERSION = "qualibug.fixture-dag.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _node_id(*parts: Any) -> str:
    raw = "|".join(_text(p) for p in parts if _text(p))
    return f"fix_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def build_fixture_dag_for_experiment(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any] | None = None,
    fk_setup_requests: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a scenario fixture DAG or an explicit block receipt."""
    exp = _dict(experiment)
    ir = _dict(behavior_ir)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    blocked: list[dict[str, Any]] = []

    # Actor credential nodes (secret_ref only)
    for step in list(exp.get("control_plan") or []) + list(exp.get("treatment_plan") or []):
        if not isinstance(step, dict):
            continue
        actor_ref = _text(step.get("actor_ref"))
        if not actor_ref:
            continue
        nid = _node_id("actor", actor_ref)
        if any(n.get("node_id") == nid for n in nodes):
            continue
        actor = next(
            (a for a in _list(ir.get("actors")) if isinstance(a, dict) and _text(a.get("id")) == actor_ref),
            {},
        )
        secret_ref = _text(_dict(actor).get("credential_secret_ref"))
        if not secret_ref and _text(_dict(actor).get("role")).lower() not in {"anonymous", "public", ""}:
            blocked.append({
                "reason_code": "BLOCKED_MISSING_ACTOR",
                "detail": f"actor {actor_ref} missing credential_secret_ref",
            })
            continue
        nodes.append({
            "node_id": nid,
            "kind": "actor_context",
            "actor_ref": actor_ref,
            "secret_ref": secret_ref or "anonymous",
            "requires_read_proof": False,
        })

    # Binding / fixture requirements from binding_plan
    for item in _list(exp.get("binding_plan")):
        if not isinstance(item, dict):
            continue
        target = _text(item.get("target") or item.get("fixture_id"))
        if not target:
            continue
        if target.startswith("actor:"):
            continue
        status = _text(item.get("status"))
        nid = _node_id("bind", target)
        if status == "runtime_resolvable":
            resolver_operations = [
                dict(resolver)
                for resolver in _list(item.get("resolver_operations"))
                if isinstance(resolver, dict)
            ]
            constructible = bool(resolver_operations) and all(
                _text(resolver.get("operation_ref"))
                and _text(resolver.get("method")).upper() in {"GET", "HEAD"}
                and _text(resolver.get("path")).startswith("/")
                and "{" not in _text(resolver.get("path"))
                and ":" not in _text(resolver.get("path"))
                for resolver in resolver_operations
            )
            # synthetic_value alone is never constructible — invented ids forbidden
            if not constructible and item.get("fixture_setup"):
                constructible = True
            if (
                _text(item.get("source_priority"))
                == "ownership_identity_param"
            ):
                # Body ownership identity fields (fromUserId/toUserId/ownerId…)
                # resolve from the arm actor's own runtime-observed identity
                # (its login token) — never from a list read of another
                # entity's collection. No resolver read or fixture creation
                # is needed, so the node is constructible without read proof.
                nodes.append({
                    "node_id": nid,
                    "kind": "runtime_read_binding",
                    "target": target,
                    "source_priority": "ownership_identity_param",
                    "resolver_operations": [],
                    "requires_read_proof": False,
                    "constructible": True,
                })
                continue
            nodes.append({
                "node_id": nid,
                "kind": "runtime_read_binding",
                "target": target,
                "source_priority": _text(item.get("source_priority") or "same_actor_list_read"),
                "resolver_operations": resolver_operations,
                "requires_read_proof": True,
                "constructible": constructible,
            })
            if not constructible:
                blocked.append({
                    "reason_code": "BLOCKED_MISSING_BINDING",
                    "detail": f"runtime resolver invalid for {target}",
                })
        elif (
            status == "bound"
            and _text(item.get("source_priority"))
            in (
                "source_declared_path_example",
                "owner_identity_runtime_observed",
            )
            and item.get("materialized_value") not in (None, "")
        ):
            # Source-grounded pre-materialized binding: the operation's own
            # documented parameter example, or the arm owner's login-observed
            # identity — no resolver read or fixture creation is needed, so
            # the node is constructible with no read proof.
            nodes.append({
                "node_id": nid,
                "kind": "runtime_read_binding",
                "target": target,
                "source_priority": _text(item.get("source_priority")),
                "resolver_operations": [],
                "materialized_value": str(item.get("materialized_value")),
                "requires_read_proof": False,
                "constructible": True,
            })
        elif status == "fixture_proof":
            create_path = _text(item.get("create_path"))
            owner_actor_ref = _text(item.get("owner_actor_ref"))
            binding_target = _text(item.get("binding_target"))
            source_binding = next((
                binding
                for binding in _list(exp.get("binding_plan"))
                if isinstance(binding, dict)
                and _text(binding.get("target")) == binding_target
                and binding.get("force_fixture_setup") is True
            ), None)
            constructible = bool(
                create_path.startswith("/")
                and not "{" in create_path
                and owner_actor_ref
                and binding_target
                and source_binding
                and _list(item.get("cleanup_operations"))
                and _text(item.get("proof_operation_ref"))
            )
            nodes.append({
                "node_id": nid,
                "kind": "ownership_fixture_proof",
                "fixture_id": _text(item.get("fixture_id") or target),
                "binding_target": binding_target,
                "owner_actor_ref": owner_actor_ref,
                "create_operation_ref": _text(item.get("create_operation_ref")),
                "create_path": create_path,
                "proof_operation_ref": _text(item.get("proof_operation_ref")),
                "cleanup_operations": [
                    dict(row)
                    for row in _list(item.get("cleanup_operations"))
                    if isinstance(row, dict)
                ],
                "requires_read_proof": True,
                "constructible": constructible,
            })
            if not constructible:
                blocked.append({
                    "reason_code": "BLOCKED_MISSING_FIXTURE",
                    "detail": f"ownership fixture proof incomplete for {target}",
                })
        elif status == "unresolved" or status == "required":
            create_path = _text(item.get("create_path") or item.get("create_operation_ref"))
            # Disposable fixtures are only constructible when a concrete create path
            # is already resolved. Never mark COMPILED/READY on unresolved fixtures.
            constructible = bool(create_path and create_path.startswith("/") and "{" not in create_path)
            nodes.append({
                "node_id": nid,
                "kind": "disposable_fixture",
                "fixture_id": _text(item.get("fixture_id") or target),
                "source_priority": _text(item.get("source_priority") or "disposable_fixture_receipt"),
                "requires_read_proof": True,
                "constructible": constructible,
                "create_path": create_path,
            })
            if not constructible:
                blocked.append({
                    "reason_code": "BLOCKED_MISSING_FIXTURE",
                    "detail": f"unresolved binding {target} without concrete create path",
                })
            elif status == "unresolved" and not item.get("fixture_id"):
                blocked.append({
                    "reason_code": "BLOCKED_MISSING_FIXTURE",
                    "detail": f"unresolved binding {target} without fixture plan",
                })
        elif status == "bound":
            nodes.append({
                "node_id": nid,
                "kind": "bound_value",
                "target": target,
                "source_priority": _text(item.get("source_priority")),
                "value_fingerprint": _text(item.get("value_fingerprint")),
                "requires_read_proof": False,
                "constructible": True,
            })

    # FK dependency setup requests (from auto_test_data_factory planner)
    prev_dep: str | None = None
    for index, req in enumerate(_list(fk_setup_requests)):
        if not isinstance(req, dict):
            continue
        nid = _node_id("fk", index, req.get("path"), req.get("purpose"))
        nodes.append({
            "node_id": nid,
            "kind": "dependency_create",
            "method": _text(req.get("method") or "POST").upper(),
            "path": _text(req.get("path")),
            "body": _dict(req.get("body")),
            "bind_response_id_to": _list(req.get("bind_response_id_to")),
            "requires_read_proof": True,
            "constructible": bool(_text(req.get("path"))),
        })
        if not _text(req.get("path")):
            blocked.append({
                "reason_code": "BLOCKED_MISSING_FIXTURE",
                "detail": f"dependency setup missing path: {req.get('purpose')}",
            })
        if prev_dep:
            edges.append({"from": prev_dep, "to": nid})
        prev_dep = nid

    # Setup plan steps
    for index, step in enumerate(_list(exp.get("setup_plan"))):
        if not isinstance(step, dict):
            continue
        nid = _node_id("setup", index, step.get("action"))
        nodes.append({
            "node_id": nid,
            "kind": "setup_step",
            "action": _text(step.get("action")),
            "requires_read_proof": True,
            "constructible": True,
        })

    # Topological order: actors → deps → fixtures → setup
    kind_rank = {
        "actor_context": 0,
        "runtime_read_binding": 1,
        "dependency_create": 1,
        "disposable_fixture": 2,
        "ownership_fixture_proof": 2,
        "bound_value": 2,
        "setup_step": 3,
    }
    ordered = sorted(nodes, key=lambda n: (kind_rank.get(str(n.get("kind")), 9), str(n.get("node_id"))))
    cleanup_order = list(reversed([
        n for n in ordered
        if n.get("kind") in {"dependency_create", "disposable_fixture", "setup_step"}
    ]))

    if blocked:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "BLOCKED",
            "experiment_id": _text(exp.get("experiment_id")),
            "nodes": ordered,
            "edges": edges,
            "setup_order": [n["node_id"] for n in ordered],
            "cleanup_order": [n["node_id"] for n in cleanup_order],
            "blocked_reasons": blocked,
            "read_proof_required_count": sum(1 for n in ordered if n.get("requires_read_proof")),
        }

    unconstructible = [n for n in ordered if n.get("constructible") is False]
    if unconstructible:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "BLOCKED",
            "experiment_id": _text(exp.get("experiment_id")),
            "nodes": ordered,
            "edges": edges,
            "setup_order": [n["node_id"] for n in ordered],
            "cleanup_order": [n["node_id"] for n in cleanup_order],
            "blocked_reasons": [{
                "reason_code": "BLOCKED_MISSING_FIXTURE",
                "detail": f"unconstructible node {item.get('node_id')}",
            } for item in unconstructible],
            "read_proof_required_count": sum(1 for n in ordered if n.get("requires_read_proof")),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "READY",
        "experiment_id": _text(exp.get("experiment_id")),
        "nodes": ordered,
        "edges": edges,
        "setup_order": [n["node_id"] for n in ordered],
        "cleanup_order": [n["node_id"] for n in cleanup_order],
        "blocked_reasons": [],
        "read_proof_required_count": sum(1 for n in ordered if n.get("requires_read_proof")),
        "rules": {
            "low_priority_must_not_override_high": True,
            "cleanup_reverse_order": True,
            "setup_rejected_is_not_cleanup_failure": True,
            "no_fake_ids": True,
        },
    }


def attach_fixture_dag_to_experiments(
    experiment_compile: dict[str, Any],
    *,
    behavior_ir: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Annotate compiled experiments with fixture DAGs; move blocked ones aside."""
    pack = dict(experiment_compile or {})
    experiments = []
    blocked = list(pack.get("blocked_experiments") or [])
    for exp in _list(pack.get("experiments")):
        if not isinstance(exp, dict):
            continue
        dag = build_fixture_dag_for_experiment(exp, behavior_ir=behavior_ir)
        row = dict(exp)
        row["fixture_dag"] = dag
        if dag.get("status") == "BLOCKED":
            receipt = dict(_dict(row.get("compile_receipt")))
            receipt["status"] = "BLOCKED"
            receipt["reason_code"] = _text((_list(dag.get("blocked_reasons")) or [{}])[0].get("reason_code")) or "BLOCKED_MISSING_FIXTURE"
            receipt["detail"] = _text((_list(dag.get("blocked_reasons")) or [{}])[0].get("detail"))
            row["compile_receipt"] = receipt
            blocked.append(row)
        else:
            experiments.append(row)
    pack["experiments"] = experiments
    pack["blocked_experiments"] = blocked
    pack["compiled_count"] = len(experiments)
    pack["blocked_count"] = len(blocked)
    return pack

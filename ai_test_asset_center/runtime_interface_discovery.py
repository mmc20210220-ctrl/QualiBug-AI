"""Source-derived, read-only discovery of undocumented runtime interfaces.

The planner derives bounded GET candidates from source-declared route
vocabulary.  The executor acquires observations only through correlated,
governed requests so every probe remains present in the obligation ledger and
evaluator-owned gateway.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .operational_receipts import build_execution_operational_receipt
from .sandbox_write_executor import _http_request
from .sandbox_write_executor_base import evaluator_request_trace


PLAN_SCHEMA = "qualibug.runtime-interface-discovery-plan.v1"
OBSERVATION_SCHEMA = "qualibug.runtime-interface-observation.v1"
_PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}|:[A-Za-z_][A-Za-z0-9_]*")
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_ref(operation: dict[str, Any]) -> dict[str, str]:
    method = _text(operation.get("method")).upper()
    path = _text(operation.get("path"))
    return {
        "source_id": _text(operation.get("source_id")) or "api_spec",
        "locator": f"{method} {path}",
        "kind": "source_route_vocabulary",
    }


def _segments(path: str) -> list[str]:
    clean = path.split("?", 1)[0].strip()
    return [segment for segment in clean.split("/") if segment]


def load_runtime_interface_discovery_actions() -> list[str]:
    """Load the deployment-owned action vocabulary; missing policy fails fast."""

    path = Path(__file__).resolve().parent / "policies" / "semantic_lexicon.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"runtime_interface_semantic_policy_unreadable:{type(exc).__name__}"
        ) from exc
    raw = payload.get("runtime_interface_discovery_actions")
    if not isinstance(raw, list):
        raise ValueError("runtime_interface_discovery_actions_missing")
    actions: list[str] = []
    for value in raw:
        action = _text(value).strip("/").lower()
        if not action or not _SAFE_SEGMENT_RE.fullmatch(action):
            raise ValueError("runtime_interface_action_marker_invalid")
        if action not in actions:
            actions.append(action)
    if not actions:
        raise ValueError("runtime_interface_discovery_actions_empty")
    return actions


def load_runtime_interface_discovery_budget() -> int:
    """Load the deployment-owned probe budget with strict type/range checks."""

    path = Path(__file__).resolve().parent / "policies" / "semantic_lexicon.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"runtime_interface_semantic_policy_unreadable:{type(exc).__name__}"
        ) from exc
    value = payload.get("runtime_interface_discovery_max_candidates")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
        raise ValueError("runtime_interface_discovery_budget_invalid")
    return value


def load_runtime_interface_confirmation_tokens(
    root: Path,
    project: str,
) -> list[str]:
    """Load unique active bearer tokens from the declared test-actor catalog.

    Tokens are returned only for transport use and must never be copied into a
    receipt.  A malformed catalog fails fast because silently treating broken
    credentials as an empty actor set would make interface absence ambiguous.
    """

    path = Path(root) / "platform_inputs" / str(project) / "test_accounts.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"runtime_interface_actor_catalog_invalid:{type(exc).__name__}"
        ) from exc
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        declared = payload.get("accounts") or payload.get("actors") or payload.get("users")
        if declared is None:
            rows = [
                value
                for key, value in payload.items()
                if key not in {"schema", "schema_version", "meta"}
            ]
        elif isinstance(declared, list):
            rows = declared
        else:
            raise ValueError("runtime_interface_actor_catalog_rows_invalid")
    else:
        raise ValueError("runtime_interface_actor_catalog_root_invalid")

    tokens: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("runtime_interface_actor_catalog_row_invalid")
        status = _text(
            row.get("status")
            or row.get("account_status")
            or row.get("authenticated_status")
            or row.get("state")
            or "active"
        ).upper()
        if status in {"DISABLED", "LOCKED", "SUSPENDED", "INACTIVE"}:
            continue
        token = _text(row.get("token") or row.get("access_token") or row.get("jwt"))
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _common_prefix(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    prefix: list[str] = []
    for values in zip(*rows):
        if len(set(values)) != 1 or _PLACEHOLDER_RE.search(values[0]):
            break
        prefix.append(values[0])
    # Preserve only a transport namespace.  A full resource path is not a
    # namespace from which sibling interfaces may be derived.
    if not prefix or any(len(row) <= 1 for row in rows):
        return []
    return prefix[:1]


def _candidate(
    path: str,
    *,
    derivation: str,
    source_refs: Iterable[dict[str, str]],
) -> dict[str, Any]:
    refs = sorted(
        {json.dumps(ref, sort_keys=True): ref for ref in source_refs}.values(),
        key=lambda row: (row["source_id"], row["locator"]),
    )
    candidate_id = "surface_" + _fingerprint(
        {"method": "GET", "path": path, "derivation": derivation, "source_refs": refs}
    )[:20]
    return {
        "candidate_id": candidate_id,
        "method": "GET",
        "path": path,
        "derivation": derivation,
        "source_refs": refs,
    }


def plan_runtime_interface_candidates(
    documented_operations: list[dict[str, Any]],
    *,
    action_markers: list[str] | None,
    max_candidates: int,
) -> dict[str, Any]:
    """Build deterministic, bounded GET candidates from source route tokens."""

    if isinstance(max_candidates, bool) or int(max_candidates) <= 0:
        raise ValueError("runtime_interface_candidate_budget_invalid")
    configured_actions = (
        load_runtime_interface_discovery_actions()
        if action_markers is None
        else action_markers
    )
    actions = []
    for value in configured_actions:
        action = _text(value).strip("/").lower()
        if not action or not _SAFE_SEGMENT_RE.fullmatch(action):
            raise ValueError("runtime_interface_action_marker_invalid")
        if action not in actions:
            actions.append(action)
    if not actions:
        raise ValueError("runtime_interface_action_markers_missing")

    operations = [
        dict(row)
        for row in documented_operations
        if isinstance(row, dict)
        and _text(row.get("path")).startswith("/")
        and _text(row.get("method"))
    ]
    if not operations:
        raise ValueError("runtime_interface_documented_operations_missing")
    segmented = [_segments(_text(row["path"])) for row in operations]
    prefix = _common_prefix(segmented)
    prefix_len = len(prefix)
    prefix_path = "/" + "/".join(prefix) if prefix else ""
    documented_paths = {_text(row["path"]).split("?", 1)[0] for row in operations}

    refs_by_token: dict[str, list[dict[str, str]]] = defaultdict(list)
    child_tokens: dict[str, set[str]] = defaultdict(set)
    admin_shape_observed = False
    for operation, parts in zip(operations, segmented):
        tail = parts[prefix_len:]
        for index, token in enumerate(tail):
            normalized = token.lower()
            if index > 0:
                if normalized == "admin":
                    admin_shape_observed = True
                continue
            if (
                normalized == "admin"
                or normalized in actions
                or _PLACEHOLDER_RE.search(token)
                or not _SAFE_SEGMENT_RE.fullmatch(token)
            ):
                if normalized == "admin":
                    admin_shape_observed = True
                continue
            refs_by_token[normalized].append(_source_ref(operation))
            if index + 1 < len(tail):
                child = tail[index + 1].lower()
                if (
                    child != "admin"
                    and not _PLACEHOLDER_RE.search(child)
                    and _SAFE_SEGMENT_RE.fullmatch(child)
                ):
                    child_tokens[normalized].add(child)

    resources = sorted(refs_by_token)
    namespaces = sorted(
        token for token, children in child_tokens.items() if len(children) >= 2
    )
    planned: list[dict[str, Any]] = []
    seen: set[str] = set(documented_paths)

    def add(path: str, derivation: str, tokens: list[str]) -> None:
        if path in seen:
            return
        seen.add(path)
        refs = [ref for token in tokens for ref in refs_by_token.get(token, [])]
        if not refs:
            raise ValueError("runtime_interface_candidate_source_refs_missing")
        planned.append(_candidate(path, derivation=derivation, source_refs=refs))

    for action in actions:
        for resource in resources:
            add(
                f"{prefix_path}/{resource}/{action}",
                "resource_action_lattice",
                [resource],
            )
        for namespace in namespaces:
            for resource in resources:
                if resource == namespace:
                    continue
                add(
                    f"{prefix_path}/{namespace}/{resource}/{action}",
                    "observed_namespace_resource_action_lattice",
                    [namespace, resource],
                )
        if admin_shape_observed:
            for resource in resources:
                add(
                    f"{prefix_path}/{resource}/admin/{action}",
                    "observed_admin_shape_action_lattice",
                    [resource],
                )

    selected = planned[: int(max_candidates)]
    receipt = {
        "schema_version": PLAN_SCHEMA,
        "documented_operation_count": len(operations),
        "source_resource_count": len(resources),
        "source_namespace_count": len(namespaces),
        "policy_action_count": len(actions),
        "candidate_budget": int(max_candidates),
        "candidate_count": len(selected),
        "unbounded_candidate_count": len(planned),
        "truncated": len(planned) > len(selected),
        "candidates": selected,
    }
    receipt["plan_fingerprint"] = _fingerprint(receipt)
    return receipt


def build_runtime_interface_observation_receipt(
    candidate: dict[str, Any],
    observation: dict[str, Any],
) -> dict[str, Any]:
    """Turn one governed GET result into a provenance-bound operation fact."""

    row = dict(candidate) if isinstance(candidate, dict) else {}
    if _text(row.get("method")).upper() != "GET":
        raise ValueError("runtime_interface_candidate_not_read_only")
    candidate_id = _text(row.get("candidate_id"))
    path = _text(row.get("path"))
    if not candidate_id or not path.startswith("/") or _PLACEHOLDER_RE.search(path):
        raise ValueError("runtime_interface_candidate_invalid")
    source_refs = [
        dict(ref) for ref in row.get("source_refs", []) if isinstance(ref, dict)
    ]
    if not source_refs:
        raise ValueError("runtime_interface_candidate_source_refs_missing")

    observed = dict(observation) if isinstance(observation, dict) else {}
    request_receipt_id = _text(observed.get("request_receipt_id"))
    if not request_receipt_id:
        raise ValueError("runtime_interface_request_receipt_missing")
    response_fingerprint = _text(observed.get("response_fingerprint")).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", response_fingerprint):
        raise ValueError("runtime_interface_response_fingerprint_invalid")
    try:
        status_code = int(observed.get("status_code"))
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime_interface_status_code_invalid") from exc
    if status_code < 0 or status_code > 599:
        raise ValueError("runtime_interface_status_code_invalid")

    raw_confirmations = observed.get("confirmation_observations")
    confirmations = (
        [dict(value) for value in raw_confirmations if isinstance(value, dict)]
        if isinstance(raw_confirmations, list)
        else []
    )
    if observed.get("confirmation_status_code") is not None:
        confirmations.append({
            "status_code": observed.get("confirmation_status_code"),
            "request_receipt_id": observed.get(
                "confirmation_request_receipt_id"
            ),
            "response_fingerprint": observed.get(
                "confirmation_response_fingerprint"
            ),
        })
    normalized_confirmations: list[dict[str, Any]] = []
    for confirmation in confirmations:
        confirmation_receipt_id = _text(
            confirmation.get("request_receipt_id")
        )
        confirmation_fingerprint = _text(
            confirmation.get("response_fingerprint")
        ).lower()
        if not confirmation_receipt_id:
            raise ValueError(
                "runtime_interface_confirmation_request_receipt_missing"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", confirmation_fingerprint):
            raise ValueError(
                "runtime_interface_confirmation_response_fingerprint_invalid"
            )
        try:
            confirmation_status = int(confirmation.get("status_code"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "runtime_interface_confirmation_status_code_invalid"
            ) from exc
        if confirmation_status < 0 or confirmation_status > 599:
            raise ValueError(
                "runtime_interface_confirmation_status_code_invalid"
            )
        normalized_confirmations.append({
            "status_code": confirmation_status,
            "request_receipt_id": confirmation_receipt_id,
            "response_fingerprint": confirmation_fingerprint,
        })

    if status_code == 404:
        status = "NOT_FOUND"
    elif status_code == 0 or status_code >= 500:
        status = "INDETERMINATE"
    elif status_code in {401, 403}:
        confirmation_statuses = {
            row["status_code"] for row in normalized_confirmations
        }
        if any(
            100 <= value < 500 and value not in {401, 403, 404}
            for value in confirmation_statuses
        ):
            status = "DISCOVERED"
        elif confirmation_statuses and confirmation_statuses == {404}:
            status = "NOT_FOUND"
        else:
            status = "INDETERMINATE"
    else:
        status = "DISCOVERED"
    receipt: dict[str, Any] = {
        "schema_version": OBSERVATION_SCHEMA,
        "candidate_id": candidate_id,
        "method": "GET",
        "path": path,
        "status": status,
        "status_code": status_code,
        "request_receipt_id": request_receipt_id,
        "response_fingerprint": response_fingerprint,
        "source_refs": source_refs,
    }
    if normalized_confirmations:
        receipt["confirmation_observations"] = normalized_confirmations
    if status == "DISCOVERED":
        receipt["operation"] = {
            "method": "GET",
            "path": path,
            "operation_id": f"runtime-observed:get:{path}",
            "source_id": request_receipt_id,
            "summary": "Runtime-observed interface",
            "description": "Interface existence proven by a governed read-only request.",
            "parameters": [],
            "request_schema": {},
            "response_schema": {},
            "derivation": "runtime-observed",
            "runtime_observation_receipt_id": request_receipt_id,
            "source_refs": source_refs,
        }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    return receipt


def merge_runtime_discovered_operations(
    documented_operations: list[dict[str, Any]],
    observation_receipts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge only fingerprint-valid DISCOVERED observations by method/path."""

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for operation in documented_operations:
        if not isinstance(operation, dict):
            continue
        key = (_text(operation.get("method")).upper(), _text(operation.get("path")))
        if key[0] and key[1]:
            merged[key] = dict(operation)
    for raw in observation_receipts:
        receipt = dict(raw) if isinstance(raw, dict) else {}
        fingerprint = _text(receipt.pop("receipt_fingerprint"))
        if not fingerprint or fingerprint != _fingerprint(receipt):
            raise ValueError("runtime_interface_observation_fingerprint_invalid")
        if receipt.get("schema_version") != OBSERVATION_SCHEMA:
            raise ValueError("runtime_interface_observation_schema_invalid")
        if _text(receipt.get("status")) != "DISCOVERED":
            continue
        operation = receipt.get("operation")
        if not isinstance(operation, dict):
            raise ValueError("runtime_interface_discovered_operation_missing")
        key = (_text(operation.get("method")).upper(), _text(operation.get("path")))
        if key[0] != "GET" or not key[1]:
            raise ValueError("runtime_interface_discovered_operation_invalid")
        merged.setdefault(key, dict(operation))
    return list(merged.values())


def execute_runtime_interface_discovery(
    plan: dict[str, Any],
    *,
    base_url: str,
    mainline_run: dict[str, Any],
    confirmation_tokens: list[str] | None = None,
) -> dict[str, Any]:
    """Execute planned GET candidates with correlation and ledger-ready receipts."""

    discovery_plan = dict(plan) if isinstance(plan, dict) else {}
    if discovery_plan.get("schema_version") != PLAN_SCHEMA:
        raise ValueError("runtime_interface_discovery_plan_schema_invalid")
    claimed = _text(discovery_plan.get("plan_fingerprint"))
    unsigned_plan = {
        key: value
        for key, value in discovery_plan.items()
        if key != "plan_fingerprint"
    }
    if not claimed or claimed != _fingerprint(unsigned_plan):
        raise ValueError("runtime_interface_discovery_plan_fingerprint_invalid")
    target = _text(base_url).rstrip("/")
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("runtime_interface_base_url_invalid")
    authority = dict(mainline_run) if isinstance(mainline_run, dict) else {}
    identities = {
        key: _text(authority.get(key))
        for key in ("run_id", "campaign_id", "target_id")
    }
    if not all(identities.values()):
        raise ValueError("runtime_interface_mainline_identity_missing")

    selected_rows: list[dict[str, Any]] = []
    compile_results: dict[str, dict[str, Any]] = {}
    execution_results: dict[str, dict[str, Any]] = {}
    gate_results: dict[str, dict[str, Any]] = {}
    observation_receipts: list[dict[str, Any]] = []
    discovered_operations: list[dict[str, Any]] = []
    harness_failure_count = 0
    declared_confirmation_tokens = list(dict.fromkeys(
        _text(value)
        for value in (confirmation_tokens or [])
        if _text(value)
    ))

    candidates = discovery_plan.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("runtime_interface_candidates_not_list")
    for raw_candidate in candidates:
        candidate = dict(raw_candidate) if isinstance(raw_candidate, dict) else {}
        if _text(candidate.get("method")).upper() != "GET":
            raise ValueError("runtime_interface_candidate_not_read_only")
        candidate_id = _text(candidate.get("candidate_id"))
        path = _text(candidate.get("path"))
        if not candidate_id or not path.startswith("/"):
            raise ValueError("runtime_interface_candidate_invalid")
        obligation_id = "surfobl_" + _fingerprint(candidate_id)[:20]
        experiment_id = "surfexp_" + _fingerprint(obligation_id)[:20]
        execution_id = "surfexec_" + _fingerprint(
            {"run_id": identities["run_id"], "obligation_id": obligation_id}
        )[:20]
        selected_rows.append({
            "obligation_id": obligation_id,
            "candidate_id": candidate_id,
            "risk_family": "interface_discovery",
            "source_refs": list(candidate.get("source_refs") or []),
            "required_operations": [],
            "required_actors": [],
            "relation_refs": [],
            "operation_refs": [],
            "actor_refs": [],
            "behavior_ir_refs": [],
            "adapter": "http_api_discovery",
            "planning_round": 0,
            "experiment_id": experiment_id,
            "property": {
                "kind": "runtime_interface_presence",
                "method": "GET",
                "path": path,
            },
        })
        compile_results[obligation_id] = {
            "status": "COMPILED",
            "compile_receipt_id": "surfcompile_" + _fingerprint(experiment_id)[:20],
            "experiment_id": experiment_id,
            "candidate_id": candidate_id,
            "cost_coverage_status": "UNKNOWN",
        }
        trace = {
            **identities,
            "obligation_id": obligation_id,
            "execution_id": execution_id,
        }
        confirmation_responses: list[dict[str, Any]] = []
        with evaluator_request_trace(trace):
            response = _http_request("GET", target + path)
            if int(response.get("status") or 0) in {401, 403}:
                for confirmation_token in declared_confirmation_tokens:
                    confirmation_response = _http_request(
                        "GET",
                        target + path,
                        token=confirmation_token,
                    )
                    confirmation_responses.append(confirmation_response)
                    confirmation_status = int(
                        confirmation_response.get("status") or 0
                    )
                    if (
                        100 <= confirmation_status < 500
                        and confirmation_status not in {401, 403, 404}
                    ):
                        break
        status_code = int(response.get("status") or 0)
        request_receipt_id = "surfreq_" + _fingerprint({
            "run_id": identities["run_id"],
            "obligation_id": obligation_id,
            "execution_id": execution_id,
            "method": "GET",
            "path": path,
            "status_code": status_code,
        })[:20]
        response_fingerprint = _fingerprint({
            "status_code": status_code,
            "body": response.get("body"),
            "headers": response.get("headers"),
        })
        confirmation_observations: list[dict[str, Any]] = []
        for index, confirmation_response in enumerate(
            confirmation_responses,
            start=1,
        ):
            confirmation_status = int(
                confirmation_response.get("status") or 0
            )
            confirmation_observations.append({
                "status_code": confirmation_status,
                "request_receipt_id": "surfreq_" + _fingerprint({
                    "request_receipt_id": request_receipt_id,
                    "confirmation_index": index,
                    "status_code": confirmation_status,
                })[:20],
                "response_fingerprint": _fingerprint({
                    "status_code": confirmation_status,
                    "body": confirmation_response.get("body"),
                    "headers": confirmation_response.get("headers"),
                }),
            })
        observation_receipt = build_runtime_interface_observation_receipt(
            candidate,
            {
                "status_code": status_code,
                "request_receipt_id": request_receipt_id,
                "response_fingerprint": response_fingerprint,
                "confirmation_observations": confirmation_observations,
            },
        )
        observation_receipts.append(observation_receipt)
        if isinstance(observation_receipt.get("operation"), dict):
            discovered_operations.append(dict(observation_receipt["operation"]))
        steps = [{
            "phase": "surface_discovery",
            "method": "GET",
            "path": path,
            "status_code": status_code,
        }]
        steps.extend({
            "phase": "surface_discovery_confirmation",
            "method": "GET",
            "path": path,
            "status_code": int(confirmation.get("status") or 0),
        } for confirmation in confirmation_responses)
        operational = build_execution_operational_receipt(
            receipt_id="surfop_" + _fingerprint(request_receipt_id)[:20],
            execution_status=("EXECUTED" if status_code else "HARNESS_FAILED"),
            steps=steps,
            cleanup_failures=0,
        )
        if not status_code:
            harness_failure_count += 1
            execution_results[obligation_id] = {
                "status": "HARNESS_FAILED",
                "reason_code": "SURFACE_DISCOVERY_TRANSPORT_FAILED",
                "reason_detail": _text(response.get("error")),
                "execution_receipt_id": "surfexecution_" + _fingerprint(execution_id)[:20],
                "execution_id": execution_id,
                "experiment_id": experiment_id,
                "candidate_id": candidate_id,
                "cost_coverage_status": "UNKNOWN",
                "operational_receipt": operational,
                "runtime_interface_observation": observation_receipt,
            }
            continue
        observation_receipt_id = "surfobs_" + _fingerprint(observation_receipt)[:20]
        execution_results[obligation_id] = {
            "status": "EXECUTED",
            "execution_receipt_id": "surfexecution_" + _fingerprint(execution_id)[:20],
            "execution_id": execution_id,
            "experiment_id": experiment_id,
            "candidate_id": candidate_id,
            "cost_coverage_status": "UNKNOWN",
            "observation_receipt_ids": [observation_receipt_id],
            "operational_receipt": operational,
            "runtime_interface_observation": observation_receipt,
        }
        gate_results[obligation_id] = {
            "status": "REJECTED",
            "reason_code": "SURFACE_DISCOVERY_OBSERVATION_ONLY",
            "reason_detail": _text(observation_receipt.get("status")),
            "gate_receipt_id": "surfgate_" + _fingerprint(observation_receipt_id)[:20],
            "cost_coverage_status": "UNKNOWN",
        }

    return {
        "schema_version": "qualibug.runtime-interface-discovery-execution.v1",
        "selected_count": len(selected_rows),
        "executed_count": len(execution_results) - harness_failure_count,
        "blocked_count": 0,
        "harness_failure_count": harness_failure_count,
        "cleanup_failures": 0,
        "selected_rows": selected_rows,
        "compile_results": compile_results,
        "execution_results": execution_results,
        "gate_results": gate_results,
        "observation_receipts": observation_receipts,
        "discovered_operations": discovered_operations,
        "findings": [],
    }

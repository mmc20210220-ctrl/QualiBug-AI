"""Public Behavior IR facade with fail-closed authority projections."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import behavior_ir_mainline_base as _base
from .compensation_derivation_authority import install_compensation_derivation_authority
from .database_body_reference_projection import project_database_body_reference_relations
from .enterprise_implementation_authority_projection import (
    project_enterprise_implementation_authority,
)
from .openapi_security_authority import project_operation_security_provenance
from .operation_service_ownership_authority import (
    install_operation_service_ownership_authority,
)

install_compensation_derivation_authority(_base._core)
# Service ownership is transport identity. Install the source-backed authority
# before any public Behavior IR build so unfamiliar source filenames do not
# silently lose their owning service merely because they do not follow a
# benchmark-era ``*_service.json`` convention.
install_operation_service_ownership_authority(_base._core)

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)

_original_build_behavior_ir = _base.build_behavior_ir_from_knowledge_asset


def _service_for_operation(operation: dict[str, Any], data: dict[str, Any]) -> str:
    service = str(
        operation.get("service")
        or operation.get("service_name")
        or operation.get("server")
        or ""
    ).strip()
    if service:
        return service
    return str(_base._core._service_name_from_source_refs(operation, data) or "").strip()


def _transport_key(operation: dict[str, Any], data: dict[str, Any]) -> tuple[str, str, str] | None:
    method = str(
        operation.get("method")
        or operation.get("http_method")
        or "GET"
    ).strip().upper()
    path = str(
        operation.get("path")
        or operation.get("endpoint")
        or operation.get("url")
        or ""
    ).strip()
    if not path:
        return None
    return method, _base._core._path_shape(path), _service_for_operation(operation, data)


def _service_aware_operation_inputs(
    asset: dict[str, Any] | None,
    api_operations: list[dict[str, Any]] | None,
    operation_path_scope: set[tuple[str, str]] | None,
) -> tuple[
    dict[str, Any] | None,
    list[dict[str, Any]] | None,
    set[tuple[str, str]] | None,
    dict[tuple[str, str, str], str],
    list[dict[str, Any]],
]:
    """Protect service execution identity before the legacy core dedupe runs.

    The immutable core historically deduplicates by METHOD+path. The public
    authority layer therefore projects the input into that core with a
    service-specific synthetic path whenever two explicit service owners share
    one transport shape. The synthetic path is restored immediately after the
    core returns, so callers retain the source-declared path while the core can
    no longer collapse distinct executable operations.

    Service-less rows are lower-precision evidence: a unique source owner is
    attached to it; multiple owners are rejected with a visible coverage gap.
    """
    if not isinstance(asset, dict):
        return asset, api_operations, operation_path_scope, {}, []

    data = deepcopy(asset)
    submitted = [dict(row) for row in (api_operations or []) if isinstance(row, dict)]
    asset_key = "operations" if data.get("operations") else "interfaces"
    asset_rows = [dict(row) for row in data.get(asset_key, []) if isinstance(row, dict)]

    all_rows = [*submitted, *asset_rows]
    owners: dict[tuple[str, str], set[str]] = {}
    for row in all_rows:
        key = _transport_key(row, data)
        if key is None:
            continue
        method, path_shape, service = key
        if service:
            owners.setdefault((method, path_shape), set()).add(service)

    synthetic_paths: dict[tuple[str, str, str], str] = {}
    ambiguous_rows: list[dict[str, Any]] = []

    def prepare(row: dict[str, Any]) -> dict[str, Any] | None:
        key = _transport_key(row, data)
        if key is None:
            return row
        method, path_shape, service = key
        candidate_owners = sorted(owners.get((method, path_shape), set()))

        if not service:
            if len(candidate_owners) == 1:
                service = candidate_owners[0]
                row["service"] = service
            elif len(candidate_owners) > 1:
                ambiguous_rows.append({
                    "method": method,
                    "path_shape": path_shape,
                    "candidate_service_refs": candidate_owners,
                    "source_id": str(row.get("source_id") or "api_spec").strip(),
                    "path": str(
                        row.get("path")
                        or row.get("endpoint")
                        or row.get("url")
                        or ""
                    ).strip(),
                })
                return None

        if len(candidate_owners) > 1 and service:
            original_path = str(
                row.get("path")
                or row.get("endpoint")
                or row.get("url")
                or ""
            ).strip()
            if original_path:
                synthetic = (
                    original_path.rstrip("/")
                    + "/__qualibug_service_scope__/"
                    + _base._core._stable_id("service_transport", service)
                )
                synthetic_paths[(method, service, synthetic)] = original_path
                if "path" in row:
                    row["path"] = synthetic
                elif "endpoint" in row:
                    row["endpoint"] = synthetic
                else:
                    row["url"] = synthetic
        return row

    prepared_submitted = [
        prepared
        for row in submitted
        if (prepared := prepare(row)) is not None
    ]
    prepared_asset_rows = [
        prepared
        for row in asset_rows
        if (prepared := prepare(row)) is not None
    ]

    if asset_key == "operations":
        data["operations"] = prepared_asset_rows
    else:
        data["interfaces"] = prepared_asset_rows

    prepared_scope: set[tuple[str, str]] | None
    if operation_path_scope is None:
        prepared_scope = None
    else:
        prepared_scope = {
            (str(method).upper(), str(path).rstrip("/"))
            for method, path in operation_path_scope
        }
        for (method, service, synthetic_path), original_path in synthetic_paths.items():
            if (method, original_path.rstrip("/")) in prepared_scope:
                prepared_scope.add((method, synthetic_path.rstrip("/")))

    return data, prepared_submitted, prepared_scope, synthetic_paths, ambiguous_rows


def _restore_service_aware_paths(
    model: dict[str, Any],
    synthetic_paths: dict[tuple[str, str, str], str],
) -> None:
    if not synthetic_paths:
        return
    for operation in model.get("operations", []):
        if not isinstance(operation, dict):
            continue
        method = str(operation.get("method") or "").strip().upper()
        service = str(
            operation.get("service")
            or operation.get("_service_name")
            or ""
        ).strip()
        path = str(operation.get("path") or operation.get("raw_path") or "").strip()
        original_path = synthetic_paths.get((method, service, path))
        if not original_path:
            continue
        if operation.get("path") == path:
            operation["path"] = original_path
        if operation.get("raw_path") == path:
            operation["raw_path"] = original_path
        for source_ref in operation.get("source_refs", []):
            if not isinstance(source_ref, dict):
                continue
            locator = str(source_ref.get("locator") or "")
            source_ref["locator"] = locator.replace(
                f"{method} {path}",
                f"{method} {original_path}",
            )


def _append_service_ownership_gaps(
    model: dict[str, Any],
    ambiguous_rows: list[dict[str, Any]],
) -> None:
    for row in ambiguous_rows:
        method = row["method"]
        path_shape = row["path_shape"]
        owners = list(row["candidate_service_refs"])
        source_id = row["source_id"]
        model.setdefault("coverage_gaps", []).append(
            _base._core._fact_node(
                node_id=_base._core._stable_id(
                    "gap",
                    "operation_service_ownership_ambiguous",
                    method,
                    path_shape,
                    *owners,
                ),
                typed_fields={
                    "gap_type": "operation_service_ownership_ambiguous",
                    "reason_code": "OPERATION_SERVICE_OWNERSHIP_AMBIGUOUS",
                    "description": (
                        "A service-agnostic operation matches multiple source-declared "
                        "service owners and cannot be safely attached to one execution identity"
                    ),
                    "method": method,
                    "path_shape": path_shape,
                    "candidate_service_refs": owners,
                },
                source_refs=[
                    _base._core._source_ref(
                        source_id,
                        locator=f"{method} {row['path']}",
                        kind="api_operation",
                    )
                ],
                confidence=1.0,
                derivation="explicit",
                status="unsupported",
            )
        )


def build_behavior_ir_from_knowledge_asset(
    asset: dict[str, Any] | None,
    *,
    project_id: str = "",
    source_snapshot_hash: str = "",
    api_operations: list[dict[str, Any]] | None = None,
    runtime_actors: list[dict[str, Any]] | None = None,
    available_surfaces: dict[str, bool] | None = None,
    operation_path_scope: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    (
        prepared_asset,
        prepared_api_operations,
        prepared_scope,
        synthetic_paths,
        ambiguous_rows,
    ) = _service_aware_operation_inputs(
        asset,
        api_operations,
        operation_path_scope,
    )
    model = _original_build_behavior_ir(
        prepared_asset,
        project_id=project_id,
        source_snapshot_hash=source_snapshot_hash,
        api_operations=prepared_api_operations,
        runtime_actors=runtime_actors,
        available_surfaces=available_surfaces,
        operation_path_scope=prepared_scope,
    )
    _restore_service_aware_paths(model, synthetic_paths)
    _append_service_ownership_gaps(model, ambiguous_rows)

    # Enterprise Understanding already owns the source-backed behavior→interface
    # decision. Project that exact authority into runtime invariants through the
    # shared fact identity before obligation compilation. Candidate-only, fuzzy,
    # ambiguous, and conflicting bindings remain fail-closed.
    model = project_enterprise_implementation_authority(model, asset)
    model = project_database_body_reference_relations(model, asset)
    model = project_operation_security_provenance(
        model,
        asset=asset,
        api_operations=api_operations,
    )
    model["model_id"] = _base._core._content_addressed_id(model)
    return model

_base.build_behavior_ir_from_knowledge_asset = build_behavior_ir_from_knowledge_asset


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))

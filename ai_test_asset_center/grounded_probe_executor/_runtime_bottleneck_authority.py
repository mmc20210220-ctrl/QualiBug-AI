"""Runtime authority for multi-service reachability and evidence quality.

The authority is intentionally project-agnostic: routing is derived only from
customer-declared service OpenAPI contracts/configuration, probe budgeting is
surface-diverse, transport values follow declared schemas, and a finding is not
confirmed merely because a negative-intent write returned 2xx.
"""
from __future__ import annotations

import copy
import json
import re
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

_INSTALLED = False
_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def _route_shape(path: Any) -> str:
    return re.sub(r"\{[^{}]+\}", "{}", str(path or "").split("?", 1)[0].rstrip("/") or "/")


def _service_route_index(config: dict[str, Any], default_base_url: str) -> dict[tuple[str, str], str]:
    cache_key = "_qualibug_service_route_index"
    cached = config.get(cache_key)
    if isinstance(cached, dict):
        return cached
    index: dict[tuple[str, str], str] = {}
    ambiguous: set[tuple[str, str]] = set()
    default_host = (urllib.parse.urlparse(str(default_base_url or "")).hostname or "").lower()
    input_dir = Path(str(config.get("input_dir") or config.get("project_input_dir") or "."))
    services = config.get("services") if isinstance(config.get("services"), list) else []
    for service in services:
        if not isinstance(service, dict):
            continue
        service_base = str(service.get("base_url") or service.get("url") or "").rstrip("/")
        service_host = (urllib.parse.urlparse(service_base).hostname or "").lower()
        if not service_base or not default_host or service_host != default_host:
            continue
        spec_ref = str(service.get("openapi_spec") or service.get("openapi") or service.get("spec") or "").strip()
        if not spec_ref:
            continue
        candidates = [Path(spec_ref)]
        if not Path(spec_ref).is_absolute():
            candidates.extend([input_dir / spec_ref, input_dir / "openapi" / Path(spec_ref).name])
        spec_path = next((path for path in candidates if path.exists() and path.is_file()), None)
        if spec_path is None:
            continue
        try:
            text = spec_path.read_text(encoding="utf-8", errors="replace")
            if spec_path.suffix.lower() == ".json":
                spec = json.loads(text)
            else:
                import yaml
                spec = yaml.safe_load(text) or {}
        except Exception:
            continue
        paths = spec.get("paths") if isinstance(spec, dict) else {}
        if not isinstance(paths, dict):
            continue
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method in path_item:
                method_u = str(method).upper()
                if method_u not in _HTTP_METHODS:
                    continue
                key = (method_u, _route_shape(path))
                if key in index and index[key] != service_base:
                    ambiguous.add(key)
                else:
                    index[key] = service_base
    for key in ambiguous:
        index.pop(key, None)
    config[cache_key] = index
    return index


def _probe_base_url(probe: dict[str, Any], config: dict[str, Any], default_base_url: str) -> str:
    endpoint = probe.get("endpoint") if isinstance(probe.get("endpoint"), dict) else {}
    method = str(endpoint.get("method") or probe.get("method") or "GET").upper()
    path = str(endpoint.get("path") or probe.get("path") or "/")
    return _service_route_index(config, default_base_url).get((method, _route_shape(path)), default_base_url)


def _request_base_url(method: Any, path: Any, config: dict[str, Any], default_base_url: str) -> str:
    return _service_route_index(config, default_base_url).get((str(method).upper(), _route_shape(path)), default_base_url)


def _priority(probe: dict[str, Any]) -> float:
    value = probe.get("validation_priority")
    if isinstance(value, dict):
        value = value.get("score")
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def select_endpoint_diverse_probes(probes: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Round-robin endpoints while balancing risk families within each surface."""
    if limit <= 0 or len(probes) <= limit:
        return list(probes)
    groups: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
    for position, probe in enumerate(probes):
        endpoint = probe.get("endpoint") if isinstance(probe.get("endpoint"), dict) else {}
        key = (
            str(endpoint.get("method") or probe.get("method") or "GET").upper(),
            str(endpoint.get("path") or probe.get("path") or ""),
        )
        groups.setdefault(key, []).append((position, probe))
    ordered = sorted(
        groups.values(),
        key=lambda items: (-max(_priority(probe) for _, probe in items), min(position for position, _ in items)),
    )
    remaining = [list(items) for items in ordered]
    selected: list[dict[str, Any]] = []
    risk_counts: dict[str, int] = {}
    while len(selected) < limit:
        added = False
        for items in remaining:
            if not items or len(selected) >= limit:
                continue
            best = min(
                range(len(items)),
                key=lambda idx: (
                    risk_counts.get(str(items[idx][1].get("risk_type") or "unknown"), 0),
                    -_priority(items[idx][1]),
                    items[idx][0],
                ),
            )
            _, probe = items.pop(best)
            selected.append(probe)
            risk = str(probe.get("risk_type") or "unknown")
            risk_counts[risk] = risk_counts.get(risk, 0) + 1
            added = True
        if not added:
            break
    return selected


def _merge_recursive_openapi(original_loader):
    from ..runtime_input_authority import load_recursive_openapi

    def load_openapi_from_input(input_dir):
        base = original_loader(input_dir)
        root = Path(input_dir) if input_dir else None
        if root is None or not root.exists():
            return base
        recursive, _source = load_recursive_openapi(root)
        if not recursive:
            return base
        if not isinstance(base, dict) or not base:
            return recursive
        merged = copy.deepcopy(base)
        merged.setdefault("openapi", recursive.get("openapi") or "3.0.3")
        merged.setdefault("info", recursive.get("info") or {"title": "QualiBug aggregate", "version": "1"})
        paths = merged.setdefault("paths", {})
        for path, item in (recursive.get("paths") or {}).items():
            target = paths.setdefault(path, {})
            if not isinstance(target, dict) or not isinstance(item, dict):
                continue
            for method, operation in item.items():
                target.setdefault(method, operation)
        components = merged.setdefault("components", {})
        for section, values in (recursive.get("components") or {}).items():
            if not isinstance(values, dict):
                continue
            target = components.setdefault(section, {})
            if isinstance(target, dict):
                for key, value in values.items():
                    target.setdefault(key, value)
        return merged

    return load_openapi_from_input


def _typed_value_from_schema(factory, name: str, schema: dict[str, Any], seed: str, spec: dict[str, Any]) -> Any:
    try:
        return factory._schema_value(name, schema, seed, spec)
    except Exception:
        typ = str(schema.get("type") or "").lower()
        if typ == "integer":
            return 1
        if typ == "number":
            return 1.0
        if typ == "boolean":
            return True
        return None


def _typed_fixture_wrapper(factory, original_builder):
    def build_auto_fixture_for_probe(probe: dict[str, Any], *, input_dir=None, config=None):
        bundle = original_builder(probe, input_dir=input_dir, config=config)
        if not isinstance(bundle, dict):
            return bundle
        endpoint = probe.get("endpoint") if isinstance(probe.get("endpoint"), dict) else {}
        method = str(endpoint.get("method") or "GET").upper()
        path = str(endpoint.get("path") or "")
        current = bundle.get("path_params") if isinstance(bundle.get("path_params"), dict) else {}
        if not current or not path:
            return bundle
        cfg = config if isinstance(config, dict) else {}
        try:
            spec = factory.load_openapi_from_input(input_dir or cfg.get("input_dir") or cfg.get("project_input_dir"))
            operation = factory._operation(spec, method, path)
        except Exception:
            return bundle
        schemas: dict[str, dict[str, Any]] = {}
        path_item = (spec.get("paths") or {}).get(path) if isinstance(spec, dict) else None
        params = []
        if isinstance(path_item, dict):
            params.extend(path_item.get("parameters") or [])
        if isinstance(operation, dict):
            params.extend(operation.get("parameters") or [])
        for raw in params:
            parameter = raw if isinstance(raw, dict) else {}
            if isinstance(parameter.get("$ref"), str):
                try:
                    parameter = factory._resolve_ref(str(parameter["$ref"]), spec)
                except Exception:
                    parameter = {}
            if str(parameter.get("in") or "").lower() != "path":
                continue
            name = str(parameter.get("name") or "")
            schema = parameter.get("schema") if isinstance(parameter.get("schema"), dict) else {}
            if isinstance(schema.get("$ref"), str):
                try:
                    schema = factory._resolve_ref(str(schema["$ref"]), spec)
                except Exception:
                    schema = {}
            if name:
                schemas[name] = schema if isinstance(schema, dict) else {}
        seed = str(probe.get("candidate_id") or "probe")
        for name, old_value in list(current.items()):
            schema = schemas.get(name) or {}
            typ = str(schema.get("type") or "").lower()
            fmt = str(schema.get("format") or "").lower()
            if typ in {"integer", "number", "boolean"} or fmt:
                value = _typed_value_from_schema(factory, name, schema, seed, spec)
                if value is not None:
                    current[name] = value
        bundle["path_params"] = current
        return bundle

    return build_auto_fixture_for_probe


def _verified_write_wrapper(core, original_verifier):
    def verify(probe, responses, snapshots):
        result = original_verifier(probe, responses, snapshots)
        if not isinstance(result, dict) or result.get("verdict") != "validated_candidate":
            return result
        reason = str(result.get("reason") or "")
        if not reason.startswith("negative sandbox write was accepted with HTTP"):
            return result
        risk = str(probe.get("risk_type") or "")
        if risk not in {"auth_boundary_probe", "ownership_scope_probe", "state_transition_probe", "async_external_event_probe"}:
            return result
        first = next((row for row in responses if isinstance(row, dict) and isinstance(row.get("status_code"), int)), {})
        payload = first.get("payload")
        if risk == "auth_boundary_probe" and core._has_business_data(payload):
            return result
        downgraded = dict(result)
        downgraded.update(
            {
                "verdict": "needs_more_evidence",
                "reason": (
                    "negative-intent sandbox write returned 2xx, but no concrete runtime "
                    "negative-control evidence proves the cross-boundary/invalid-state condition was exercised"
                ),
                "confidence": 0.42,
            }
        )
        return downgraded

    return verify


def _classify_wrapper(mechanics, original_classifier):
    def classify(name: str, text: str, explicit: str | None = None):
        labels = list(original_classifier(name, text, explicit))
        if "openapi" not in labels or str(explicit or "").strip().lower() == "openapi":
            return labels
        try:
            data = mechanics._json_or_none(text)
        except Exception:
            data = None
        structured = bool(
            isinstance(data, dict)
            and isinstance(data.get("paths"), dict)
            and (data.get("openapi") or data.get("swagger"))
        )
        if not structured:
            labels = [label for label in labels if label != "openapi"]
        return labels or ["collaboration_document"]

    return classify


def install(package_globals: dict[str, Any] | None = None) -> None:
    global _INSTALLED
    if _INSTALLED:
        if package_globals is not None:
            from . import _entry
            package_globals["run_grounded_probe_executor"] = _entry.run_grounded_probe_executor
        return

    from . import _core, _entry
    from .. import auto_test_data_factory as factory
    from .. import openapi_spec_utils
    from ..enterprise_knowledge_center import _parsing_mechanics as mechanics

    # Source classification: keyword mentions are not executable OpenAPI authority.
    mechanics._classify_source_multi = _classify_wrapper(mechanics, mechanics._classify_source_multi)

    # Nested per-service OpenAPI contracts feed fixture generation.
    recursive_loader = _merge_recursive_openapi(openapi_spec_utils.load_openapi_from_input)
    openapi_spec_utils.load_openapi_from_input = recursive_loader
    factory.load_openapi_from_input = recursive_loader

    # Type-compatible path parameters are applied after the existing grounded
    # fixture planner, preserving all of its setup/cleanup provenance.
    factory.build_auto_fixture_for_probe = _typed_fixture_wrapper(factory, factory.build_auto_fixture_for_probe)

    # Evidence authority: generic 2xx acceptance is not sufficient confirmation.
    _core._verify_write_observation = _verified_write_wrapper(_core, _core._verify_write_observation)

    original_decide = _entry._decide_probe
    original_read = _entry._execute_read_probe
    original_write = _entry._execute_write_probe
    original_flow = _entry._execute_flow_probe

    def decide(probe, *, base_url, config, options):
        return original_decide(probe, base_url=_probe_base_url(probe, config, base_url), config=config, options=options)

    def read(probe, decision, config, base_url, timeout):
        return original_read(probe, decision, config, _probe_base_url(probe, config, base_url), timeout)

    def write(probe, decision, config, base_url, timeout):
        return original_write(probe, decision, config, _probe_base_url(probe, config, base_url), timeout)

    def flow(probe, decision, config, base_url, timeout):
        routed = copy.deepcopy(probe)
        plan = routed.get("probe_plan") if isinstance(routed.get("probe_plan"), dict) else {}
        scenario = plan.get("flow_scenario") if isinstance(plan.get("flow_scenario"), dict) else {}
        for step in scenario.get("steps") or []:
            if not isinstance(step, dict):
                continue
            method = str(step.get("method") or getattr(decision, "method", "GET")).upper()
            path = str(step.get("path") or getattr(decision, "path", "/"))
            if re.match(r"^https?://", path, re.I):
                continue
            service_base = _request_base_url(method, path, config, base_url)
            step["path"] = service_base.rstrip("/") + "/" + path.lstrip("/")
        return original_flow(routed, decision, config, _probe_base_url(probe, config, base_url), timeout)

    _entry._decide_probe = decide
    _entry._execute_read_probe = read
    _entry._execute_write_probe = write
    _entry._execute_flow_probe = flow

    original_run = _entry.run_grounded_probe_executor

    def run_grounded_probe_executor(*args, **kwargs):
        max_probes = int(kwargs.get("max_probes") or 0)
        plan_path = kwargs.get("probe_plan_path")
        if max_probes > 0 and plan_path:
            try:
                path = Path(plan_path)
                payload = json.loads(path.read_text(encoding="utf-8"))
                probes = payload.get("probes") if isinstance(payload, dict) else None
                if isinstance(probes, list) and len(probes) > max_probes:
                    selected = select_endpoint_diverse_probes(
                        [probe for probe in probes if isinstance(probe, dict)], max_probes
                    )
                    selected_ids = {id(probe) for probe in selected}
                    # Keep selected probes first because the established runner
                    # applies its own prefix slice. Append the rest unchanged so
                    # the derived plan remains complete and auditable.
                    reordered = selected + [probe for probe in probes if id(probe) not in selected_ids]
                    derived = dict(payload)
                    derived["probes"] = reordered
                    out_dir = Path(kwargs.get("out_dir") or tempfile.mkdtemp(prefix="qualibug_runtime_"))
                    out_dir.mkdir(parents=True, exist_ok=True)
                    derived_path = out_dir / "runtime_endpoint_diverse_probe_plan.json"
                    derived_path.write_text(json.dumps(derived, ensure_ascii=False, indent=2), encoding="utf-8")
                    kwargs["probe_plan_path"] = derived_path
            except Exception:
                pass
        return original_run(*args, **kwargs)

    _entry.run_grounded_probe_executor = run_grounded_probe_executor
    if package_globals is not None:
        package_globals["run_grounded_probe_executor"] = run_grounded_probe_executor
    _INSTALLED = True

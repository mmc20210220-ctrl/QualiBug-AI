from __future__ import annotations

"""Evidence-first business world model compiler.

The platform already has several specialised reasoning engines.  This module is
not another detection engine: it compiles their shared business vocabulary into
an explicit, reviewable model so a team can confirm a small number of grounded
relationships instead of hand-writing every Oracle from scratch.

Important boundaries:
* OpenAPI/PRD inference produces *candidates* only.
* A candidate becomes a reusable Oracle contract only after an explicit human
  confirmation in ``business_world_model.confirmations``.
* The module never performs HTTP requests, never mutates a target and never
  creates a formal finding by itself.
"""

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import (
    ROOT,
    _load_json,
    _read_text,
    _safe_project_id,
    _write_json,
    config_paths,
    load_real_project_config,
)

SAFE_METHODS = {"GET"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash(value: Any, length: int = 18) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:length]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


def _singular(value: str) -> str:
    value = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", value.lower()).strip("_")
    if value.endswith("ies") and len(value) > 4:
        return value[:-3] + "y"
    if value.endswith("ses") and len(value) > 4:
        return value[:-2]
    if value.endswith("s") and len(value) > 3:
        return value[:-1]
    return value or "resource"


def _paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    cfg = config_paths(project, root)
    workspace = root / "platform_workspace" / project / "business_world_model"
    output = root / "platform_outputs" / project / "business_world_model"
    return {
        **cfg,
        "workspace": workspace,
        "output": output,
        "profile": workspace / "business_world_model_profile.json",
        "proposal": workspace / "business_world_model_confirmation_template.json",
    }


def _resolve_schema(schema: Any, components: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    if depth > 8 or not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        key = ref.rsplit("/", 1)[-1]
        return _resolve_schema((components.get("schemas") or {}).get(key), components, depth + 1)
    if isinstance(schema.get("items"), dict):
        item = _resolve_schema(schema["items"], components, depth + 1)
        if item:
            return item
    return schema


def _response_fields(operation: dict[str, Any], components: dict[str, Any]) -> dict[str, dict[str, Any]]:
    responses = operation.get("responses") or {}
    for code, response in responses.items():
        if not str(code).startswith("2") or not isinstance(response, dict):
            continue
        content = response.get("content") or {}
        app_json = content.get("application/json") or {}
        schema = _resolve_schema(app_json.get("schema"), components)
        props = schema.get("properties") or {}
        if isinstance(props, dict):
            # Common collection envelope: {items: [{...}]}
            items = props.get("items")
            if isinstance(items, dict):
                inner = _resolve_schema(items.get("items") or items, components)
                inner_props = inner.get("properties") or {}
                if isinstance(inner_props, dict) and inner_props:
                    return {str(k): v for k, v in inner_props.items() if isinstance(v, dict)}
            return {str(k): v for k, v in props.items() if isinstance(v, dict)}
    return {}


def _resource(path: str) -> str:
    segments = [part for part in str(path or "").strip("/").split("/") if part and not part.startswith("{")]
    return _singular(segments[-1] if segments else "resource")


def _operation_rows(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    components = openapi.get("components") or {}
    rows: list[dict[str, Any]] = []
    for path, methods in (openapi.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            method_u = str(method).upper()
            if method_u not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not isinstance(operation, dict):
                continue
            rows.append({
                "path": str(path),
                "method": method_u,
                "resource": _resource(str(path)),
                "summary": str(operation.get("summary") or operation.get("operationId") or ""),
                "fields": _response_fields(operation, components),
            })
    return rows


def _candidate_id(kind: str, payload: dict[str, Any]) -> str:
    return f"WM_{kind.upper()}_{_hash(payload)}"


def _prd_mentions(prd: str, *terms: str) -> bool:
    text = _norm(prd)
    return any(_norm(term) and _norm(term) in text for term in terms)


def _relationship_candidates(operations: list[dict[str, Any]], prd: str) -> list[dict[str, Any]]:
    resources = {str(row["resource"]) for row in operations}
    candidates: list[dict[str, Any]] = []
    for row in operations:
        for field, schema in (row.get("fields") or {}).items():
            field_l = str(field).lower()
            if not field_l.endswith("_id") or field_l in {"id", "uuid", "trace_id", "request_id"}:
                continue
            target = _singular(field_l[:-3])
            if target not in resources:
                continue
            payload = {
                "source_entity": row["resource"],
                "target_entity": target,
                "foreign_key": field,
                "path": row["path"],
                "method": row["method"],
            }
            candidate = {
                "candidate_id": _candidate_id("relation", payload),
                "candidate_type": "referential_relation",
                **payload,
                "oracle_family": "referential_integrity",
                "evidence": [
                    {"kind": "openapi_response_field", "operation": f"{row['method']} {row['path']}", "field": field},
                    {"kind": "openapi_resource", "resource": target},
                ],
                "confidence": 0.86 if _prd_mentions(prd, row["resource"], target) else 0.76,
                "status": "needs_human_confirmation",
                "execution_policy": "candidate_only",
                "why_review_required": "Schema proves the field relationship shape, but only the business owner can confirm whether every source row must reference a target row.",
            }
            candidates.append(candidate)
    return candidates


def _state_candidates(operations: list[dict[str, Any]], prd: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in operations:
        for field, schema in (row.get("fields") or {}).items():
            if str(field).lower() not in {"status", "state", "phase", "lifecycle_state"}:
                continue
            enum = schema.get("enum") if isinstance(schema, dict) else None
            if not isinstance(enum, list) or len(enum) < 2:
                continue
            states = [str(item) for item in enum if str(item).strip()][:16]
            if len(states) < 2:
                continue
            payload = {
                "entity": row["resource"],
                "state_field": field,
                "states": states,
                "path": row["path"],
                "method": row["method"],
            }
            candidates.append({
                "candidate_id": _candidate_id("state", payload),
                "candidate_type": "state_machine",
                **payload,
                "oracle_family": "state_consistency",
                "evidence": [{"kind": "openapi_enum", "operation": f"{row['method']} {row['path']}", "field": field, "states": states}],
                "confidence": 0.84 if _prd_mentions(prd, row["resource"], "status", "状态") else 0.74,
                "status": "needs_human_confirmation",
                "execution_policy": "candidate_only",
                "why_review_required": "OpenAPI enumerates states but does not prove legal transition order or terminal-state semantics.",
            })
    return candidates


def _entity_rows(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in operations:
        entity = str(row["resource"])
        item = grouped.setdefault(entity, {"entity_id": f"WM_ENTITY_{_hash(entity)}", "entity": entity, "operations": [], "fields": set()})
        item["operations"].append(f"{row['method']} {row['path']}")
        item["fields"].update((row.get("fields") or {}).keys())
    rows: list[dict[str, Any]] = []
    for item in grouped.values():
        rows.append({
            "entity_id": item["entity_id"],
            "entity": item["entity"],
            "operations": sorted(item["operations"]),
            "fields": sorted(str(field) for field in item["fields"]),
            "evidence": "openapi_path_and_response_schema",
        })
    return sorted(rows, key=lambda item: item["entity"])


def _approved_confirmations(candidates: list[dict[str, Any]], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    section = cfg.get("business_world_model") or {}
    raw = section.get("confirmations") or section.get("approved_candidates") or []
    if not isinstance(raw, list):
        raw = []
    by_id = {str(candidate.get("candidate_id")): candidate for candidate in candidates}
    confirmed: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        candidate = by_id.get(str(row.get("candidate_id") or ""))
        approver = str(row.get("approved_by") or row.get("reviewer") or "").strip()
        decision = str(row.get("decision") or "approved").strip().lower()
        if not candidate or not approver or decision not in {"approved", "confirmed"}:
            continue
        contract = {
            "contract_id": f"WMC_{_hash([candidate.get('candidate_id'), approver, row.get('approved_at_utc') or row.get('approved_at')])}",
            "candidate_id": candidate["candidate_id"],
            "candidate_type": candidate["candidate_type"],
            "oracle_family": candidate["oracle_family"],
            "entity": candidate.get("entity") or candidate.get("source_entity"),
            "source_entity": candidate.get("source_entity"),
            "target_entity": candidate.get("target_entity"),
            "foreign_key": candidate.get("foreign_key"),
            "state_field": candidate.get("state_field"),
            "states": candidate.get("states") or [],
            "path": candidate.get("path"),
            "method": "GET" if str(candidate.get("method") or "GET").upper() == "GET" else "GET",
            "approved_by": approver[:120],
            "approved_at_utc": str(row.get("approved_at_utc") or row.get("approved_at") or _now())[:40],
            "business_rule": str(row.get("business_rule") or row.get("rule") or "")[:1000],
            "status": "confirmed_contract",
            "execution_policy": "safe_read_only",
            "governance": "human_confirmed_schema_grounded_world_model_contract",
        }
        confirmed.append(contract)
    return confirmed


def build_business_world_model_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    paths = _paths(project, root)
    cfg = load_real_project_config(project, root)
    openapi = _load_json(paths["input_dir"] / "openapi.json", {})
    if not isinstance(openapi, dict):
        openapi = {}
    prd = _read_text(paths["input_dir"] / "prd.md")
    operations = _operation_rows(openapi)
    candidates = _relationship_candidates(operations, prd) + _state_candidates(operations, prd)
    seen: set[str] = set()
    candidates = [row for row in candidates if not (str(row.get("candidate_id")) in seen or seen.add(str(row.get("candidate_id"))))]
    confirmed = _approved_confirmations(candidates, cfg)
    template = {
        "business_world_model": {
            "confirmations": [
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "decision": "approved",
                    "approved_by": "business_owner",
                    "approved_at_utc": "YYYY-MM-DDTHH:MM:SSZ",
                    "business_rule": "Confirm the exact invariant; do not approve automatically.",
                }
                for candidate in candidates[:12]
            ]
        }
    }
    profile = {
        "phase": "phase72_business_world_model",
        "project_id": project,
        "generated_at_utc": _now(),
        "entities": _entity_rows(operations),
        "candidate_contracts": candidates,
        "confirmed_contracts": confirmed,
        "summary": {
            "operation_count": len(operations),
            "entity_count": len(_entity_rows(operations)),
            "candidate_relation_count": sum(1 for row in candidates if row.get("candidate_type") == "referential_relation"),
            "candidate_state_machine_count": sum(1 for row in candidates if row.get("candidate_type") == "state_machine"),
            "confirmed_contract_count": len(confirmed),
        },
        "governance": {
            "schema_and_prd_inference_is_candidate_only": True,
            "human_confirmation_required_before_probe_generation": True,
            "confirmed_contracts_generate_get_only_plans": True,
            "does_not_create_formal_findings": True,
            "raw_prd_and_openapi_payloads_not_duplicated": True,
        },
    }
    paths["workspace"].mkdir(parents=True, exist_ok=True)
    paths["output"].mkdir(parents=True, exist_ok=True)
    _write_json(paths["profile"], profile)
    _write_json(paths["output"] / "business_world_model_profile.json", profile)
    _write_json(paths["proposal"], template)
    _write_json(paths["output"] / "business_world_model_confirmation_template.json", template)
    return profile


def load_business_world_model_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _load_json(_paths(project, root)["profile"], {})
    return data if isinstance(data, dict) and data else None


def generate_business_world_model_probes(
    openapi: dict[str, Any] | None = None,
    cfg: dict[str, Any] | None = None,
    project_id: str = "real_project_demo",
    root: Path | None = None,
    max_count: int | None = None,
) -> list[dict[str, Any]]:
    """Generate only approved, GET-only follow-up probes from confirmed contracts."""
    del openapi, cfg  # Profile is the authoritative reviewed model.
    root = root or ROOT
    profile = load_business_world_model_profile(project_id, root) or build_business_world_model_profile(project_id, root)
    limit = max(1, int(max_count or 40))
    probes: list[dict[str, Any]] = []
    for contract in profile.get("confirmed_contracts") or []:
        if not isinstance(contract, dict) or not contract.get("path"):
            continue
        probes.append({
            "probe_id": f"WMP_{_hash(contract.get('contract_id'))}",
            "source": "business_world_model",
            "contract_id": contract.get("contract_id"),
            "oracle_family": contract.get("oracle_family"),
            "risk_type": "business_world_model_confirmed_relation",
            "severity": "P1" if contract.get("candidate_type") == "referential_relation" else "P2",
            "title": f"已确认世界模型契约：{contract.get('oracle_family')}",
            "method": "GET",
            "path": contract.get("path"),
            "expected": contract.get("business_rule") or "已确认的业务关系或状态约束必须持续成立。",
            "execution_policy": "safe_read_only",
            "requires_human_confirmation": False,
            "world_model_candidate_id": contract.get("candidate_id"),
            "governance": "derived_from_explicit_human_confirmation",
        })
    return probes[:limit]

"""UI follow-up candidate asset materialization for product scans.

Extracted from ``__main__``. Symbols are re-exported from ``__main__``
for compatibility with existing tests and callers.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .product_scan_mainline import _as_dict, _first_text, _safe_project, _sha256


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Persist JSON only after unified recursive redaction + secret scan."""
    from .artifact_redactor import ArtifactSecretLeakError, write_json_redacted

    try:
        write_json_redacted(path, payload)
    except ArtifactSecretLeakError as exc:
        import sys as _sys

        print(
            f"[scan] FAILED_SAFE artifact secret scan blocked write to {path}: {exc}",
            file=_sys.stderr,
        )
        raise


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}

def _ui_candidate_target_path(item: dict[str, Any]) -> str:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    raw = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
    ui_result = raw.get("ui_execution_result") if isinstance(raw.get("ui_execution_result"), dict) else {}
    target = str(ui_result.get("current_url") or evidence.get("target") or item.get("_api_path") or item.get("path") or "").strip()
    if not target:
        return "/"
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return parsed.path or "/"
    return target


def _ui_candidate_method(item: dict[str, Any]) -> str:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    request = evidence.get("request") if isinstance(evidence.get("request"), dict) else {}
    reproduction = item.get("reproduction") if isinstance(item.get("reproduction"), dict) else {}
    method = str(item.get("_api_method") or request.get("method") or reproduction.get("method") or "GET").strip().upper()
    return method or "GET"


def _normalize_ui_verification_http_path(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    normalized = re.sub(r"\{id\}", "{object_id}", text, flags=re.IGNORECASE)
    normalized = re.sub(r"<id>", "{object_id}", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r":id(?=/|$)", "{object_id}", normalized, flags=re.IGNORECASE)
    return normalized


def _candidate_followup_verification_template(item: dict[str, Any], *, path: str, method: str) -> dict[str, Any]:
    raw = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
    ui_result = raw.get("ui_execution_result") if isinstance(raw.get("ui_execution_result"), dict) else {}
    ui_metadata = ui_result.get("metadata") if isinstance(ui_result.get("metadata"), dict) else {}
    verification = ui_metadata.get("verification") if isinstance(ui_metadata.get("verification"), dict) else {}
    if verification:
        return dict(verification)
    item_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    verification = item_metadata.get("verification") if isinstance(item_metadata.get("verification"), dict) else {}
    if verification:
        return dict(verification)
    created_data = raw.get("created_data") if isinstance(raw.get("created_data"), dict) else {}
    object_id = str(created_data.get("object_id") or "").strip()
    normalized_path = _normalize_ui_verification_http_path(path)
    if method != "GET" or not normalized_path.startswith("/") or not object_id:
        return {}
    return {
        "kind": "http_get",
        "path": normalized_path,
        "expected_statuses": [200],
        "body_contains": "{object_id}",
    }


def _source_bound_followup_verification_template(
    matching_scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    for scenario in matching_scenarios:
        if not isinstance(scenario, dict):
            continue
        scenario_metadata = scenario.get("metadata") if isinstance(scenario.get("metadata"), dict) else {}
        verification = scenario_metadata.get("verification") if isinstance(scenario_metadata.get("verification"), dict) else {}
        if verification:
            return dict(verification)
        expected_state = str(
            scenario.get("expected_state")
            or scenario.get("expected")
            or ""
        ).strip()
        for step in scenario.get("steps", []):
            if not isinstance(step, dict):
                continue
            method = str(step.get("method") or "GET").strip().upper()
            path = _normalize_ui_verification_http_path(str(step.get("path") or "").strip())
            if method != "GET" or not path.startswith("/"):
                continue
            template = {
                "kind": "http_get",
                "path": path,
                "expected_statuses": [200],
            }
            if expected_state:
                template["body_contains"] = expected_state
            return template
    return {}


def _ui_followup_execution_template(
    item: dict[str, Any],
    *,
    project: str,
    scan_id: str,
    campaign: dict[str, Any],
    generated_at: str,
    index: int,
) -> dict[str, Any]:
    title = str(item.get("title") or f"ui_candidate_{index}").strip() or f"ui_candidate_{index}"
    path = _ui_candidate_target_path(item) or "/"
    method = _ui_candidate_method(item)
    risk_type = str(item.get("risk_type") or item.get("defect_family") or "ui_execution").strip() or "ui_execution"
    severity = str(item.get("severity") or "P2").strip().upper()
    candidate_id = str(item.get("risk_id") or item.get("bug_id") or _sha256(f"{title}|{method}|{path}")[:16]).strip()
    expected = str(item.get("expected") or item.get("expected_behavior") or "页面状态、关键提示和业务结果应与既有候选证据一致。").strip()
    reproduction_steps = [str(step).strip()[:500] for step in (item.get("reproduction_steps") or []) if str(step).strip()]
    page_hints = [
        hint
        for hint in (
            f"candidate severity: {severity}" if severity else "",
            f"risk type: {risk_type}" if risk_type else "",
            f"candidate tier: {str(item.get('candidate_tier') or 'ui_candidate').strip()}",
            *reproduction_steps[:3],
        )
        if hint
    ]
    verification = item.get("ui_verification") if isinstance(item.get("ui_verification"), dict) else {}
    followup_kind = (
        "ui_evidence_enrichment"
        if str(verification.get("status") or item.get("verification_badge") or "").strip().lower() == "verified"
        else "reproduction_assistant"
    )
    verification_template = _candidate_followup_verification_template(item, path=path, method=method)
    return {
        "request_template_id": f"UIFOLLOW_{candidate_id}",
        "scan_id": scan_id,
        "campaign_id": str(campaign.get("campaign_id") or ""),
        "project_id": project,
        "title": title,
        "severity": severity if severity in {"P0", "P1", "P2", "P3"} else "P2",
        "risk_type": risk_type,
        "method": method,
        "path": path or "/",
        "task": f"Re-open the candidate target and capture deterministic UI evidence for: {title}",
        "expected": expected,
        "page_hints": page_hints,
        "success_criteria": {"url_pattern": path or "/"} if path else {},
        "browser_plan": {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "goto", "url": path or "/", "wait_until": "networkidle"},
                {"action": "wait_for_load", "state": "networkidle"},
                {"action": "screenshot", "full_page": True},
            ],
        },
        "metadata": {
            "auto_generated": True,
            "request_origin": "ui_followup_execution_asset",
            "bridge_mode": "page_agent_browser_plan",
            "source_candidate_id": candidate_id,
            "candidate_tier": str(item.get("candidate_tier") or "ui_candidate"),
            "verification_badge": str(item.get("verification_badge") or ""),
            "followup_kind": followup_kind,
            "verification": verification_template,
        },
        "generated_at_utc": generated_at,
    }


def _source_bound_ui_followup_templates(
    *,
    project: str,
    scan_id: str,
    campaign: dict[str, Any],
    generated_at: str,
    selected_slices: list[dict[str, Any]] | None,
    plan_only_scenarios: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    scenarios_by_slice: dict[str, list[dict[str, Any]]] = {}
    for item in plan_only_scenarios or []:
        if not isinstance(item, dict):
            continue
        slice_id = str(item.get("behavior_slice_id") or "").strip()
        if slice_id:
            scenarios_by_slice.setdefault(slice_id, []).append(item)
    templates: list[dict[str, Any]] = []
    for index, slice_item in enumerate(selected_slices or [], start=1):
        if not isinstance(slice_item, dict):
            continue
        slice_id = str(slice_item.get("slice_id") or "").strip()
        if not slice_id:
            continue
        entity = str(slice_item.get("entity") or "entity").strip() or "entity"
        kind = str(slice_item.get("kind") or "source_bound").strip() or "source_bound"
        endpoints = [str(value).strip() for value in slice_item.get("endpoints", []) if str(value).strip()]
        source_refs = [dict(value) for value in slice_item.get("source_refs", []) if isinstance(value, dict)]
        matching_scenarios = scenarios_by_slice.get(slice_id, [])
        scenario_titles = [str(item.get("title") or "").strip() for item in matching_scenarios if str(item.get("title") or "").strip()]
        scenario_categories = [str(item.get("category") or "").strip() for item in matching_scenarios if str(item.get("category") or "").strip()]
        followup_kind = "ui_evidence_enrichment" if kind == "source_observation" else "reproduction_assistant"
        verification_template = _source_bound_followup_verification_template(matching_scenarios)
        page_hints = [
            hint
            for hint in (
                f"behavior slice: {slice_id}",
                f"entity: {entity}",
                f"slice kind: {kind}",
                *(f"source endpoint: {path}" for path in endpoints[:3]),
                *(f"scenario title: {title}" for title in scenario_titles[:2]),
                *(f"scenario category: {category}" for category in scenario_categories[:2]),
                *(
                    f"source reference: {str(ref.get('locator') or ref.get('quote') or '').strip()}"
                    for ref in source_refs[:2]
                    if str(ref.get("locator") or ref.get("quote") or "").strip()
                ),
            )
            if hint
        ]
        task_tail = scenario_titles[0] if scenario_titles else f"{entity} / {kind}"
        templates.append(
            {
                "request_template_id": f"UISLICE_{slice_id}",
                "scan_id": scan_id,
                "campaign_id": str(campaign.get("campaign_id") or ""),
                "project_id": project,
                "title": f"Source-bound UI follow-up: {task_tail}",
                "severity": str((matching_scenarios[0].get("severity") if matching_scenarios else "") or "P2").strip().upper() or "P2",
                "risk_type": f"source_bound_{kind}",
                "method": "GET",
                "path": "/",
                "task": (
                    "Open the approved application entry URL, navigate toward the source-bound flow, "
                    f"and capture deterministic UI evidence for behavior slice {slice_id}."
                ),
                "expected": (
                    str(matching_scenarios[0].get("expected_state") or "").strip()
                    if matching_scenarios
                    else f"UI behavior should remain consistent with the source-bound obligation for {entity}."
                ),
                "page_hints": page_hints,
                "success_criteria": {},
                "browser_plan": {
                    "execution_mode": "safe_read_only",
                    "steps": [
                        {"action": "goto", "url": "/", "wait_until": "networkidle"},
                        {"action": "wait_for_load", "state": "networkidle"},
                        {"action": "screenshot", "full_page": True},
                    ],
                },
                "metadata": {
                    "auto_generated": True,
                    "request_origin": "source_bound_slice_followup_asset",
                    "bridge_mode": "page_agent_browser_plan",
                    "behavior_slice_id": slice_id,
                    "slice_kind": kind,
                    "scenario_count": len(matching_scenarios),
                    "followup_kind": followup_kind,
                    "verification": verification_template,
                },
                "generated_at_utc": generated_at,
            }
        )
    return templates


def _source_bound_ui_test_data_templates(
    *,
    project: str,
    scan_id: str,
    campaign: dict[str, Any],
    generated_at: str,
    selected_slices: list[dict[str, Any]] | None,
    plan_only_scenarios: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    scenarios_by_slice: dict[str, list[dict[str, Any]]] = {}
    for item in plan_only_scenarios or []:
        if not isinstance(item, dict):
            continue
        slice_id = str(item.get("behavior_slice_id") or "").strip()
        if slice_id:
            scenarios_by_slice.setdefault(slice_id, []).append(item)
    templates: list[dict[str, Any]] = []
    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    for slice_item in selected_slices or []:
        if not isinstance(slice_item, dict):
            continue
        slice_id = str(slice_item.get("slice_id") or "").strip()
        if not slice_id:
            continue
        matching_scenarios = scenarios_by_slice.get(slice_id, [])
        scenario_steps = [step for item in matching_scenarios for step in item.get("steps", []) if isinstance(step, dict)]
        has_write_step = any(str(step.get("method") or "").strip().upper() in write_methods for step in scenario_steps)
        evidence_gaps = [
            str(gap).strip()
            for item in matching_scenarios
            for gap in item.get("evidence_gaps", [])
            if str(gap).strip()
        ]
        needs_fixture = any(gap in {"FIXTURE_CONTRACT_MISSING", "CLEANUP_CONTRACT_MISSING"} for gap in evidence_gaps)
        if not has_write_step and not needs_fixture:
            continue
        entity = str(slice_item.get("entity") or "entity").strip() or "entity"
        kind = str(slice_item.get("kind") or "source_bound").strip() or "source_bound"
        endpoints = [str(value).strip() for value in slice_item.get("endpoints", []) if str(value).strip()]
        title = next(
            (
                str(item.get("title") or "").strip()
                for item in matching_scenarios
                if str(item.get("title") or "").strip()
            ),
            f"Source-bound UI test data backfill: {entity}/{kind}",
        )
        templates.append(
            {
                "request_template_id": f"UITESTDATA_{slice_id}",
                "scan_id": scan_id,
                "campaign_id": str(campaign.get("campaign_id") or ""),
                "project_id": project,
                "title": title,
                "task": (
                    "Prepare disposable sandbox data through the approved UI flow so the selected source-bound slice "
                    f"{slice_id} becomes executable."
                ),
                "execution_mode": "approved_sandbox_write",
                "path": "/",
                "page_hints": [
                    hint
                    for hint in (
                        f"behavior slice: {slice_id}",
                        f"entity: {entity}",
                        f"slice kind: {kind}",
                        *(f"source endpoint: {path}" for path in endpoints[:3]),
                        *(f"evidence gap: {gap}" for gap in evidence_gaps[:3]),
                    )
                    if hint
                ],
                "browser_plan": {},
                "browser_plan_draft": _ui_test_data_browser_plan_draft(
                    entity=entity,
                    slice_id=slice_id,
                    scenario_steps=scenario_steps,
                    endpoints=endpoints,
                ),
                "metadata": {
                    "auto_generated": True,
                    "request_origin": "source_bound_ui_test_data_asset",
                    "bridge_mode": "page_agent_browser_plan",
                    "behavior_slice_id": slice_id,
                    "slice_kind": kind,
                    "followup_kind": "ui_test_data_backfill",
                    "executable": False,
                    "requires_explicit_browser_plan": True,
                },
                "review_contract": {
                    "status": "needs_selector_confirmation",
                    "required_confirmations": [
                        "confirmed_field_bindings",
                        "approved_browser_plan",
                        "approved_by",
                    ],
                    "promotion_target": "ui_test_data_requests",
                },
                "promotion": {
                    "status": "draft",
                    "approved_by": "",
                    "confirmed_field_bindings": [],
                    "approved_browser_plan": {},
                },
                "generated_at_utc": generated_at,
            }
        )
    return templates


def _ui_test_data_browser_plan_draft(
    *,
    entity: str,
    slice_id: str,
    scenario_steps: list[dict[str, Any]],
    endpoints: list[str],
) -> dict[str, Any]:
    def _field_tokens(name: str) -> list[str]:
        raw = str(name or "").strip()
        if not raw:
            return []
        spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw.replace("_", " ").replace("-", " "))
        parts = [part.strip() for part in spaced.split() if part.strip()]
        joined = " ".join(parts)
        tokens = [raw, raw.lower(), joined, joined.lower()]
        return list(dict.fromkeys(token for token in tokens if token))

    def _selector_candidates(name: str) -> list[str]:
        selectors: list[str] = []
        for token in _field_tokens(name):
            selectors.extend(
                [
                    f'[name="{token}"]',
                    f'[data-testid="{token}"]',
                    f'[data-field="{token}"]',
                    f'input[placeholder*="{token}"]',
                ]
            )
        return list(dict.fromkeys(selectors))[:8]

    def _ui_action_candidates(name: str, value: Any) -> list[dict[str, Any]]:
        field_type = type(value).__name__.lower()
        candidates = [{"action": "fill", "reason": "default text-like input"}]
        if isinstance(value, bool):
            candidates = [{"action": "check", "reason": "boolean field"}]
        elif isinstance(value, (int, float)):
            candidates = [{"action": "fill", "reason": "numeric field as text input"}]
        elif isinstance(value, list):
            candidates = [{"action": "select_option", "reason": "list-like value may map to multi-select"}]
        elif str(name).lower().endswith(("type", "status", "category", "role")):
            candidates = [
                {"action": "select_option", "reason": "field name suggests option selection"},
                {"action": "fill", "reason": "fallback if UI control is editable"},
            ]
        return candidates

    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    write_steps = [
        step
        for step in scenario_steps
        if isinstance(step, dict) and str(step.get("method") or "").strip().upper() in write_methods
    ]
    observation_steps = [
        step
        for step in scenario_steps
        if isinstance(step, dict) and str(step.get("method") or "").strip().upper() in {"GET", "HEAD", "OPTIONS"}
    ]
    draft_actions: list[dict[str, Any]] = []
    field_bindings_draft: list[dict[str, Any]] = []
    for order, step in enumerate(write_steps[:3], start=1):
        body = step.get("body") if isinstance(step.get("body"), dict) else {}
        body_field_hints = [str(key).strip() for key in body.keys() if str(key).strip()]
        field_bindings = [
            {
                "field": field,
                "value_hint": body.get(field),
                "selector_candidates": _selector_candidates(field),
                "ui_action_candidates": _ui_action_candidates(field, body.get(field)),
                "binding_status": "selector_mapping_needed",
            }
            for field in body_field_hints[:8]
        ]
        field_bindings_draft.extend(field_bindings)
        draft_actions.append(
            {
                "order": order,
                "intent": "execute_source_bound_write_via_ui",
                "source_api_method": str(step.get("method") or "").strip().upper(),
                "source_api_path": str(step.get("path") or "").strip(),
                "body_field_hints": body_field_hints[:8],
                "field_bindings": field_bindings,
                "selector_hints": [
                    f"Need a UI trigger that maps to {str(step.get('method') or '').strip().upper()} {str(step.get('path') or '').strip()}",
                    f"Need form bindings for entity {entity}",
                ],
                "missing_requirements": [
                    "UI_SELECTOR_BINDING_MISSING",
                    "FORM_FIELD_MAPPING_MISSING",
                ],
            }
        )
    if observation_steps:
        first_observation = observation_steps[0]
        draft_actions.append(
            {
                "order": len(draft_actions) + 1,
                "intent": "verify_created_object_via_ui_or_source_observation",
                "source_api_method": str(first_observation.get("method") or "").strip().upper(),
                "source_api_path": str(first_observation.get("path") or "").strip(),
                "selector_hints": [f"Need a UI locator to verify the created {entity} state"],
                "missing_requirements": ["UI_ASSERTION_SELECTOR_MISSING"],
            }
        )
    return {
        "execution_mode": "approved_sandbox_write",
        "write_approved": False,
        "steps": [
            {"action": "goto", "url": "/", "wait_until": "networkidle"},
            {"action": "wait_for_load", "state": "networkidle"},
            {"action": "screenshot", "full_page": True},
        ],
        "draft_actions": draft_actions,
        "field_bindings_draft": field_bindings_draft,
        "suggested_start_paths": list(dict.fromkeys(["/"] + [item for item in endpoints[:3] if item])),
        "missing_requirements": [
            "UI_SELECTOR_BINDING_MISSING",
            "FORM_FIELD_MAPPING_MISSING",
            "WRITE_PLAN_EXPLICIT_APPROVAL_REQUIRED",
        ],
        "slice_id": slice_id,
    }


def _ui_execution_evidence_summary(ui_execution: Any) -> dict[str, Any]:
    payload = _as_dict(ui_execution)
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    requested = int(payload.get("requested") or len(results) or 0)
    executed = int(payload.get("executed") or 0)
    failed = int(payload.get("failed") or 0)
    blocked = int(payload.get("blocked") or 0)
    provider_distribution = (
        {str(key): int(value or 0) for key, value in payload.get("provider_distribution", {}).items()}
        if isinstance(payload.get("provider_distribution"), dict)
        else {}
    )
    bridge_provider_distribution: dict[str, int] = {}
    created_data_count = 0
    evidence_captured_count = 0
    current_url_samples: list[str] = []
    artifact_refs: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        ref = str(artifact.get("ref") or "").strip()
        if ref and ref not in artifact_refs:
            artifact_refs.append(ref)
    for result in results:
        row = _as_dict(result)
        if not row:
            continue
        bridge_provider = str(row.get("bridge_provider") or row.get("provider") or "").strip()
        if bridge_provider:
            bridge_provider_distribution[bridge_provider] = bridge_provider_distribution.get(bridge_provider, 0) + 1
        if _as_dict(row.get("created_data")):
            created_data_count += 1
        current_url = str(row.get("current_url") or "").strip()
        if current_url and current_url not in current_url_samples and len(current_url_samples) < 3:
            current_url_samples.append(current_url)
        has_evidence = bool(
            isinstance(row.get("artifacts"), list) and row.get("artifacts")
            or isinstance(row.get("findings"), list) and row.get("findings")
            or _as_dict(row.get("created_data"))
            or isinstance(row.get("history"), list) and row.get("history")
            or isinstance(row.get("console"), list) and row.get("console")
            or isinstance(row.get("network"), list) and row.get("network")
        )
        if has_evidence:
            evidence_captured_count += 1
    status = str(payload.get("status") or ("not_requested" if requested <= 0 else "completed"))
    summary = (
        "UI execution not requested."
        if requested <= 0
        else (
            f"UI execution {status}: requested {requested}, executed {executed}, failed {failed}, "
            f"blocked {blocked}, evidence captured {evidence_captured_count}, artifacts {len(artifacts)}, "
            f"findings {len(findings)}"
            + (f", created data {created_data_count}" if created_data_count else "")
            + "."
        )
    )
    return {
        "status": status,
        "requested": requested,
        "executed": executed,
        "failed": failed,
        "blocked": blocked,
        "finding_count": len(findings),
        "artifact_count": len(artifacts),
        "created_data_count": created_data_count,
        "evidence_captured_count": evidence_captured_count,
        "provider_distribution": provider_distribution,
        "bridge_provider_distribution": bridge_provider_distribution,
        "artifact_refs": artifact_refs[:3],
        "current_url_samples": current_url_samples,
        "summary": summary,
    }


def _load_candidate_items(path: Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    return [dict(item) for item in items if isinstance(item, dict)]


def _merge_candidate_items(existing: list[dict[str, Any]], new_items: list[dict[str, Any]], *, key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in existing + new_items:
        if not isinstance(row, dict):
            continue
        key = "|".join(str(row.get(field) or "").strip().lower() for field in key_fields)
        if not key.strip("|"):
            key = _sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))[:16]
        current = merged.get(key)
        if current is None:
            merged[key] = dict(row)
            continue
        current_generated = str(current.get("generated_at_utc") or "")
        incoming_generated = str(row.get("generated_at_utc") or "")
        if incoming_generated >= current_generated:
            merged[key] = {**current, **dict(row)}
    return list(merged.values())


def _materialize_ui_followup_assets(
    *,
    project: str,
    root: Path,
    scan_id: str,
    campaign: dict[str, Any],
    items: list[dict[str, Any]],
    selected_slices: list[dict[str, Any]] | None = None,
    plan_only_scenarios: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    workspace_dir = root / "platform_workspace" / _safe_project(project) / "defect_discovery"
    output_dir = root / "platform_outputs" / _safe_project(project) / "defect_discovery"
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    intake_items: list[dict[str, Any]] = []
    regression_items: list[dict[str, Any]] = []
    execution_request_items: list[dict[str, Any]] = []
    test_data_request_items: list[dict[str, Any]] = []
    seen_intake: set[str] = set()
    seen_regression: set[str] = set()
    seen_execution_request: set[str] = set()
    for index, value in enumerate(items if isinstance(items, list) else [], start=1):
        if not isinstance(value, dict):
            continue
        title = str(value.get("title") or f"ui_high_confidence_candidate_{index}").strip() or f"ui_high_confidence_candidate_{index}"
        path = _ui_candidate_target_path(value)
        method = _ui_candidate_method(value)
        risk_type = str(value.get("risk_type") or value.get("defect_family") or "ui_execution").strip() or "ui_execution"
        severity = str(value.get("severity") or "P2").strip().upper()
        candidate_id = str(value.get("risk_id") or value.get("bug_id") or _sha256(f"{title}|{method}|{path}")[:16]).strip()
        intake_key = f"{title.lower()}|{method}|{path}|{severity}"
        if intake_key not in seen_intake:
            seen_intake.add(intake_key)
            intake_items.append({
                "intake_id": f"UIINTAKE_{candidate_id}",
                "scan_id": scan_id,
                "campaign_id": str(campaign.get("campaign_id") or ""),
                "project_id": project,
                "title": title,
                "severity": severity if severity in {"P0", "P1", "P2", "P3"} else "P2",
                "risk_type": risk_type,
                "candidate_tier": str(value.get("candidate_tier") or "high_confidence_ui_candidate"),
                "verification_badge": str(value.get("verification_badge") or "ui_verified"),
                "verification_status": str((_as_dict(value.get("ui_verification"))).get("status") or ""),
                "confidence_score": float(value.get("confidence_score") or value.get("confidence") or 0.0),
                "path": path,
                "method": method,
                "source": "ui_high_confidence_candidate",
                "defect_intake_route": "internal_defect_intake",
                "defect_intake_priority": "P1" if severity != "P0" else "P0",
                "defect_intake_reason": "UI 高可信候选已完成二次验真，建议进入内部缺陷入账或人工复核队列。",
                "reproduction_steps": list(value.get("reproduction_steps") or []),
                "evidence_quality": dict(value.get("evidence_quality") or {}),
            })
        regression_key = f"{method}|{path}|{risk_type}|{title.lower()}"
        if regression_key not in seen_regression:
            seen_regression.add(regression_key)
            regression_items.append({
                "regression_probe_id": f"UIREG_{candidate_id}",
                "issue_id": f"UIINTAKE_{candidate_id}",
                "title": title,
                "risk_type": risk_type,
                "severity": severity if severity in {"P0", "P1", "P2", "P3"} else "P2",
                "method": method,
                "path": path or "/",
                "actor": "ui_dom_agent",
                "expected": str(value.get("expected") or "原 UI 高可信缺陷信号不应再次出现，相关页面与业务状态应保持一致。"),
                "source": "ui_high_confidence_candidate",
                "candidate_tier": str(value.get("candidate_tier") or "high_confidence_ui_candidate"),
                "high_confidence_candidate": bool(value.get("high_confidence_candidate") is True),
                "verification_badge": str(value.get("verification_badge") or "ui_verified"),
                "verification_status": str((_as_dict(value.get("ui_verification"))).get("status") or ""),
                "confidence_score": float(value.get("confidence_score") or value.get("confidence") or 0.0),
                "evidence_quality": dict(value.get("evidence_quality") or {}),
            })
        execution_request_key = f"{title.lower()}|{method}|{path}|{risk_type}"
        if execution_request_key not in seen_execution_request:
            seen_execution_request.add(execution_request_key)
            execution_request_items.append(
                _ui_followup_execution_template(
                    value,
                    project=project,
                    scan_id=scan_id,
                    campaign=campaign,
                    generated_at=generated_at,
                    index=index,
                )
            )
    execution_request_items.extend(
        _source_bound_ui_followup_templates(
            project=project,
            scan_id=scan_id,
            campaign=campaign,
            generated_at=generated_at,
            selected_slices=selected_slices,
            plan_only_scenarios=plan_only_scenarios,
        )
    )
    test_data_request_items.extend(
        _source_bound_ui_test_data_templates(
            project=project,
            scan_id=scan_id,
            campaign=campaign,
            generated_at=generated_at,
            selected_slices=selected_slices,
            plan_only_scenarios=plan_only_scenarios,
        )
    )
    intake_payload = {
        "version": "ui_high_confidence_defect_intake_candidates_v1",
        "project_id": project,
        "scan_id": scan_id,
        "campaign_id": str(campaign.get("campaign_id") or ""),
        "generated_at_utc": generated_at,
        "items": [],
    }
    regression_payload = {
        "version": "ui_high_confidence_regression_candidates_v1",
        "project_id": project,
        "scan_id": scan_id,
        "campaign_id": str(campaign.get("campaign_id") or ""),
        "generated_at_utc": generated_at,
        "items": [],
    }
    execution_request_payload = {
        "version": "ui_followup_execution_requests_v1",
        "project_id": project,
        "scan_id": scan_id,
        "campaign_id": str(campaign.get("campaign_id") or ""),
        "generated_at_utc": generated_at,
        "items": [],
    }
    test_data_request_payload = {
        "version": "ui_followup_test_data_requests_v1",
        "project_id": project,
        "scan_id": scan_id,
        "campaign_id": str(campaign.get("campaign_id") or ""),
        "generated_at_utc": generated_at,
        "items": [],
    }
    intake_path = workspace_dir / "internal_defect_intake_candidates.json"
    intake_existing = _load_candidate_items(intake_path)
    intake_payload["items"] = _merge_candidate_items(
        intake_existing,
        [{**item, "generated_at_utc": generated_at} for item in intake_items],
        key_fields=("title", "method", "path", "severity", "risk_type"),
    )
    regression_path = workspace_dir / "ui_high_confidence_regression_candidates.json"
    regression_existing = _load_candidate_items(regression_path)
    regression_payload["items"] = _merge_candidate_items(
        regression_existing,
        [{**item, "generated_at_utc": generated_at, "approved": bool(item.get("approved"))} for item in regression_items],
        key_fields=("title", "method", "path", "severity", "risk_type"),
    )
    execution_request_path = workspace_dir / "ui_followup_execution_requests.json"
    execution_request_existing = _load_candidate_items(execution_request_path)
    execution_request_payload["items"] = _merge_candidate_items(
        execution_request_existing,
        execution_request_items,
        key_fields=("title", "method", "path", "severity", "risk_type"),
    )
    test_data_request_path = workspace_dir / "ui_followup_test_data_requests.json"
    test_data_request_existing = _load_candidate_items(test_data_request_path)
    test_data_request_payload["items"] = _merge_candidate_items(
        test_data_request_existing,
        test_data_request_items,
        key_fields=("title", "execution_mode", "path"),
    )
    if intake_items or intake_existing:
        _write_json(intake_path, intake_payload)
        _write_json(output_dir / "internal_defect_intake_candidates.json", intake_payload)
    if regression_items or regression_existing:
        _write_json(regression_path, regression_payload)
        _write_json(output_dir / "ui_high_confidence_regression_candidates.json", regression_payload)
    if execution_request_items or execution_request_existing:
        _write_json(execution_request_path, execution_request_payload)
        _write_json(output_dir / "ui_followup_execution_requests.json", execution_request_payload)
    if test_data_request_items or test_data_request_existing:
        _write_json(test_data_request_path, test_data_request_payload)
        _write_json(output_dir / "ui_followup_test_data_requests.json", test_data_request_payload)
    return {
        "status": (
            "materialized"
            if intake_items or regression_items or execution_request_items or test_data_request_items
            else (
                "preserved"
                if intake_existing or regression_existing or execution_request_existing or test_data_request_existing
                else "empty"
            )
        ),
        "generated_at_utc": generated_at,
        "defect_intake_candidate_count": len(intake_payload["items"]),
        "regression_candidate_count": len(regression_payload["items"]),
        "execution_request_count": len(execution_request_payload["items"]),
        "test_data_request_count": len(test_data_request_payload["items"]),
        "defect_intake_candidates_ref": f"platform_workspace/{_safe_project(project)}/defect_discovery/internal_defect_intake_candidates.json",
        "regression_candidates_ref": f"platform_workspace/{_safe_project(project)}/defect_discovery/ui_high_confidence_regression_candidates.json",
        "execution_requests_ref": f"platform_workspace/{_safe_project(project)}/defect_discovery/ui_followup_execution_requests.json",
        "test_data_requests_ref": f"platform_workspace/{_safe_project(project)}/defect_discovery/ui_followup_test_data_requests.json",
    }



from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import re

from .defect_signal_schema import normalize_defect_signal
from .real_project_onboarding import ROOT, config_paths


@dataclass(frozen=True)
class BrowserUIReplayConfig:
    project_id: str
    base_url: str
    execute_browser: bool
    browser: str
    headless: bool
    max_pages: int


def _candidate_task_journey_manifest_paths(
    project_id: str,
    cfg: dict[str, Any],
    root: Path | None,
) -> list[Path]:
    root = root or ROOT
    paths = config_paths(project_id, root)
    candidates: list[Path] = []
    configured_manifest = str(cfg.get("frontend_task_journeys_manifest") or "").strip()
    configured_dir = str(cfg.get("frontend_project_routes_dir") or "").strip()
    if configured_manifest:
        candidates.append(Path(configured_manifest))
    if configured_dir:
        candidates.append(Path(configured_dir) / "frontend_task_journeys_manifest.json")
    candidates.extend(
        [
            paths["output_dir"] / "frontend_task_journeys_manifest.json",
            root / "platform_outputs" / project_id / "frontend_task_journeys_manifest.json",
            root / "frontend_task_journeys_manifest.json",
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _load_frontend_task_journeys(
    project_id: str,
    cfg: dict[str, Any],
    root: Path | None = None,
) -> list[dict[str, Any]]:
    for manifest_path in _candidate_task_journey_manifest_paths(project_id, cfg, root):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        journeys = payload.get("journeys") if isinstance(payload, dict) else None
        if not isinstance(journeys, list):
            continue
        normalized = [item for item in journeys if isinstance(item, dict)]
        if normalized:
            return normalized
    return []


def _candidate_ui_design_oracle_paths(
    project_id: str,
    cfg: dict[str, Any],
    root: Path | None,
) -> list[Path]:
    root = root or ROOT
    paths = config_paths(project_id, root)
    candidates: list[Path] = []
    configured_manifest = str(cfg.get("ui_design_oracle_manifest") or "").strip()
    configured_dir = str(cfg.get("frontend_project_routes_dir") or "").strip()
    if configured_manifest:
        candidates.append(Path(configured_manifest))
    if configured_dir:
        candidates.append(Path(configured_dir) / "ui_design_oracle_manifest.json")
    candidates.extend(
        [
            paths["output_dir"] / "ui_design_oracle_manifest.json",
            root / "platform_outputs" / project_id / "ui_design_oracle_manifest.json",
            root / "ui_design_oracle_manifest.json",
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _load_ui_design_oracle(
    project_id: str,
    cfg: dict[str, Any],
    root: Path | None = None,
) -> dict[str, Any]:
    for manifest_path in _candidate_ui_design_oracle_paths(project_id, cfg, root):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        screens = payload.get("screens")
        journeys = payload.get("journeys")
        if isinstance(screens, list) and isinstance(journeys, list):
            return payload
    return {}


def _route_signature_from_url(url: str) -> str:
    try:
        path = urlparse(url).path or ""
    except Exception:
        path = ""
    if path.startswith("/projects/") and len(path.split("/")) >= 3:
        return "/projects/:projectId"
    if path == "/projects" or path.startswith("/projects?"):
        return "/projects"
    if path.startswith("/projects"):
        return "/projects"
    return "/"


def _text_corpus(page: dict[str, Any], elements: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    if isinstance(page.get("title"), str):
        chunks.append(page.get("title") or "")
    frags = page.get("text_fragments") if isinstance(page.get("text_fragments"), list) else []
    for frag in frags[:80]:
        if isinstance(frag, str):
            chunks.append(frag)
        elif isinstance(frag, dict):
            value = frag.get("text")
            if isinstance(value, str):
                chunks.append(value)
    for element in elements[:200]:
        if not isinstance(element, dict):
            continue
        for key in (
            "text",
            "label",
            "name",
            "role",
            "selector",
            "aria_label",
            "ariaLabel",
            "semantic_name",
            "element_id",
            "id",
            "href",
            "placeholder",
            "tag",
            "type",
            "css",
            "css_candidates",
            "xpath_candidate",
        ):
            value = element.get(key)
            if isinstance(value, str) and value.strip():
                chunks.append(value)
            elif isinstance(value, list):
                for item in value[:20]:
                    if isinstance(item, str) and item.strip():
                        chunks.append(item)
    return " ".join(chunks)


def _expected_tokens() -> dict[str, list[str]]:
    return {
        "topbar": ["顶部状态区", "运行模式", "后端状态", "QualiBug"],
        "project_switcher": ["当前项目切换", "ProjectSwitcher", "设为当前项目"],
        "project_card_list": ["项目列表", "进入项目详情", "继续当前旅程"],
        "create_project_button": ["创建项目草案"],
        "workspace_state_gate": ["统一加载态", "统一失败态", "统一空态", "统一离线态"],
        "project_summary": ["项目详情", "项目 ID", "上线建议"],
        "project_route_guard": ["项目级状态缓存准备中", "返回项目列表"],
        "project_scoped_api_paths": ["/api/v1/projects/{projectId}", "项目级 API 请求"],
        "command_center_entry": ["质量驾驶舱", "command-center"],
        "risk_evidence_entry": ["风险证据链", "/risks"],
        "loading_indicator": ["正在加载", "加载", "loading"],
        "empty_state_message": ["暂无", "空态", "暂无可用项目"],
        "error_feedback": ["失败", "error", "加载失败"],
        "current_project_visible": ["当前项目", "项目 ID"],
        "navigation_entry_visible": ["进入", "返回项目列表"],
        "navigation_success": ["http", "/projects"],
        "selected_project_persisted": ["qualibug.selectedProjectId", "selectedProjectId"],
        "project_context_updated": ["当前项目", "ProjectSwitcher"],
    }


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _element_value(element: dict[str, Any], key: str) -> str:
    value = element.get(key)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks = [str(item) for item in value[:20] if str(item).strip()]
        return " ".join(chunks)
    return ""


def _element_role(element: dict[str, Any]) -> str:
    return _normalize(_element_value(element, "role") or _element_value(element, "aria_role") or _element_value(element, "ariaRole"))


def _element_text_blob(element: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "text",
        "label",
        "name",
        "aria_label",
        "ariaLabel",
        "placeholder",
        "selector",
        "css",
        "css_candidates",
        "xpath_candidate",
        "semantic_name",
        "element_id",
        "href",
        "id",
        "tag",
        "testid",
        "data_testid",
        "data-testid",
    ):
        value = _element_value(element, key)
        if value:
            parts.append(value)
    return _normalize(" ".join(parts))


def _extract_data_testids(text: str) -> list[str]:
    if not text:
        return []
    hits: list[str] = []
    for match in re.finditer(r"data-testid\s*=\s*['\"]([^'\"]+)['\"]", text, flags=re.IGNORECASE):
        value = (match.group(1) or "").strip()
        if value:
            hits.append(value)
    for match in re.finditer(r"\[data-testid\s*=\s*['\"]([^'\"]+)['\"]\]", text, flags=re.IGNORECASE):
        value = (match.group(1) or "").strip()
        if value:
            hits.append(value)
    return hits


def _element_testid_candidates(element: dict[str, Any]) -> set[str]:
    candidates: set[str] = set()
    for key in ("testid", "data_testid", "data-testid"):
        value = _element_value(element, key)
        if value.strip():
            candidates.add(value.strip().lower())
    selector = _element_value(element, "selector")
    for tid in _extract_data_testids(selector):
        candidates.add(tid.strip().lower())
    for key in ("css", "css_candidates"):
        value = _element_value(element, key)
        for tid in _extract_data_testids(value):
            candidates.add(tid.strip().lower())
    return candidates


def _oracle_match_hints(oracle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    hints = oracle.get("match_hints") if isinstance(oracle.get("match_hints"), dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in hints.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        out[key] = value
    return out


def _oracle_hint_list(hints: dict[str, dict[str, Any]], item_id: str, key: str) -> list[str]:
    hint = hints.get(item_id)
    if not isinstance(hint, dict):
        return []
    value = hint.get(key)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _oracle_confidence(match_hints: dict[str, dict[str, Any]], item_id: str, *, base: float, elements: list[dict[str, Any]]) -> tuple[float, str]:
    testids = _oracle_hint_list(match_hints, item_id, "testids")
    keywords = _oracle_hint_list(match_hints, item_id, "keywords")
    roles = _oracle_hint_list(match_hints, item_id, "roles")
    if testids and elements:
        return min(0.82, max(base, 0.76)), "testid_role"
    if testids:
        return min(0.78, max(base, 0.72)), "testid"
    if keywords and elements:
        return min(0.72, max(base, 0.66)), "keyword_role"
    if keywords:
        return min(0.68, max(base, 0.62)), "keyword"
    if roles and elements:
        return min(0.66, max(base, 0.6)), "role_only"
    return min(0.62, base), "token_fallback"


def _minimize_missing_items(match_hints: dict[str, dict[str, Any]], items: list[str], *, elements: list[dict[str, Any]]) -> list[str]:
    order = {
        "testid_role": 6,
        "testid": 5,
        "keyword_role": 4,
        "keyword": 3,
        "role_only": 2,
        "token_fallback": 1,
    }
    ranked: list[tuple[int, float, str]] = []
    for item_id in items:
        confidence, basis = _oracle_confidence(match_hints, item_id, base=0.6, elements=elements)
        ranked.append((order.get(basis, 0), confidence, item_id))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [item_id for _, _, item_id in ranked[:2]]


def _structured_match(item_id: str, elements: list[dict[str, Any]], hints: dict[str, dict[str, Any]]) -> bool:
    rules = hints.get(item_id)
    if not isinstance(rules, dict) or not rules:
        return False
    roles = {str(r).lower() for r in (rules.get("roles") or []) if str(r).strip()}
    keywords = [str(k).lower() for k in (rules.get("keywords") or []) if str(k).strip()]
    testids = [str(k).lower() for k in (rules.get("testids") or []) if str(k).strip()]
    for element in elements:
        if not isinstance(element, dict):
            continue
        role = _element_role(element)
        blob = _element_text_blob(element)
        if roles and role and role not in roles:
            continue
        if testids:
            candidates = _element_testid_candidates(element)
            if candidates and any(tid in candidates for tid in testids):
                return True
            if any(tid in blob for tid in testids):
                return True
        if keywords and any(kw in blob for kw in keywords):
            return True
    return False


def _oracle_item_present(
    item_id: str,
    corpus: str,
    elements: list[dict[str, Any]],
    *,
    match_hints: dict[str, dict[str, Any]],
    tokens_fallback: dict[str, list[str]],
    oracle_tokens: dict[str, list[str]],
) -> bool:
    if _structured_match(item_id, elements, match_hints):
        return True
    expected = oracle_tokens.get(item_id) or tokens_fallback.get(item_id) or []
    return bool(expected and any(str(t).lower() in corpus for t in expected))


def _design_issue_family(screen_id: str, component_id: str) -> str:
    if component_id in {"project_summary", "project_card_list", "project_scoped_api_paths"}:
        return "ui"
    if screen_id == "project_detail" and component_id in {"project_route_guard"}:
        return "ui"
    return "uiux"


def _append_design_oracle_issues(
    issues: list[dict[str, Any]],
    *,
    project_id: str,
    route_signature: str,
    oracle: dict[str, Any],
    page: dict[str, Any],
    elements: list[dict[str, Any]],
    dedupe_keys: set[tuple[str, str, str]] | None = None,
) -> None:
    screens = oracle.get("screens") if isinstance(oracle.get("screens"), list) else []
    screen = next((item for item in screens if isinstance(item, dict) and str(item.get("route") or "") == route_signature), None)
    if not isinstance(screen, dict):
        return
    screen_id = str(screen.get("screen_id") or "unknown")
    corpus = _text_corpus(page, elements).lower()
    tokens_fallback = _expected_tokens()
    match_hints = _oracle_match_hints(oracle)
    oracle_tokens: dict[str, list[str]] = {}
    for key, hint in match_hints.items():
        if not isinstance(hint, dict):
            continue
        token_list = hint.get("tokens")
        if isinstance(token_list, list):
            oracle_tokens[str(key)] = [str(item) for item in token_list if str(item).strip()]
    dedupe_keys = dedupe_keys if isinstance(dedupe_keys, set) else set()
    missing_components: list[str] = []
    for component in screen.get("expected_components") or []:
        component_id = str(component)
        if _oracle_item_present(
            component_id,
            corpus,
            elements,
            match_hints=match_hints,
            tokens_fallback=tokens_fallback,
            oracle_tokens=oracle_tokens,
        ):
            continue
        if oracle_tokens.get(component_id) or tokens_fallback.get(component_id):
            missing_components.append(component_id)
    for component_id in missing_components[:3]:
        dedupe_key = ("component", screen_id, component_id)
        if dedupe_key in dedupe_keys:
            continue
        dedupe_keys.add(dedupe_key)
        confidence, confidence_basis = _oracle_confidence(match_hints, component_id, base=0.66, elements=elements)
        evidence = {
            "route_signature": route_signature,
            "screen_id": screen_id,
            "missing_component": component_id,
            "expected_tokens": oracle_tokens.get(component_id) or tokens_fallback.get(component_id, []),
            "match_hints": {
                "roles": _oracle_hint_list(match_hints, component_id, "roles"),
                "testids": _oracle_hint_list(match_hints, component_id, "testids"),
                "keywords": _oracle_hint_list(match_hints, component_id, "keywords"),
            },
            "confidence_basis": confidence_basis,
            "element_count": len(elements),
            "page": _summarize_page(page),
            "element_sample": elements[:12],
        }
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_UI_DESIGN_{len(issues)+1:04d}",
                    "title": f"设计预期缺失关键组件：{component_id}",
                    "defect_family": _design_issue_family(screen_id, component_id),
                    "risk_type": "ui_design_oracle",
                    "severity": "P2",
                    "confidence": confidence,
                    "status": "needs_human_review",
                    "source": "ui_design_oracle",
                    "route": route_signature,
                    "path": route_signature,
                    "expected": "页面应包含设计预期的关键组件与信息架构",
                    "actual": f"未识别到组件语义 {component_id}",
                    "evidence": evidence,
                },
                signal_kind="issue",
                default_source="ui_design_oracle",
            )
        )
    missing_feedback: list[str] = []
    for feedback in screen.get("required_feedback") or []:
        feedback_id = str(feedback)
        if _oracle_item_present(
            feedback_id,
            corpus,
            elements,
            match_hints=match_hints,
            tokens_fallback=tokens_fallback,
            oracle_tokens=oracle_tokens,
        ):
            continue
        if oracle_tokens.get(feedback_id) or tokens_fallback.get(feedback_id):
            missing_feedback.append(feedback_id)
    for feedback_id in missing_feedback[:2]:
        dedupe_key = ("feedback", screen_id, feedback_id)
        if dedupe_key in dedupe_keys:
            continue
        dedupe_keys.add(dedupe_key)
        confidence, confidence_basis = _oracle_confidence(match_hints, feedback_id, base=0.55, elements=elements)
        evidence = {
            "route_signature": route_signature,
            "screen_id": screen_id,
            "missing_feedback": feedback_id,
            "expected_tokens": oracle_tokens.get(feedback_id) or tokens_fallback.get(feedback_id, []),
            "match_hints": {
                "roles": _oracle_hint_list(match_hints, feedback_id, "roles"),
                "testids": _oracle_hint_list(match_hints, feedback_id, "testids"),
                "keywords": _oracle_hint_list(match_hints, feedback_id, "keywords"),
            },
            "confidence_basis": confidence_basis,
            "element_count": len(elements),
            "page": _summarize_page(page),
            "element_sample": elements[:12],
        }
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_UI_DESIGN_{len(issues)+1:04d}",
                    "title": f"设计预期反馈缺失：{feedback_id}",
                    "defect_family": "uiux",
                    "risk_type": "ui_design_oracle",
                    "severity": "P3",
                    "confidence": confidence,
                    "status": "needs_human_review",
                    "source": "ui_design_oracle",
                    "route": route_signature,
                    "path": route_signature,
                    "expected": "页面应呈现设计预期的统一状态反馈",
                    "actual": f"未识别到反馈语义 {feedback_id}",
                    "evidence": evidence,
                },
                signal_kind="issue",
                default_source="ui_design_oracle",
            )
        )
    journeys = oracle.get("journeys") if isinstance(oracle.get("journeys"), list) else []
    for journey in journeys:
        if not isinstance(journey, dict):
            continue
        journey_id = str(journey.get("journey_id") or "")
        entry_route = str(journey.get("entry_route") or "")
        if not journey_id or entry_route != route_signature:
            continue
        dedupe_key = ("journey", journey_id, "missing")
        if dedupe_key in dedupe_keys:
            continue
        required_components = [str(item) for item in (journey.get("required_components") or []) if str(item).strip()]
        expected_feedback = [str(item) for item in (journey.get("expected_feedback") or []) if str(item).strip()]
        missing_required_components: list[str] = []
        missing_expected_feedback: list[str] = []
        for component_id in required_components:
            if _oracle_item_present(
                component_id,
                corpus,
                elements,
                match_hints=match_hints,
                tokens_fallback=tokens_fallback,
                oracle_tokens=oracle_tokens,
            ):
                continue
            if oracle_tokens.get(component_id) or tokens_fallback.get(component_id):
                missing_required_components.append(component_id)
        for feedback_id in expected_feedback:
            if _oracle_item_present(
                feedback_id,
                corpus,
                elements,
                match_hints=match_hints,
                tokens_fallback=tokens_fallback,
                oracle_tokens=oracle_tokens,
            ):
                continue
            if oracle_tokens.get(feedback_id) or tokens_fallback.get(feedback_id):
                missing_expected_feedback.append(feedback_id)
        if not (missing_required_components or missing_expected_feedback):
            continue
        dedupe_keys.add(dedupe_key)
        minimal_components = _minimize_missing_items(match_hints, missing_required_components, elements=elements)
        minimal_feedback = _minimize_missing_items(match_hints, missing_expected_feedback, elements=elements)
        focus_item = (minimal_components + minimal_feedback + missing_required_components + missing_expected_feedback)[0]
        confidence, confidence_basis = _oracle_confidence(match_hints, focus_item, base=0.6, elements=elements)
        evidence = {
            "route_signature": route_signature,
            "screen_id": screen_id,
            "journey_id": journey_id,
            "journey_title": str(journey.get("title") or journey_id),
            "entry_route": entry_route,
            "missing_required_components": missing_required_components[:5],
            "missing_expected_feedback": missing_expected_feedback[:5],
            "minimal_missing_required_components": minimal_components,
            "minimal_missing_expected_feedback": minimal_feedback,
            "match_hints": {
                "roles": _oracle_hint_list(match_hints, focus_item, "roles"),
                "testids": _oracle_hint_list(match_hints, focus_item, "testids"),
                "keywords": _oracle_hint_list(match_hints, focus_item, "keywords"),
            },
            "confidence_basis": confidence_basis,
            "element_count": len(elements),
            "page": _summarize_page(page),
            "element_sample": elements[:12],
        }
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_UI_DESIGN_{len(issues)+1:04d}",
                    "title": f"设计旅程预期缺失：{journey_id}",
                    "defect_family": str(journey.get("defect_family") or "uiux"),
                    "risk_type": "ui_design_oracle",
                    "severity": "P2",
                    "confidence": confidence,
                    "status": "needs_human_review",
                    "source": "ui_design_oracle",
                    "route": route_signature,
                    "path": route_signature,
                    "expected": "页面应满足设计旅程的关键组件与反馈闭环",
                    "actual": f"缺少组件={','.join(missing_required_components[:3]) or 'none'}; 缺少反馈={','.join(missing_expected_feedback[:3]) or 'none'}",
                    "evidence": evidence,
                },
                signal_kind="issue",
                default_source="ui_design_oracle",
            )
        )


def generate_browser_ui_replay_probes(
    openapi: dict[str, Any],
    cfg: dict[str, Any],
    project_id: str,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    del openapi
    execute_browser = bool(cfg.get("execute_browser_ui"))
    browser = str(cfg.get("browser_ui_browser") or "chromium")
    headless = bool(cfg.get("browser_ui_headless", True))
    max_pages = int(cfg.get("browser_ui_max_pages") or 8)
    probes = [
        normalize_defect_signal(
            {
                "probe_id": "BROWSER_UI_0001",
                "title": "真实浏览器 UI 探勘与断链检查",
                "defect_family": "ui",
                "risk_type": "browser_ui_replay",
                "severity": "P2",
                "source": "browser_ui_replay",
                "method": "GET",
                "path": "/",
                "expected": "真实浏览器可打开系统入口，页面可渲染且存在可用导航与关键元素",
                "actual": "待验证 UI 渲染、导航闭环与关键交互元素可达性",
                "status": "planned_probe",
                "confidence": 0.35,
                "evidence": {
                    "project_id": project_id,
                    "execute_browser_ui": execute_browser,
                    "browser": browser,
                    "headless": headless,
                    "max_pages": max_pages,
                },
            },
            signal_kind="probe",
            default_source="browser_ui_replay",
            default_status="planned_probe",
            default_confidence=0.35,
        )
    ]
    journeys = _load_frontend_task_journeys(project_id, cfg, root)
    for index, journey in enumerate(journeys, start=1):
        entry_route = str(journey.get("entry_route") or "/")
        defect_family = str(journey.get("defect_family") or "uiux")
        title = str(journey.get("title") or journey.get("journey_id") or f"任务流 {index}")
        journey_id = str(journey.get("journey_id") or f"journey_{index}")
        success_signals = [str(item) for item in (journey.get("success_signals") or []) if str(item).strip()]
        steps = [str(item) for item in (journey.get("steps") or []) if str(item).strip()]
        probes.append(
            normalize_defect_signal(
                {
                    "probe_id": f"BROWSER_UI_JOURNEY_{index:04d}",
                    "title": f"前端任务流探勘：{title}",
                    "defect_family": defect_family,
                    "risk_type": "frontend_task_journey",
                    "severity": "P2",
                    "source": "frontend_task_journey",
                    "method": "GET",
                    "route": entry_route,
                    "path": entry_route,
                    "expected": "前端任务流入口应可访问，且关键步骤与成功信号能够成立：" + ", ".join(success_signals[:4]),
                    "actual": "待验证前端任务流步骤：" + ", ".join(steps[:4]),
                    "status": "planned_probe",
                    "confidence": 0.4,
                    "evidence": {
                        "project_id": project_id,
                        "execute_browser_ui": execute_browser,
                        "browser": browser,
                        "headless": headless,
                        "max_pages": max_pages,
                        "journey_id": journey_id,
                        "journey_title": title,
                        "required_project_context": bool(journey.get("required_project_context")),
                        "journey_steps": steps,
                        "success_signals": success_signals,
                        "failure_signals": [str(item) for item in (journey.get("failure_signals") or []) if str(item).strip()],
                        "risk_tags": [str(item) for item in (journey.get("risk_tags") or []) if str(item).strip()],
                    },
                },
                signal_kind="probe",
                default_source="frontend_task_journey",
                default_status="planned_probe",
                default_confidence=0.4,
            )
        )
    return probes


def _build_replay_config(cfg: dict[str, Any], project_id: str) -> BrowserUIReplayConfig:
    return BrowserUIReplayConfig(
        project_id=project_id,
        base_url=str(cfg.get("ui_base_url") or cfg.get("base_url") or ""),
        execute_browser=bool(cfg.get("execute_browser_ui")),
        browser=str(cfg.get("browser_ui_browser") or "chromium"),
        headless=bool(cfg.get("browser_ui_headless", True)),
        max_pages=int(cfg.get("browser_ui_max_pages") or 8),
    )


def _output_dirs(project_id: str, root: Path | None = None) -> tuple[Path, Path]:
    root = root or ROOT
    paths = config_paths(project_id, root)
    project_output_root = root / "platform_outputs" / project_id
    workspace = project_output_root / "browser_ui_replay"
    output = project_output_root / "browser_ui_replay"
    if paths["output_dir"].exists():
        workspace = paths["output_dir"] / "browser_ui_replay"
        output = paths["output_dir"] / "browser_ui_replay"
    return workspace, output


def _summarize_page(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": page.get("url"),
        "title": page.get("title"),
        "link_count": len(page.get("links") or []) if isinstance(page.get("links"), list) else 0,
        "text_fragment_count": len(page.get("text_fragments") or []) if isinstance(page.get("text_fragments"), list) else 0,
        "screenshot": page.get("screenshot"),
    }


def _apply_playwright_offline_env(cfg: dict[str, Any]) -> None:
    browsers_path = str(cfg.get("playwright_browsers_path") or cfg.get("browser_binaries_path") or "").strip()
    if browsers_path:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path
        os.environ.setdefault("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")


def browser_ui_capability_health(
    *,
    cfg: dict[str, Any] | None,
    project_id: str,
    root: Path | None = None,
) -> dict[str, Any]:
    cfg = cfg if isinstance(cfg, dict) else {}
    execute = bool(cfg.get("execute_browser_ui"))
    _apply_playwright_offline_env(cfg)
    browsers_path = str(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "")
    root = root or ROOT
    paths = config_paths(project_id, root)
    bundle_default = str((paths["output_dir"] if paths["output_dir"].exists() else (root / "platform_outputs" / project_id)) / "browsers" / "ms-playwright")
    if not browsers_path and Path(bundle_default).exists():
        browsers_path = bundle_default
    result = {
        "enabled": execute,
        "playwright_importable": False,
        "browsers_path": browsers_path,
        "browsers_present": False,
        "chromium_present": False,
        "firefox_present": False,
        "reason_code": "",
        "severity": "",
        "action": "",
        "reason": "",
    }
    if not execute:
        result["reason_code"] = "E_BROWSER_UI_DISABLED"
        result["severity"] = "info"
        result["reason"] = "execute_browser_ui disabled"
        result["action"] = "在 real_project_config.json 设置 execute_browser_ui=true，然后按离线 bundle 安装 Playwright 与浏览器内核。"
        return result
    try:
        import playwright  # type: ignore
        result["playwright_importable"] = True
    except Exception as exc:
        result["reason_code"] = "E_PLAYWRIGHT_MISSING"
        result["severity"] = "error"
        result["reason"] = f"playwright not available: {exc}"
        result["action"] = "使用 playwright-offline-install 安装到 venv，并配置 playwright_browsers_path/PLAYWRIGHT_BROWSERS_PATH 指向 ms-playwright。"
        return result
    if browsers_path and Path(browsers_path).exists():
        result["browsers_present"] = True
        subdirs = [p.name for p in Path(browsers_path).iterdir()] if Path(browsers_path).is_dir() else []
        result["chromium_present"] = any(name.startswith("chromium-") for name in subdirs)
        result["firefox_present"] = any(name.startswith("firefox-") for name in subdirs)
        result["browsers_present"] = result["chromium_present"] or result["firefox_present"] or bool(subdirs)
    if not result["browsers_present"]:
        result["reason_code"] = "E_BROWSER_CACHE_MISSING"
        result["severity"] = "error"
        result["reason"] = "browser cache not found; set playwright_browsers_path or PLAYWRIGHT_BROWSERS_PATH"
        result["action"] = "将离线 bundle 的 browsers/ms-playwright 目录路径写入 playwright_browsers_path 或环境变量 PLAYWRIGHT_BROWSERS_PATH。"
        return result
    if not (result["chromium_present"] and result["firefox_present"]):
        result["reason_code"] = "E_BROWSER_CACHE_PARTIAL"
        result["severity"] = "warn"
        missing = []
        if not result["chromium_present"]:
            missing.append("chromium")
        if not result["firefox_present"]:
            missing.append("firefox")
        result["reason"] = f"browser cache partial: missing {','.join(missing)}"
        result["action"] = "在打包机重新执行 playwright-offline-build --browsers chromium,firefox 并重新分发离线 bundle。"
        return result
    result["reason_code"] = "OK"
    result["severity"] = "ok"
    result["action"] = "浏览器能力就绪。可启用 execute_browser_ui 执行真实浏览器 UI 探勘与回放。"
    return result


def collect_browser_ui_replay_issues(
    project_id: str,
    root: Path | None = None,
    *,
    cfg: dict[str, Any] | None = None,
    scenario: str = "",
) -> list[dict[str, Any]]:
    cfg = cfg if isinstance(cfg, dict) else {}
    _apply_playwright_offline_env(cfg)
    replay = _build_replay_config(cfg, project_id)
    workspace, output = _output_dirs(project_id, root)
    issues: list[dict[str, Any]] = []
    try:
        from aitestops.ui_journey_tester import BrowserExplorer, UIJourneyConfig  # type: ignore
    except Exception as exc:
        if replay.execute_browser:
            issues.append(
                normalize_defect_signal(
                    {
                        "issue_id": f"ISSUE_BROWSER_UI_{len(issues)+1:04d}",
                        "title": "真实浏览器探勘能力不可用",
                        "defect_family": "ui",
                        "risk_type": "browser_ui_replay",
                        "severity": "P2",
                        "confidence": 0.55,
                        "status": "needs_human_review",
                        "source": "browser_ui_replay",
                        "route": "/",
                        "path": "/",
                        "expected": "启用 execute_browser_ui 后应具备 Playwright 探勘能力",
                        "actual": str(exc),
                        "evidence": {"requires": "playwright", "workspace_dir": str(workspace), "output_dir": str(output)},
                    },
                    signal_kind="issue",
                    default_source="browser_ui_replay",
                )
            )
        return issues

    config = UIJourneyConfig(
        project=project_id,
        base_url=replay.base_url,
        execute_browser=replay.execute_browser,
        browser=replay.browser,
        headless=replay.headless,
        max_pages=replay.max_pages,
    )
    explorer = BrowserExplorer()
    try:
        result = explorer.explore(config, workspace=workspace, output=output)
    except Exception as exc:
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_BROWSER_UI_{len(issues)+1:04d}",
                    "title": "真实浏览器 UI 探勘执行失败",
                    "defect_family": "ui",
                    "risk_type": "browser_ui_replay",
                    "severity": "P2",
                    "confidence": 0.52,
                    "status": "needs_human_review",
                    "source": "browser_ui_replay",
                    "route": "/",
                    "path": "/",
                    "expected": "真实浏览器探勘应可执行并输出页面/元素地图",
                    "actual": str(exc),
                    "evidence": {"scenario": scenario, "workspace_dir": str(workspace), "output_dir": str(output)},
                },
                signal_kind="issue",
                default_source="browser_ui_replay",
            )
        )
        return issues

    page_map = result.get("page_map") if isinstance(result, dict) else {}
    element_map = result.get("element_map") if isinstance(result, dict) else {}
    status = str(page_map.get("status") or "")
    pages = page_map.get("pages") if isinstance(page_map.get("pages"), list) else []
    elements = element_map.get("elements") if isinstance(element_map.get("elements"), list) else []

    if status != "explored":
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_BROWSER_UI_{len(issues)+1:04d}",
                    "title": "真实浏览器 UI 探勘未执行或被跳过",
                    "defect_family": "ui",
                    "risk_type": "browser_ui_replay",
                    "severity": "P3",
                    "confidence": 0.4,
                    "status": "needs_human_review",
                    "source": "browser_ui_replay",
                    "route": "/",
                    "path": "/",
                    "expected": "可执行真实浏览器探勘并产生 explored 状态",
                    "actual": str(page_map.get("reason") or status or "skipped"),
                    "evidence": {"page_map": {"status": status, "reason": page_map.get("reason"), "base_url": page_map.get("base_url")}},
                },
                signal_kind="issue",
                default_source="browser_ui_replay",
            )
        )
        return issues

    if not pages:
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_BROWSER_UI_{len(issues)+1:04d}",
                    "title": "真实浏览器未捕获到任何可渲染页面",
                    "defect_family": "ui",
                    "risk_type": "browser_ui_replay",
                    "severity": "P1",
                    "confidence": 0.85,
                    "status": "needs_human_review",
                    "source": "browser_ui_replay",
                    "route": "/",
                    "path": "/",
                    "expected": "至少捕获到入口页面与可访问页面列表",
                    "actual": "pages 为空",
                    "evidence": {"page_map": {"base_url": page_map.get("base_url"), "edges": page_map.get("edges")}},
                },
                signal_kind="issue",
                default_source="browser_ui_replay",
            )
        )
        return issues

    first_page = pages[0] if isinstance(pages[0], dict) else {}
    text_fragments = first_page.get("text_fragments") if isinstance(first_page.get("text_fragments"), list) else []
    link_count = len(first_page.get("links") or []) if isinstance(first_page.get("links"), list) else 0
    if len(text_fragments) <= 1:
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_BROWSER_UI_{len(issues)+1:04d}",
                    "title": "入口页面疑似空白页或渲染失败",
                    "defect_family": "ui",
                    "risk_type": "browser_ui_replay",
                    "severity": "P1",
                    "confidence": 0.82,
                    "status": "needs_human_review",
                    "source": "browser_ui_replay",
                    "route": "/",
                    "path": "/",
                    "expected": "入口页面应包含可见文本和导航元素",
                    "actual": f"text_fragments_count={len(text_fragments)}",
                    "evidence": {"page": _summarize_page(first_page)},
                },
                signal_kind="issue",
                default_source="browser_ui_replay",
            )
        )
    if link_count <= 0:
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_BROWSER_UI_{len(issues)+1:04d}",
                    "title": "入口页面缺少可访问导航链接，存在断链风险",
                    "defect_family": "uiux",
                    "risk_type": "frontend_ux",
                    "severity": "P2",
                    "confidence": 0.72,
                    "status": "needs_human_review",
                    "source": "browser_ui_replay",
                    "route": "/",
                    "path": "/",
                    "expected": "入口页面至少暴露核心导航或 CTA 链接",
                    "actual": "link_count=0",
                    "evidence": {"page": _summarize_page(first_page)},
                },
                signal_kind="issue",
                default_source="browser_ui_replay",
            )
        )
    if len(elements) <= 2:
        issues.append(
            normalize_defect_signal(
                {
                    "issue_id": f"ISSUE_BROWSER_UI_{len(issues)+1:04d}",
                    "title": "入口页面关键交互元素过少，疑似交互不可用",
                    "defect_family": "uiux",
                    "risk_type": "frontend_ux",
                    "severity": "P2",
                    "confidence": 0.65,
                    "status": "needs_human_review",
                    "source": "browser_ui_replay",
                    "route": "/",
                    "path": "/",
                    "expected": "入口页面应至少包含按钮/输入框/链接等可交互元素",
                    "actual": f"elements_count={len(elements)}",
                    "evidence": {"element_sample": elements[:10]},
                },
                signal_kind="issue",
                default_source="browser_ui_replay",
            )
        )
    oracle = _load_ui_design_oracle(project_id, cfg, root)
    if oracle:
        dedupe_keys: set[tuple[str, str, str]] = set()
        normalized_elements = [item for item in elements if isinstance(item, dict)]
        for page in pages[:5]:
            page_dict = page if isinstance(page, dict) else {}
            route_signature = _route_signature_from_url(str(page_dict.get("url") or replay.base_url or ""))
            _append_design_oracle_issues(
                issues,
                project_id=project_id,
                route_signature=route_signature,
                oracle=oracle,
                page=page_dict,
                elements=normalized_elements,
                dedupe_keys=dedupe_keys,
            )
    return issues

from __future__ import annotations

import time
from typing import Any

from .defect_family_registry import iter_defect_families


BROWSER_DECLARED_SOURCES = {
    "browser_ui_replay",
    "frontend_runtime",
    "frontend_execution_runtime",
    "frontend_runtime_smoke",
    "frontend_smoke",
    "frontend_preview",
    "frontend_ux",
    "frontend_ux_adapter",
    "frontend_interaction_acceptance",
}

SOURCE_ALIASES = {
    "frontend_runtime": {"frontend_execution_runtime", "frontend_runtime_smoke"},
    "frontend_smoke": {"frontend_runtime_smoke"},
    "frontend_ux": {"frontend_ux_adapter"},
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _check_map(onboarding: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(c.get("name") or ""): c for c in (onboarding.get("checks") or []) if isinstance(c, dict) and c.get("name")}


def _lane(ok: bool, *, plan_only: bool = False) -> str:
    if plan_only:
        return "plan_only"
    return "capability_ready" if ok else "blocked_by_preflight"


def _source_row(
    family: dict[str, Any],
    source_id: str,
    *,
    source_probe_count: int,
    api_contract_ready: bool,
    api_contract_plan_only: bool,
    runtime_ready: bool,
    browser_ready: bool,
    browser_enabled: bool,
    browser_reason_code: str,
    browser_reason: str,
    browser_action: str,
    testops_health: dict[str, Any],
    testops_testable: bool,
) -> dict[str, Any]:
    execution_modes = list(family.get("allowed_execution_modes") or [])
    reason_code = ""
    reason = ""
    action = ""
    if source_probe_count > 0:
        lane = "source_ready"
    elif source_id in BROWSER_DECLARED_SOURCES and not browser_ready:
        lane = "plan_only" if not browser_enabled else "blocked_by_preflight"
        reason_code = browser_reason_code or ("E_BROWSER_DISABLED" if not browser_enabled else "E_BROWSER_UNAVAILABLE")
        reason = browser_reason or "浏览器能力不可用，当前 source 未能落入主发现流程"
        action = browser_action or "安装 Playwright 浏览器缓存或启用 browser_ui 探勘"
    elif any(mode in {"api_probe", "runtime_signal", "performance_oracle"} for mode in execution_modes) and not runtime_ready:
        lane = "plan_only" if api_contract_plan_only else "blocked_by_preflight"
        reason_code = "E_RUNTIME_PLAN_ONLY" if api_contract_plan_only else "E_BASE_URL_UNREACHABLE"
        reason = "未配置或未允许在线请求，当前 source 仅保留计划态" if api_contract_plan_only else "目标环境不可达，当前 source 无法落地"
        action = "配置 staging base_url 并通过安全边界" if api_contract_plan_only else "修复目标环境可达性与安全边界配置"
    elif "contract_only" in execution_modes and not api_contract_ready:
        lane = "blocked_by_preflight"
        reason_code = "E_OPENAPI_UNAVAILABLE"
        reason = "缺少 OpenAPI，当前 source 无法派生契约级探针"
        action = "提供可解析的 OpenAPI 文档，并确保包含 paths"
    elif "compatibility_matrix" in execution_modes and not testops_testable:
        lane = "plan_only" if not testops_health else "blocked_by_preflight"
        reason_code = "E_TESTOPS_DISABLED" if not testops_health else "E_ENV_NOT_TESTABLE"
        reason = "未启用企业 TestOps 控制平面，当前 source 暂停在计划态" if not testops_health else "目标环境不可测，当前 source 无法形成差分/兼容矩阵"
        action = "启用 enterprise_testops_preflight 并配置 target_environment" if not testops_health else "修复环境可测性并确保可运行探针"
    else:
        lane = "plan_only"
        reason_code = "E_SOURCE_NOT_MATERIALIZED"
        reason = "注册表声明了该 source，但当前项目输入或主发现链路尚未实际产出对应探针"
        action = "补齐该 source 的项目输入，或将对应适配器接入当前主发现流程"
    return {
        "defect_family": str(family.get("family_id") or ""),
        "reporting_bucket": str(family.get("reporting_bucket") or ""),
        "source_id": source_id,
        "coverage_kind": "declared_source",
        "preflight_lane": lane,
        "reason_code": reason_code,
        "reason": reason,
        "action": action,
        "execution_modes": execution_modes,
        "probe_count": int(source_probe_count),
        "evidence_bias": list(family.get("required_evidence") or []),
    }


def _source_probe_count(
    family_id: str,
    source_id: str,
    probe_source_map: dict[tuple[str, str], int],
) -> int:
    candidate_sources = {source_id}
    candidate_sources.update({str(item).strip() for item in SOURCE_ALIASES.get(source_id, set()) if str(item).strip()})
    return sum(int(probe_source_map.get((family_id, candidate), 0) or 0) for candidate in candidate_sources)


def build_full_spectrum_capability_matrix(
    probes: list[dict[str, Any]] | None,
    *,
    onboarding: dict[str, Any] | None = None,
    browser_ui_health: dict[str, Any] | None = None,
    enterprise_testops_preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    probes = probes or []
    onboarding = onboarding or {}
    browser_ui_health = browser_ui_health or {}
    enterprise_testops_preflight = enterprise_testops_preflight or {}

    checks = _check_map(onboarding)
    base_url_configured = bool(str((checks.get("base_url_reachable") or {}).get("skipped") or "").lower() != "true") and bool(
        (checks.get("base_url_reachable") or {}).get("ok")
    )
    live_allowed = bool((checks.get("safety_boundary") or {}).get("ok"))
    openapi_ok = bool((checks.get("openapi_parse") or {}).get("ok"))
    openapi_paths = int((checks.get("openapi_paths") or {}).get("count") or 0)
    test_accounts = bool((checks.get("test_accounts") or {}).get("ok"))

    browser_enabled = bool(browser_ui_health.get("enabled"))
    browser_reason_code = str(browser_ui_health.get("reason_code") or "")
    browser_ready = browser_enabled and browser_reason_code in {"", "OK"}

    testops_health = (enterprise_testops_preflight.get("environment_health") or {}) if isinstance(enterprise_testops_preflight, dict) else {}
    testops_testable = bool(testops_health.get("target_testable"))
    testops_data_ratio = float(((enterprise_testops_preflight.get("test_data") or {}).get("automatic_preparation_ratio") or 0.0)) if isinstance(enterprise_testops_preflight, dict) else 0.0

    families = iter_defect_families()
    capability_rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    probe_source_map: dict[tuple[str, str], int] = {}
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        family_id = str(probe.get("defect_family") or "")
        source_id = str(probe.get("source") or "")
        if not family_id or not source_id:
            continue
        key = (family_id, source_id)
        probe_source_map[key] = int(probe_source_map.get(key, 0) or 0) + 1

    for family in families:
        family_id = str(family.get("family_id") or "")
        reporting_bucket = str(family.get("reporting_bucket") or "")

        api_contract_ready = openapi_ok and openapi_paths > 0
        api_contract_plan_only = not live_allowed or not base_url_configured
        runtime_ready = live_allowed and base_url_configured

        capability_specs: list[dict[str, Any]] = [
            {
                "capability_id": "api_contract_acceptance",
                "lane": _lane(api_contract_ready, plan_only=False),
                "reason_code": "" if api_contract_ready else "E_OPENAPI_UNAVAILABLE",
                "reason": "" if api_contract_ready else "OpenAPI 未加载或无有效 paths",
                "action": "" if api_contract_ready else "在接入配置中提供可解析的 OpenAPI 文档，并确保包含 paths",
                "execution_modes": ["contract_only", "api_probe"],
            },
            {
                "capability_id": "runtime_http_probes",
                "lane": _lane(runtime_ready, plan_only=api_contract_plan_only),
                "reason_code": "" if runtime_ready else ("E_RUNTIME_PLAN_ONLY" if api_contract_plan_only else "E_BASE_URL_UNREACHABLE"),
                "reason": "" if runtime_ready else ("未配置或未允许在线请求，仅生成计划" if api_contract_plan_only else "Base URL 不可达或被安全边界阻断"),
                "action": "" if runtime_ready else ("配置 staging base_url 并通过安全边界" if api_contract_plan_only else "修复目标环境可达性与安全边界配置"),
                "execution_modes": ["api_probe"],
            },
            {
                "capability_id": "security_boundary_probes",
                "lane": _lane(runtime_ready and test_accounts, plan_only=api_contract_plan_only),
                "reason_code": "" if (runtime_ready and test_accounts) else ("E_TEST_ACCOUNTS_MISSING" if runtime_ready else "E_RUNTIME_PLAN_ONLY"),
                "reason": "" if (runtime_ready and test_accounts) else ("缺少测试账号，权限/越权类探针会降级" if runtime_ready else "未允许在线请求"),
                "action": "" if (runtime_ready and test_accounts) else ("配置 test_accounts.json 并补齐登录链路" if runtime_ready else "配置 staging base_url 并通过安全边界"),
                "execution_modes": ["api_probe"],
            },
            {
                "capability_id": "browser_ui_replay",
                "lane": _lane(browser_ready, plan_only=not browser_enabled),
                "reason_code": "" if browser_ready else (browser_reason_code or ("E_BROWSER_DISABLED" if not browser_enabled else "E_BROWSER_UNAVAILABLE")),
                "reason": "" if browser_ready else str(browser_ui_health.get("reason") or "浏览器能力不可用"),
                "action": "" if browser_ready else str(browser_ui_health.get("action") or "安装 Playwright 浏览器缓存或启用 browser_ui 探勘"),
                "execution_modes": ["frontend_runtime"],
            },
            {
                "capability_id": "local_analyzers",
                "lane": "capability_ready",
                "reason_code": "",
                "reason": "",
                "action": "",
                "execution_modes": ["plan_only"],
            },
            {
                "capability_id": "static_sast_lite",
                "lane": "capability_ready",
                "reason_code": "",
                "reason": "",
                "action": "",
                "execution_modes": ["plan_only"],
            },
            {
                "capability_id": "property_fuzz_lite",
                "lane": _lane(openapi_ok and openapi_paths > 0, plan_only=False),
                "reason_code": "" if (openapi_ok and openapi_paths > 0) else "E_OPENAPI_UNAVAILABLE",
                "reason": "" if (openapi_ok and openapi_paths > 0) else "缺少 OpenAPI，无法派生通用 property/fuzz 输入域",
                "action": "" if (openapi_ok and openapi_paths > 0) else "提供 OpenAPI 文档以生成参数边界与 payload 变体",
                "execution_modes": ["contract_only"],
            },
            {
                "capability_id": "differential_tests",
                "lane": _lane(testops_testable, plan_only=not testops_health),
                "reason_code": "" if testops_testable else ("E_TESTOPS_DISABLED" if not testops_health else "E_ENV_NOT_TESTABLE"),
                "reason": "" if testops_testable else ("未启用企业 TestOps 控制平面" if not testops_health else "目标环境不可测，差分测试无法执行"),
                "action": "" if testops_testable else ("启用 enterprise_testops_preflight 并配置 target_environment" if not testops_health else "修复环境可测性并确保可运行探针"),
                "execution_modes": ["compatibility_matrix"],
            },
        ]

        for spec in capability_specs:
            capability_rows.append(
                {
                    "defect_family": family_id,
                    "reporting_bucket": reporting_bucket,
                    "capability_id": spec["capability_id"],
                    "preflight_lane": spec["lane"],
                    "reason_code": spec.get("reason_code", ""),
                    "reason": spec.get("reason", ""),
                    "action": spec.get("action", ""),
                    "execution_modes": list(spec.get("execution_modes") or []),
                    "evidence_bias": list(family.get("required_evidence") or []),
                }
            )
        for source_id in [str(item).strip() for item in (family.get("probe_sources") or []) if str(item).strip()]:
            source_rows.append(
                _source_row(
                    family,
                    source_id,
                    source_probe_count=_source_probe_count(family_id, source_id, probe_source_map),
                    api_contract_ready=api_contract_ready,
                    api_contract_plan_only=api_contract_plan_only,
                    runtime_ready=runtime_ready,
                    browser_ready=browser_ready,
                    browser_enabled=browser_enabled,
                    browser_reason_code=browser_reason_code,
                    browser_reason=str(browser_ui_health.get("reason") or ""),
                    browser_action=str(browser_ui_health.get("action") or ""),
                    testops_health=testops_health,
                    testops_testable=testops_testable,
                )
            )

    rows = capability_rows + source_rows
    by_lane: dict[str, int] = {}
    for row in rows:
        by_lane[str(row.get("preflight_lane") or "")] = by_lane.get(str(row.get("preflight_lane") or ""), 0) + 1

    declared_source_count = len(source_rows)
    materialized_source_count = sum(1 for row in source_rows if str(row.get("preflight_lane") or "").endswith("ready"))
    missing_source_count = max(0, declared_source_count - materialized_source_count)
    capability_summary = {
        "browser_ui_ready": bool(browser_ready),
        "browser_ui_reason_code": browser_reason_code,
        "runtime_ready": bool(live_allowed and base_url_configured),
        "openapi_ready": bool(openapi_ok and openapi_paths > 0),
        "enterprise_testops_testable": bool(testops_testable),
        "enterprise_testops_data_automatic_preparation_ratio": testops_data_ratio,
        "declared_source_count": declared_source_count,
        "materialized_source_count": materialized_source_count,
        "missing_source_count": missing_source_count,
    }
    return {
        "engine": "full_spectrum_capability_matrix_v1",
        "generated_at": _now(),
        "family_count": len(families),
        "capability_row_count": len(capability_rows),
        "source_row_count": len(source_rows),
        "by_preflight_lane": dict(sorted(by_lane.items())),
        "summary": capability_summary,
        "rows": rows,
    }


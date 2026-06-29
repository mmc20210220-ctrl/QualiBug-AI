from __future__ import annotations

import time
from typing import Any

from .defect_family_registry import iter_defect_families


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _check_map(onboarding: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(c.get("name") or ""): c for c in (onboarding.get("checks") or []) if isinstance(c, dict) and c.get("name")}


def _lane(ok: bool, *, plan_only: bool = False) -> str:
    if plan_only:
        return "plan_only"
    return "capability_ready" if ok else "blocked_by_preflight"


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
    rows: list[dict[str, Any]] = []

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
            rows.append(
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

    by_lane: dict[str, int] = {}
    for row in rows:
        by_lane[str(row.get("preflight_lane") or "")] = by_lane.get(str(row.get("preflight_lane") or ""), 0) + 1

    capability_rows = rows
    capability_summary = {
        "browser_ui_ready": bool(browser_ready),
        "browser_ui_reason_code": browser_reason_code,
        "runtime_ready": bool(live_allowed and base_url_configured),
        "openapi_ready": bool(openapi_ok and openapi_paths > 0),
        "enterprise_testops_testable": bool(testops_testable),
        "enterprise_testops_data_automatic_preparation_ratio": testops_data_ratio,
    }
    return {
        "engine": "full_spectrum_capability_matrix_v1",
        "generated_at": _now(),
        "family_count": len(families),
        "capability_row_count": len(capability_rows),
        "by_preflight_lane": dict(sorted(by_lane.items())),
        "summary": capability_summary,
        "rows": capability_rows,
    }


from __future__ import annotations

"""Phase105N: acceptance gate for the frontend delivery preview server.

Phase105M turned the Phase105L frontend delivery bundle into a local read-only
preview server.  Phase105N validates that preview surface as a demo/review
contract: the entry page must load, each core page must be reachable, read-only
APIs must return the expected envelope, unsafe writes and directory traversal
must be rejected, delivery handoff artifacts must be visible, checksums must be
available, and no raw secrets or Python tracebacks may leak through the bundle.

The gate uses the pure routing layer of ``Phase105FrontendPreviewSite``.  That
keeps it deterministic and CI-friendly: no network socket is required to prove
that the preview service is ready for customer demos.
"""

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase105_frontend_delivery_bundle import (
    FRONTEND_DELIVERY_CHECKSUMS,
    FRONTEND_DELIVERY_MANIFEST_JSON,
    FRONTEND_DELIVERY_REPORT_MD,
    FRONTEND_DELIVERY_ZIP,
    HANDOFF_DIR,
    HUB_DIR,
    INTERACTION_ACCEPTANCE_DIR,
    scan_frontend_delivery_for_secret_leaks,
)
from ai_test_asset_center.phase105_frontend_preview_server import (
    PHASE105M_VERSION,
    PREVIEW_API_PREFIX,
    PREVIEW_MANIFEST_JSON,
    PREVIEW_MANIFEST_MD,
    Phase105FrontendPreviewSite,
)

PHASE105N_VERSION = "phase105n-frontend-preview-acceptance-v1"

FRONTEND_PREVIEW_ACCEPTANCE_JSON = "frontend_preview_acceptance_report.json"
FRONTEND_PREVIEW_ACCEPTANCE_MD = "frontend_preview_acceptance_report.md"
FRONTEND_PREVIEW_ACCEPTANCE_MANIFEST = "frontend_preview_acceptance_manifest.json"

REQUIRED_PREVIEW_API_ROUTES: tuple[str, ...] = (
    f"{PREVIEW_API_PREFIX}/health",
    f"{PREVIEW_API_PREFIX}/manifest",
    f"{PREVIEW_API_PREFIX}/pages",
    f"{PREVIEW_API_PREFIX}/acceptance",
    f"{PREVIEW_API_PREFIX}/delivery",
    f"{PREVIEW_API_PREFIX}/handoff",
    f"{PREVIEW_API_PREFIX}/checksums",
)

REQUIRED_HANDOFF_DOCS: tuple[str, ...] = (
    "README_FRONTEND_DELIVERY.md",
    "DEMO_RUNBOOK.md",
    "CUSTOMER_WALKTHROUGH_SCRIPT.md",
    "FRONTEND_DELIVERY_CHECKLIST.md",
)

EXPECTED_PAGE_KEYS: tuple[str, ...] = (
    "product_shell",
    "dashboard",
    "customer_intake",
    "environment_diagnosis",
    "business_flow_map",
    "test_execution",
    "risk_evidence",
    "report_roi",
)

PAGE_COPY_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "product_shell": ("质量驾驶舱", "客户资料导入", "环境诊断", "风险", "ROI"),
    "dashboard": ("上线", "质量", "Top 风险", "环境", "ROI"),
    "customer_intake": ("客户资料", "AI", "业务链路", "环境", "补料"),
    "environment_diagnosis": ("环境", "URL", "DNS", "HTTP", "认证", "API Smoke"),
    "business_flow_map": ("业务", "节点", "风险", "覆盖", "证据"),
    "test_execution": ("测试计划", "实时", "可执行", "阻断", "探针", "证据"),
    "risk_evidence": ("风险", "证据链", "复现", "业务影响", "修复"),
    "report_roi": ("领导", "ROI", "上线建议", "执行摘要", "节省工时"),
}

FORBIDDEN_PREVIEW_ACCEPTANCE_PATTERNS: tuple[str, ...] = (
    "raw-token",
    "raw-cookie",
    "raw-session",
    "raw-password",
    "client_secret=",
    "clientSecret=raw",
    "SESSION=raw",
    "Bearer raw",
    "DemoPasswordShouldBeRedacted",
    "Traceback (most recent call last)",
)


@dataclass(frozen=True)
class FrontendPreviewAcceptanceCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class FrontendPreviewAcceptanceReport:
    passed: bool
    score: int
    version: str
    preview_version: str
    bundle_dir: str
    output_dir: str
    checks: list[FrontendPreviewAcceptanceCheck] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return redact_value(
            {
                "passed": self.passed,
                "score": self.score,
                "version": self.version,
                "preview_version": self.preview_version,
                "bundle_dir": self.bundle_dir,
                "output_dir": self.output_dir,
                "checks": [asdict(check) for check in self.checks],
                "artifacts": self.artifacts,
            }
        )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json_dump(data: Any) -> str:
    return json.dumps(redact_value(data), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _read_text(path: Path, *, limit: int | None = None) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    return text if limit is None else text[:limit]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload) if isinstance(payload, Mapping) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _scan_text(text: str, patterns: Sequence[str]) -> list[str]:
    return [pattern for pattern in patterns if pattern in text]


def _route_json(site: Phase105FrontendPreviewSite, route: str, *, method: str = "GET") -> dict[str, Any]:
    response = site.route(route, method=method)
    try:
        payload = response.json_body()
    except json.JSONDecodeError:
        payload = {}
    return {"status": response.status, "payload": payload, "body": response.body.decode("utf-8", errors="ignore")}


def _page_route(page: Mapping[str, Any]) -> str:
    url = str(page.get("url") or "").strip().lstrip("/")
    return f"/{url}" if url else ""


def _page_key(page: Mapping[str, Any]) -> str:
    return str(page.get("key") or "").strip()


def _page_text_key(page_key: str, route: str) -> str:
    if page_key:
        return page_key
    parts = [part for part in route.split("/") if part]
    return parts[-2] if len(parts) >= 2 else route


def validate_frontend_preview_acceptance(
    bundle_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    scenario: str = "manufacturing",
    build_bundle: bool = False,
    min_score: int = 90,
    host: str = "127.0.0.1",
    port: int = 8795,
) -> FrontendPreviewAcceptanceReport:
    """Validate the Phase105M preview surface without opening a socket."""
    bundle = Path(bundle_dir)
    output = Path(output_dir) if output_dir is not None else bundle
    output.mkdir(parents=True, exist_ok=True)
    site = Phase105FrontendPreviewSite(scenario=scenario, bundle_dir=bundle, build_bundle=build_bundle)
    checks: list[FrontendPreviewAcceptanceCheck] = []

    def add(key: str, passed: bool, detail: str, severity: str = "critical") -> None:
        checks.append(FrontendPreviewAcceptanceCheck(key=key, passed=passed, detail=detail, severity=severity))

    preview_manifest_path = bundle / PREVIEW_MANIFEST_JSON
    preview_manifest_md_path = bundle / PREVIEW_MANIFEST_MD
    add(
        "preview_manifest_files",
        preview_manifest_path.exists() and preview_manifest_md_path.exists(),
        "预览服务 manifest JSON/Markdown 已生成。"
        if preview_manifest_path.exists() and preview_manifest_md_path.exists()
        else "缺少预览服务 manifest JSON 或 Markdown。",
    )

    health = _route_json(site, f"{PREVIEW_API_PREFIX}/health")
    health_payload = health.get("payload", {})
    health_data = health_payload.get("data", {}) if isinstance(health_payload, Mapping) else {}
    add(
        "health_api",
        health["status"] == 200 and health_payload.get("success") is True and health_data.get("status") == "ok",
        f"health status={health["status"]}, service_status={health_data.get('status')}。",
    )

    manifest_route = _route_json(site, f"{PREVIEW_API_PREFIX}/manifest")
    manifest_data = manifest_route.get("payload", {}).get("data", {}) if isinstance(manifest_route.get("payload"), Mapping) else {}
    add(
        "manifest_api",
        manifest_route["status"] == 200
        and manifest_route.get("payload", {}).get("success") is True
        and manifest_data.get("delivery_passed") is True
        and manifest_data.get("redaction_status") == "safe",
        "manifest API 返回交付通过且脱敏安全。"
        if manifest_data.get("delivery_passed") is True and manifest_data.get("redaction_status") == "safe"
        else f"manifest API 状态异常：{manifest_data.get('delivery_passed')}, {manifest_data.get('redaction_status')}。",
    )

    pages_route = _route_json(site, f"{PREVIEW_API_PREFIX}/pages")
    pages_data = pages_route.get("payload", {}).get("data", {}) if isinstance(pages_route.get("payload"), Mapping) else {}
    pages = pages_data.get("pages") if isinstance(pages_data.get("pages"), list) else []
    page_keys = {_page_key(page) for page in pages if isinstance(page, Mapping)}
    missing_page_keys = sorted(set(EXPECTED_PAGE_KEYS) - page_keys)
    add(
        "pages_api",
        pages_route["status"] == 200 and int(pages_data.get("count", 0) or 0) == len(EXPECTED_PAGE_KEYS) and not missing_page_keys,
        "pages API 暴露 8 个核心页面。" if not missing_page_keys else "pages API 缺少页面：" + ", ".join(missing_page_keys),
    )

    api_failures: list[str] = []
    for route in REQUIRED_PREVIEW_API_ROUTES:
        route_result = _route_json(site, route)
        if route_result["status"] != 200 or route_result.get("payload", {}).get("success") is not True:
            api_failures.append(f"{route}:{route_result['status']}")
    add(
        "readonly_api_routes",
        not api_failures,
        "所有只读预览 API 均返回统一 success/data/error/meta envelope。" if not api_failures else "只读 API 异常：" + ", ".join(api_failures),
    )

    static_root = site.route("/")
    root_text = static_root.body.decode("utf-8", errors="ignore")
    add(
        "static_entrypoint",
        static_root.status == 200 and "前端显示层" in root_text and "客户资料导入" in root_text,
        "根路径可打开 Hub V2 总入口。" if static_root.status == 200 else f"根路径不可访问：{static_root.status}。",
    )

    page_failures: list[str] = []
    page_copy_gaps: list[str] = []
    for page in pages:
        if not isinstance(page, Mapping):
            continue
        route = _page_route(page)
        key = _page_key(page)
        if not route:
            page_failures.append(f"{key or 'unknown'}:missing_url")
            continue
        response = site.route(route)
        text = response.body.decode("utf-8", errors="ignore")
        if response.status != 200:
            page_failures.append(f"{key}:{response.status}")
            continue
        lookup_key = _page_text_key(key, route)
        missing_keywords = [keyword for keyword in PAGE_COPY_REQUIREMENTS.get(lookup_key, ()) if keyword not in text]
        if missing_keywords:
            page_copy_gaps.append(f"{lookup_key}:" + "/".join(missing_keywords))
    add(
        "static_page_routes",
        not page_failures,
        "8 个核心页面静态路由均可访问。" if not page_failures else "页面路由异常：" + ", ".join(page_failures),
    )
    add(
        "page_business_copy",
        not page_copy_gaps,
        "各页面关键业务文案完整。" if not page_copy_gaps else "页面业务文案缺口：" + "; ".join(page_copy_gaps),
        severity="major",
    )

    handoff_route = _route_json(site, f"{PREVIEW_API_PREFIX}/handoff")
    handoff_data = handoff_route.get("payload", {}).get("data", {}) if isinstance(handoff_route.get("payload"), Mapping) else {}
    handoff_docs = handoff_data.get("docs") if isinstance(handoff_data.get("docs"), list) else []
    present_docs = {str(doc.get("name")) for doc in handoff_docs if isinstance(doc, Mapping) and doc.get("exists")}
    missing_docs = sorted(set(REQUIRED_HANDOFF_DOCS) - present_docs)
    add(
        "handoff_docs_api",
        handoff_route["status"] == 200 and not missing_docs,
        "handoff API 可读取演示、交付和客户讲解文档。" if not missing_docs else "handoff API 缺少文档：" + ", ".join(missing_docs),
    )

    checksums_route = _route_json(site, f"{PREVIEW_API_PREFIX}/checksums")
    checksum_data = checksums_route.get("payload", {}).get("data", {}) if isinstance(checksums_route.get("payload"), Mapping) else {}
    checksum_entries = checksum_data.get("entries") if isinstance(checksum_data.get("entries"), list) else []
    checksum_paths = {str(entry.get("path")) for entry in checksum_entries if isinstance(entry, Mapping)}
    add(
        "checksums_api",
        checksums_route["status"] == 200 and int(checksum_data.get("count", 0) or 0) > 10 and f"{HUB_DIR}/index.html" in checksum_paths,
        "checksums API 可读取并包含 Hub V2 总入口校验。"
        if f"{HUB_DIR}/index.html" in checksum_paths
        else "checksums API 缺少 Hub V2 总入口校验项。",
    )

    acceptance_route = _route_json(site, f"{PREVIEW_API_PREFIX}/acceptance")
    acceptance_data = acceptance_route.get("payload", {}).get("data", {}) if isinstance(acceptance_route.get("payload"), Mapping) else {}
    delivery_report = acceptance_data.get("delivery_report", {}) if isinstance(acceptance_data.get("delivery_report"), Mapping) else {}
    interaction_acceptance = acceptance_data.get("interaction_acceptance", {}) if isinstance(acceptance_data.get("interaction_acceptance"), Mapping) else {}
    add(
        "acceptance_api",
        acceptance_route["status"] == 200 and delivery_report.get("passed") is True and interaction_acceptance.get("passed") is True,
        "acceptance API 显示交付验收与交互验收均通过。"
        if delivery_report.get("passed") is True and interaction_acceptance.get("passed") is True
        else "acceptance API 未确认交付验收/交互验收通过。",
    )

    zip_response = site.route(f"/{FRONTEND_DELIVERY_ZIP}")
    report_response = site.route(f"/{FRONTEND_DELIVERY_REPORT_MD}")
    manifest_response = site.route(f"/{FRONTEND_DELIVERY_MANIFEST_JSON}")
    add(
        "delivery_static_artifacts",
        zip_response.status == 200 and report_response.status == 200 and manifest_response.status == 200,
        "zip、交付验收报告和交付 manifest 均可通过静态路由访问。"
        if zip_response.status == 200 and report_response.status == 200 and manifest_response.status == 200
        else f"交付静态物料访问异常：zip={zip_response.status}, report={report_response.status}, manifest={manifest_response.status}。",
        severity="major",
    )

    method_post = site.route(f"{PREVIEW_API_PREFIX}/health", method="POST")
    method_options = site.route(f"{PREVIEW_API_PREFIX}/health", method="OPTIONS")
    traversal = site.route("/../../etc/passwd")
    add(
        "readonly_method_guard",
        method_post.status == 405 and method_options.status == 200,
        "预览服务拒绝 POST 并允许 OPTIONS。" if method_post.status == 405 and method_options.status == 200 else "预览服务只读方法保护异常。",
    )
    add(
        "path_traversal_guard",
        traversal.status == 404,
        "目录穿越访问被拒绝。" if traversal.status == 404 else f"目录穿越保护异常：{traversal.status}。",
    )

    expected_files = [
        bundle / HUB_DIR / "index.html",
        bundle / HUB_DIR / "pages" / "test_execution" / "test_execution.html",
        bundle / HUB_DIR / "pages" / "risk_evidence" / "risk_evidence.html",
        bundle / HUB_DIR / "pages" / "report_roi" / "report_roi.html",
        bundle / INTERACTION_ACCEPTANCE_DIR / "frontend_interaction_acceptance_report.md",
        bundle / HANDOFF_DIR / "DEMO_RUNBOOK.md",
        bundle / FRONTEND_DELIVERY_CHECKSUMS,
    ]
    missing_files = [path.relative_to(bundle).as_posix() for path in expected_files if not path.exists()]
    add(
        "bundle_files",
        not missing_files,
        "预览服务依赖的关键交付文件完整。" if not missing_files else "缺少关键交付文件：" + ", ".join(missing_files),
    )

    leaks = scan_frontend_delivery_for_secret_leaks(bundle)
    route_leaks = _scan_text(root_text + json.dumps(manifest_data, ensure_ascii=False), FORBIDDEN_PREVIEW_ACCEPTANCE_PATTERNS)
    all_leaks = sorted(set(leaks + [f"preview_response contains forbidden pattern {pattern}" for pattern in route_leaks]))
    add(
        "redaction_guard",
        not all_leaks,
        "预览包与预览响应未发现原始 token/cookie/session/client_secret/traceback 泄露。" if not all_leaks else "发现疑似泄露：" + "; ".join(all_leaks),
    )

    raw_score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    passed = raw_score >= min_score and all(check.passed for check in checks if check.severity == "critical")
    report = FrontendPreviewAcceptanceReport(
        passed=passed,
        score=raw_score,
        version=PHASE105N_VERSION,
        preview_version=PHASE105M_VERSION,
        bundle_dir=str(bundle),
        output_dir=str(output),
        checks=checks,
        artifacts={
            "preview_url": f"http://{host}:{int(port)}/",
            "preview_manifest": str(bundle / PREVIEW_MANIFEST_JSON),
            "preview_manifest_md": str(bundle / PREVIEW_MANIFEST_MD),
            "delivery_manifest": str(bundle / FRONTEND_DELIVERY_MANIFEST_JSON),
            "delivery_report": str(bundle / FRONTEND_DELIVERY_REPORT_MD),
            "interaction_acceptance": str(bundle / INTERACTION_ACCEPTANCE_DIR / "frontend_interaction_acceptance_report.md"),
            "checksums": str(bundle / FRONTEND_DELIVERY_CHECKSUMS),
            "zip": str(bundle / FRONTEND_DELIVERY_ZIP),
        },
    )
    write_frontend_preview_acceptance_report(output, report)
    return report


def run_frontend_preview_acceptance(
    *,
    bundle_dir: str | Path,
    output_dir: str | Path | None = None,
    scenario: str = "manufacturing",
    build_first: bool = False,
    min_score: int = 90,
    host: str = "127.0.0.1",
    port: int = 8795,
) -> FrontendPreviewAcceptanceReport:
    return validate_frontend_preview_acceptance(
        bundle_dir,
        output_dir=output_dir,
        scenario=scenario,
        build_bundle=build_first,
        min_score=min_score,
        host=host,
        port=port,
    )


def render_frontend_preview_acceptance_markdown(report: FrontendPreviewAcceptanceReport) -> str:
    rows = "\n".join(
        f"| {check.key} | {'PASS' if check.passed else 'FAIL'} | {check.severity} | {check.detail} |"
        for check in report.checks
    )
    return f"""# Phase105N 前端预览服务验收报告

- 验收状态：{'通过' if report.passed else '未通过'}
- 验收分数：{report.score}
- 版本：`{report.version}`
- 预览服务版本：`{report.preview_version}`
- 交付包目录：`{report.bundle_dir}`
- 输出目录：`{report.output_dir}`

## 验收项

| 检查项 | 结果 | 严重级别 | 详情 |
|---|---|---|---|
{rows}

## 验收结论

Phase105N 用于确认 Phase105M 本地预览服务已经达到客户演示和交付复验标准：静态页面可访问、只读 API 可访问、关键交付物可读、写入请求被拒绝、目录穿越被阻止，并且没有原始凭证或 traceback 泄露。
"""


def write_frontend_preview_acceptance_report(output_dir: str | Path, report: FrontendPreviewAcceptanceReport) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / FRONTEND_PREVIEW_ACCEPTANCE_JSON, _json_dump(report.to_dict()))
    _write_text(output / FRONTEND_PREVIEW_ACCEPTANCE_MD, render_frontend_preview_acceptance_markdown(report))
    manifest = {
        "version": PHASE105N_VERSION,
        "generated_at": _now(),
        "passed": report.passed,
        "score": report.score,
        "bundle_dir": report.bundle_dir,
        "output_dir": report.output_dir,
        "artifacts": report.artifacts,
        "report_json": FRONTEND_PREVIEW_ACCEPTANCE_JSON,
        "report_md": FRONTEND_PREVIEW_ACCEPTANCE_MD,
    }
    _write_text(output / FRONTEND_PREVIEW_ACCEPTANCE_MANIFEST, _json_dump(manifest))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the Phase105 frontend delivery local preview server.")
    parser.add_argument("--bundle-dir", default="outputs/phase105_frontend_delivery_bundle", help="Phase105L frontend delivery bundle directory.")
    parser.add_argument("--output-dir", default="outputs/phase105_frontend_preview_acceptance", help="Where acceptance reports are written.")
    parser.add_argument("--scenario", default="manufacturing", choices=["manufacturing", "ecommerce", "saas"], help="Demo scenario used when building first.")
    parser.add_argument("--build-first", action="store_true", help="Build Phase105L bundle before validating the preview surface.")
    parser.add_argument("--min-score", type=int, default=90, help="Minimum acceptance score.")
    parser.add_argument("--host", default="127.0.0.1", help="Expected preview host for report metadata.")
    parser.add_argument("--port", type=int, default=8795, help="Expected preview port for report metadata.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report = run_frontend_preview_acceptance(
        bundle_dir=args.bundle_dir,
        output_dir=args.output_dir,
        scenario=args.scenario,
        build_first=args.build_first,
        min_score=args.min_score,
        host=args.host,
        port=args.port,
    )
    print(_json_dump(report.to_dict()))
    return 0 if report.passed else 1


__all__ = [
    "PHASE105N_VERSION",
    "FRONTEND_PREVIEW_ACCEPTANCE_JSON",
    "FRONTEND_PREVIEW_ACCEPTANCE_MD",
    "FRONTEND_PREVIEW_ACCEPTANCE_MANIFEST",
    "FrontendPreviewAcceptanceCheck",
    "FrontendPreviewAcceptanceReport",
    "run_frontend_preview_acceptance",
    "validate_frontend_preview_acceptance",
    "write_frontend_preview_acceptance_report",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

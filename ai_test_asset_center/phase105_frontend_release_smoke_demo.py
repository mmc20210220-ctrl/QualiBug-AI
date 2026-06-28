from __future__ import annotations

"""Phase105P: one-click smoke and demo command for the frontend preview release.

Phase105O produces a release-ready local preview package.  Phase105P adds the
last mile needed by sales, implementation, and frontend teams: one command to
build the release, one command to smoke-test the release without opening a
network socket, and one command to launch the local read-only preview server for
customer demos.

The smoke path intentionally uses the pure Phase105M routing layer.  It proves
that the release package can serve the Hub V2 entry, all key static pages, the
read-only APIs, write-method rejection, directory-traversal protection,
checksums, release handoff docs, and redaction guards while remaining fully
CI-friendly.
"""

import argparse
import hashlib
import json
import shutil
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase105_frontend_delivery_bundle import HUB_DIR
from ai_test_asset_center.phase105_frontend_preview_acceptance import EXPECTED_PAGE_KEYS
from ai_test_asset_center.phase105_frontend_preview_release_package import (
    API_ROUTES,
    DELIVERY_BUNDLE_DIR,
    FRONTEND_PREVIEW_RELEASE_CHECKSUMS,
    FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON,
    FRONTEND_PREVIEW_RELEASE_REPORT_JSON,
    FRONTEND_PREVIEW_RELEASE_REPORT_MD,
    FRONTEND_PREVIEW_RELEASE_ZIP,
    PHASE105O_VERSION,
    RELEASE_HANDOFF_DIR,
    build_frontend_preview_release_package,
    scan_frontend_preview_release_for_secret_leaks,
    validate_frontend_preview_release_package,
    verify_frontend_preview_release_checksums,
)
from ai_test_asset_center.phase105_frontend_preview_server import (
    PHASE105M_VERSION,
    PREVIEW_API_PREFIX,
    Phase105FrontendPreviewSite,
)

PHASE105P_VERSION = "phase105p-frontend-release-smoke-demo-v1"

RELEASE_PACKAGE_DIR = "release_package"
SMOKE_ARTIFACT_DIR = "smoke"
FRONTEND_RELEASE_SMOKE_REPORT_JSON = "frontend_release_smoke_report.json"
FRONTEND_RELEASE_SMOKE_REPORT_MD = "frontend_release_smoke_report.md"
FRONTEND_RELEASE_SMOKE_MANIFEST_JSON = "frontend_release_smoke_demo_manifest.json"
FRONTEND_RELEASE_SMOKE_MANIFEST_MD = "frontend_release_smoke_demo_manifest.md"
FRONTEND_RELEASE_SMOKE_CHECKSUMS = "CHECKSUMS.sha256"
FRONTEND_RELEASE_SMOKE_ZIP = "phase105_frontend_release_smoke_demo_package.zip"
RUN_DEMO_PS1 = "RUN_FRONTEND_DEMO.ps1"
RUN_DEMO_CMD = "RUN_FRONTEND_DEMO.cmd"
SMOKE_DEMO_PS1 = "SMOKE_FRONTEND_RELEASE.ps1"
SMOKE_DEMO_CMD = "SMOKE_FRONTEND_RELEASE.cmd"
DEMO_QUICKSTART_MD = "DEMO_QUICKSTART.md"

EXPECTED_STATIC_ROUTES: tuple[str, ...] = (
    "/",
    "/index.html",
    "/pages/product_shell/index.html",
    "/pages/dashboard/dashboard.html",
    "/pages/customer_intake/customer_intake.html",
    "/pages/environment_diagnosis/environment_diagnosis.html",
    "/pages/business_flow_map/business_flow_map.html",
    "/pages/test_execution/test_execution.html",
    "/pages/risk_evidence/risk_evidence.html",
    "/pages/report_roi/report_roi.html",
)

REQUIRED_SMOKE_FILES: tuple[str, ...] = (
    f"{RELEASE_PACKAGE_DIR}/{DELIVERY_BUNDLE_DIR}/{HUB_DIR}/index.html",
    f"{RELEASE_PACKAGE_DIR}/{DELIVERY_BUNDLE_DIR}/{HUB_DIR}/pages/test_execution/test_execution.html",
    f"{RELEASE_PACKAGE_DIR}/{FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON}",
    f"{RELEASE_PACKAGE_DIR}/{FRONTEND_PREVIEW_RELEASE_REPORT_JSON}",
    f"{RELEASE_PACKAGE_DIR}/{FRONTEND_PREVIEW_RELEASE_ZIP}",
    f"{SMOKE_ARTIFACT_DIR}/{FRONTEND_RELEASE_SMOKE_REPORT_JSON}",
    f"{SMOKE_ARTIFACT_DIR}/{FRONTEND_RELEASE_SMOKE_REPORT_MD}",
    FRONTEND_RELEASE_SMOKE_MANIFEST_JSON,
    FRONTEND_RELEASE_SMOKE_MANIFEST_MD,
    DEMO_QUICKSTART_MD,
    RUN_DEMO_PS1,
    RUN_DEMO_CMD,
    SMOKE_DEMO_PS1,
    SMOKE_DEMO_CMD,
    FRONTEND_RELEASE_SMOKE_CHECKSUMS,
)

FORBIDDEN_RELEASE_SMOKE_PATTERNS: tuple[str, ...] = (
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

SMOKE_COPY_KEYWORDS: tuple[str, ...] = (
    "一键演示",
    "本地预览服务",
    "只读 API",
    "/health",
    "/pages",
    "/acceptance",
    "checksum",
    "脱敏",
)


@dataclass(frozen=True)
class FrontendReleaseSmokeCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class FrontendReleaseSmokeReport:
    passed: bool
    score: int
    version: str
    release_version: str
    preview_version: str
    package_dir: str
    release_dir: str
    output_dir: str
    checks: list[FrontendReleaseSmokeCheck] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    route_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return redact_value(
            {
                "passed": self.passed,
                "score": self.score,
                "version": self.version,
                "release_version": self.release_version,
                "preview_version": self.preview_version,
                "package_dir": self.package_dir,
                "release_dir": self.release_dir,
                "output_dir": self.output_dir,
                "checks": [asdict(check) for check in self.checks],
                "artifacts": self.artifacts,
                "route_summary": self.route_summary,
            }
        )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json_dump(data: Any) -> str:
    return json.dumps(redact_value(data), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload) if isinstance(payload, Mapping) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_text(path: Path, *, limit: int = 80_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_checksum_files(root: Path) -> list[Path]:
    excluded = {FRONTEND_RELEASE_SMOKE_CHECKSUMS, FRONTEND_RELEASE_SMOKE_ZIP}
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in excluded and not path.name.endswith(".pyc")
    ]


def write_frontend_release_smoke_checksums(package_dir: str | Path) -> dict[str, str]:
    root = Path(package_dir)
    checksums = {path.relative_to(root).as_posix(): _sha256(path) for path in _iter_checksum_files(root)}
    lines = [f"{digest}  {name}" for name, digest in sorted(checksums.items())]
    _write_text(root / FRONTEND_RELEASE_SMOKE_CHECKSUMS, "\n".join(lines) + "\n")
    return checksums


def verify_frontend_release_smoke_checksums(package_dir: str | Path) -> list[str]:
    root = Path(package_dir)
    checksums_path = root / FRONTEND_RELEASE_SMOKE_CHECKSUMS
    if not checksums_path.exists():
        return ["missing CHECKSUMS.sha256"]
    problems: list[str] = []
    for line in checksums_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        if "  " not in line:
            problems.append(f"invalid checksum line: {line}")
            continue
        expected, rel = line.split("  ", 1)
        path = root / rel
        if not path.exists():
            problems.append(f"missing file: {rel}")
            continue
        actual = _sha256(path)
        if actual != expected:
            problems.append(f"checksum mismatch: {rel}")
    return problems


def _scan_files(root: Path, patterns: Sequence[str]) -> list[str]:
    if not root.exists():
        return [f"missing_package_dir:{root}"]
    leaks: list[str] = []
    text_suffixes = {".html", ".css", ".js", ".json", ".md", ".txt", ".ps1", ".cmd", ".yml", ".yaml"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in text_suffixes:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in patterns:
            if pattern in text:
                leaks.append(f"{path.relative_to(root).as_posix()} contains forbidden pattern {pattern}")
    return leaks


def scan_frontend_release_smoke_for_secret_leaks(package_dir: str | Path) -> list[str]:
    root = Path(package_dir)
    leaks = _scan_files(root, FORBIDDEN_RELEASE_SMOKE_PATTERNS)
    release = root / RELEASE_PACKAGE_DIR
    if release.exists():
        leaks.extend(f"{RELEASE_PACKAGE_DIR}/{item}" for item in scan_frontend_preview_release_for_secret_leaks(release))
    return sorted(set(leaks))


def _zip_smoke_package(package_dir: Path) -> str:
    zip_path = package_dir / FRONTEND_RELEASE_SMOKE_ZIP
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_dir.rglob("*")):
            if not path.is_file() or path == zip_path:
                continue
            archive.write(path, path.relative_to(package_dir).as_posix())
    return zip_path.name


def _decode_response_text(body: bytes, *, limit: int = 4000) -> str:
    return body.decode("utf-8", errors="ignore")[:limit]


def _route_json(site: Phase105FrontendPreviewSite, route: str, *, method: str = "GET") -> dict[str, Any]:
    response = site.route(route, method=method)
    try:
        payload = response.json_body()
    except json.JSONDecodeError:
        payload = {}
    return {"status": response.status, "payload": payload, "body": _decode_response_text(response.body)}


def _route_static(site: Phase105FrontendPreviewSite, route: str, *, method: str = "GET") -> dict[str, Any]:
    response = site.route(route, method=method)
    return {
        "status": response.status,
        "content_type": response.headers.get("Content-Type", ""),
        "size_bytes": len(response.body),
        "body_preview": _decode_response_text(response.body),
    }


def run_frontend_release_route_smoke(
    release_dir: str | Path,
    *,
    scenario: str = "manufacturing",
) -> dict[str, Any]:
    """Smoke the Phase105M preview surface from a Phase105O release directory."""
    release = Path(release_dir)
    bundle = release / DELIVERY_BUNDLE_DIR
    site = Phase105FrontendPreviewSite(scenario=scenario, bundle_dir=bundle, build_bundle=False)
    api_results = {route: _route_json(site, route) for route in API_ROUTES}
    static_results = {route: _route_static(site, route) for route in EXPECTED_STATIC_ROUTES}
    write_guard = _route_json(site, f"{PREVIEW_API_PREFIX}/health", method="POST")
    options_guard = _route_json(site, f"{PREVIEW_API_PREFIX}/health", method="OPTIONS")
    traversal_guard = _route_static(site, "/../../.env.local")
    health = api_results.get(f"{PREVIEW_API_PREFIX}/health", {})
    pages_payload = api_results.get(f"{PREVIEW_API_PREFIX}/pages", {}).get("payload", {})
    pages_data = pages_payload.get("data", {}) if isinstance(pages_payload, Mapping) else {}
    pages = pages_data.get("pages", []) if isinstance(pages_data, Mapping) else []
    page_keys = sorted(str(page.get("key")) for page in pages if isinstance(page, Mapping) and page.get("key"))
    return redact_value(
        {
            "release_dir": str(release),
            "bundle_dir": str(bundle),
            "api_results": api_results,
            "static_results": static_results,
            "write_guard": write_guard,
            "options_guard": options_guard,
            "traversal_guard": traversal_guard,
            "health_status": health.get("status"),
            "health_payload": health.get("payload"),
            "page_keys": page_keys,
            "expected_page_keys": list(EXPECTED_PAGE_KEYS),
            "all_api_ok": all(result.get("status") == 200 for result in api_results.values()),
            "all_static_ok": all(result.get("status") == 200 and int(result.get("size_bytes", 0)) > 100 for result in static_results.values()),
            "write_blocked": write_guard.get("status") == 405,
            "options_ok": options_guard.get("status") == 200,
            "traversal_blocked": traversal_guard.get("status") in {403, 404},
        }
    )


def _render_run_demo_ps1(port: int) -> str:
    return f'''param(
    [int]$Port = {int(port)}
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ReleaseRoot = Join-Path $Root "{RELEASE_PACKAGE_DIR}"
$StartScript = Join-Path $ReleaseRoot "{RELEASE_HANDOFF_DIR}\\START_PREVIEW_SERVER.ps1"

Write-Host "QualiBug Phase105P 一键演示启动" -ForegroundColor Cyan
Write-Host "本地预览服务: http://127.0.0.1:$Port/"
Write-Host "Health: http://127.0.0.1:$Port{PREVIEW_API_PREFIX}/health"
Write-Host "Pages: http://127.0.0.1:$Port{PREVIEW_API_PREFIX}/pages"
Write-Host "Acceptance: http://127.0.0.1:$Port{PREVIEW_API_PREFIX}/acceptance"
Write-Host "Checksums: http://127.0.0.1:$Port{PREVIEW_API_PREFIX}/checksums"

if (!(Test-Path $StartScript)) {{
    throw "Missing preview start script: $StartScript"
}}

powershell -ExecutionPolicy Bypass -File $StartScript -Port $Port
'''


def _render_run_demo_cmd(port: int) -> str:
    return f'''@echo off
set PORT=%1
if "%PORT%"=="" set PORT={int(port)}
set ROOT=%~dp0
set RELEASE_ROOT=%ROOT%{RELEASE_PACKAGE_DIR}
set START_SCRIPT=%RELEASE_ROOT%\\{RELEASE_HANDOFF_DIR}\\START_PREVIEW_SERVER.ps1

echo QualiBug Phase105P 一键演示启动
echo 本地预览服务: http://127.0.0.1:%PORT%/
echo Health: http://127.0.0.1:%PORT%{PREVIEW_API_PREFIX}/health
echo Pages: http://127.0.0.1:%PORT%{PREVIEW_API_PREFIX}/pages
echo Acceptance: http://127.0.0.1:%PORT%{PREVIEW_API_PREFIX}/acceptance
echo Checksums: http://127.0.0.1:%PORT%{PREVIEW_API_PREFIX}/checksums

powershell -ExecutionPolicy Bypass -File "%START_SCRIPT%" -Port %PORT%
'''


def _render_smoke_ps1(port: int) -> str:
    return f'''param(
    [int]$Port = {int(port)}
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "QualiBug Phase105P 前端发布包冒烟验收" -ForegroundColor Cyan
python -m ai_test_asset_center.phase105_frontend_release_smoke_demo --validate-only --package-dir $Root --output-dir (Join-Path $Root "smoke_recheck") --port $Port
'''


def _render_smoke_cmd(port: int) -> str:
    return f'''@echo off
set PORT=%1
if "%PORT%"=="" set PORT={int(port)}
set ROOT=%~dp0
echo QualiBug Phase105P 前端发布包冒烟验收
python -m ai_test_asset_center.phase105_frontend_release_smoke_demo --validate-only --package-dir "%ROOT%" --output-dir "%ROOT%smoke_recheck" --port %PORT%
'''


def _render_quickstart(manifest: Mapping[str, Any]) -> str:
    return f'''# Phase105P 前端发布包一键演示 Quickstart

本包用于把 Phase105O 前端预览服务发布包推进到“一键演示 + 冒烟验收”状态。

## 一键演示

```powershell
powershell -ExecutionPolicy Bypass -File .\\{RUN_DEMO_PS1}
```

默认打开本地预览服务：`{manifest.get('preview_url', 'http://127.0.0.1:8795/')}`。

## 冒烟验收

```powershell
powershell -ExecutionPolicy Bypass -File .\\{SMOKE_DEMO_PS1}
```

该命令会复验：

- Hub V2 首页和 8 个核心页面是否可读。
- 只读 API：`/health`、`/manifest`、`/pages`、`/acceptance`、`/delivery`、`/handoff`、`/checksums` 是否正常。
- POST 写入是否被拒绝。
- 目录穿越是否被阻止。
- `checksum` 是否可复验。
- 是否存在 token / cookie / session / client_secret / traceback 原文泄露。

## 客户演示顺序

1. 打开首页，说明这是 QualiBug AI 企业质量指挥中心。
2. 进入客户资料导入，展示企业资料如何被理解。
3. 进入环境诊断，说明为什么客户环境适配是第一关。
4. 进入业务流程地图，展示业务链路、风险点和环境阻断。
5. 进入 AI 测试计划 / 实时执行，展示 AI 正在测什么。
6. 进入风险证据链，展示 Bug 为什么可信、怎么复现、如何修。
7. 进入领导报告 / ROI，展示上线建议、节省工时和业务价值。

## 发布物料

- `{RELEASE_PACKAGE_DIR}/`：Phase105O 前端预览服务发布包。
- `{SMOKE_ARTIFACT_DIR}/`：本轮冒烟验收报告。
- `{RUN_DEMO_PS1}` / `{RUN_DEMO_CMD}`：一键演示命令。
- `{SMOKE_DEMO_PS1}` / `{SMOKE_DEMO_CMD}`：一键冒烟命令。
- `{FRONTEND_RELEASE_SMOKE_CHECKSUMS}`：完整性复验文件。
- `{FRONTEND_RELEASE_SMOKE_ZIP}`：可归档交付物。

## 脱敏说明

发布包只展示脱敏后的演示数据和只读 API，不输出原始 token、cookie、session、client_secret 或 Python traceback。
'''


def _render_manifest_md(manifest: Mapping[str, Any]) -> str:
    route_rows = "\n".join(f"| `{route}` | {'PASS' if manifest.get('route_summary', {}).get('api_results', {}).get(route, {}).get('status') == 200 else 'CHECK'} |" for route in API_ROUTES)
    return f'''# Phase105P 前端发布包冒烟演示 Manifest

- 版本：`{manifest.get('version')}`
- 生成时间：`{manifest.get('generated_at')}`
- 场景：`{manifest.get('scenario')}`
- 预览 URL：`{manifest.get('preview_url')}`
- 发布包验收：`{manifest.get('release_acceptance_passed')}` / score `{manifest.get('release_acceptance_score')}`
- 冒烟验收：`{manifest.get('smoke_passed')}` / score `{manifest.get('smoke_score')}`
- 脱敏状态：`{manifest.get('redaction_status')}`

## 只读 API 冒烟结果

| API | 状态 |
|---|---|
{route_rows}

## 一键命令

- 演示：`{RUN_DEMO_PS1}` / `{RUN_DEMO_CMD}`
- 冒烟：`{SMOKE_DEMO_PS1}` / `{SMOKE_DEMO_CMD}`
'''


def render_frontend_release_smoke_report_markdown(report: FrontendReleaseSmokeReport) -> str:
    rows = "\n".join(f"| {check.key} | {'PASS' if check.passed else 'FAIL'} | {check.severity} | {check.detail} |" for check in report.checks)
    return f'''# Phase105P 前端发布包冒烟验收报告

- 验收状态：{'通过' if report.passed else '未通过'}
- 验收分数：{report.score}
- 版本：`{report.version}`
- 发布包版本：`{report.release_version}`
- 预览服务版本：`{report.preview_version}`
- 包目录：`{report.package_dir}`

## 验收项

| 检查项 | 结果 | 严重级别 | 详情 |
|---|---|---|---|
{rows}

## 验收结论

Phase105P 用于确认前端发布包已经达到“生成发布包 → 一键冒烟 → 一键演示”的标准。它不依赖外部网络，通过 Phase105M 纯路由层验证首页、核心页面、只读 API、只读保护、目录穿越保护、checksum、handoff 文档和脱敏状态。
'''


def write_frontend_release_smoke_report(output_dir: str | Path, report: FrontendReleaseSmokeReport) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / FRONTEND_RELEASE_SMOKE_REPORT_JSON, _json_dump(report.to_dict()))
    _write_text(output / FRONTEND_RELEASE_SMOKE_REPORT_MD, render_frontend_release_smoke_report_markdown(report))


def validate_frontend_release_smoke_demo(
    package_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    scenario: str = "manufacturing",
    port: int = 8795,
    min_score: int = 90,
    require_zip: bool = True,
) -> FrontendReleaseSmokeReport:
    package = Path(package_dir)
    output = Path(output_dir) if output_dir is not None else package / SMOKE_ARTIFACT_DIR
    output.mkdir(parents=True, exist_ok=True)
    release = package / RELEASE_PACKAGE_DIR
    checks: list[FrontendReleaseSmokeCheck] = []

    def add(key: str, passed: bool, detail: str, severity: str = "critical") -> None:
        checks.append(FrontendReleaseSmokeCheck(key=key, passed=passed, detail=detail, severity=severity))

    missing = [rel for rel in REQUIRED_SMOKE_FILES if not (package / rel).exists()]
    add("required_smoke_files", not missing, "冒烟演示包关键文件完整。" if not missing else "缺少关键文件：" + ", ".join(missing))

    release_report = validate_frontend_preview_release_package(release, output_dir=release, min_score=min_score, require_zip=True) if release.exists() else None
    release_score = int(release_report.score if release_report else 0)
    add(
        "release_package_acceptance",
        bool(release_report and release_report.passed and release_score >= min_score),
        f"Phase105O 发布包验收 score={release_score}。" if release_report else "缺少 Phase105O 发布包。",
    )

    route_summary: dict[str, Any] = {}
    if release.exists():
        route_summary = run_frontend_release_route_smoke(release, scenario=scenario)
    add(
        "readonly_api_routes",
        bool(route_summary.get("all_api_ok")),
        "预览服务只读 API 冒烟全部返回 200。" if route_summary.get("all_api_ok") else "只读 API 冒烟存在失败。",
    )
    add(
        "static_page_routes",
        bool(route_summary.get("all_static_ok")),
        "首页和 8 个核心页面静态路由可访问。" if route_summary.get("all_static_ok") else "静态页面路由存在失败。",
    )
    missing_page_keys = sorted(set(EXPECTED_PAGE_KEYS) - set(route_summary.get("page_keys", [])))
    add(
        "page_inventory",
        not missing_page_keys and bool(route_summary.get("page_keys")),
        "页面清单覆盖产品壳、驾驶舱、资料导入、环境诊断、业务地图、测试执行、风险证据和报告 ROI。" if not missing_page_keys else "页面清单缺少：" + ", ".join(missing_page_keys),
        severity="major",
    )
    add(
        "readonly_guards",
        bool(route_summary.get("write_blocked")) and bool(route_summary.get("options_ok")) and bool(route_summary.get("traversal_blocked")),
        "POST 被拒绝、OPTIONS 可读、目录穿越被阻止。" if route_summary.get("write_blocked") else "只读保护不完整。",
    )

    demo_ps1_text = _read_text(package / RUN_DEMO_PS1)
    demo_cmd_text = _read_text(package / RUN_DEMO_CMD)
    demo_scripts_ok = all(
        "START_PREVIEW_SERVER" in text and RELEASE_PACKAGE_DIR in text and str(port) in text
        for text in (demo_ps1_text, demo_cmd_text)
    )
    add(
        "demo_scripts",
        demo_scripts_ok,
        "一键演示 PS1/CMD 均指向发布包预览服务。" if demo_scripts_ok else "一键演示脚本缺少预览服务启动命令。",
    )

    smoke_ps1_text = _read_text(package / SMOKE_DEMO_PS1)
    smoke_cmd_text = _read_text(package / SMOKE_DEMO_CMD)
    smoke_scripts_ok = all(
        "phase105_frontend_release_smoke_demo" in text and "--validate-only" in text
        for text in (smoke_ps1_text, smoke_cmd_text)
    )
    add(
        "smoke_scripts",
        smoke_scripts_ok,
        "一键冒烟 PS1/CMD 会复验 Phase105P 包。" if smoke_scripts_ok else "一键冒烟脚本缺少 validate-only。",
        severity="major",
    )

    quickstart = _read_text(package / DEMO_QUICKSTART_MD)
    missing_keywords = [keyword for keyword in SMOKE_COPY_KEYWORDS if keyword not in quickstart]
    add(
        "demo_quickstart_copy",
        not missing_keywords,
        "Quickstart 覆盖一键演示、本地预览服务、只读 API、checksum 和脱敏说明。" if not missing_keywords else "Quickstart 缺少关键词：" + ", ".join(missing_keywords),
        severity="major",
    )

    release_checksum_problems = verify_frontend_preview_release_checksums(release) if release.exists() else ["missing release_package"]
    add(
        "release_checksums",
        not release_checksum_problems,
        "Phase105O 发布包 checksum 复验通过。" if not release_checksum_problems else "发布包 checksum 问题：" + "; ".join(release_checksum_problems),
    )

    package_checksum_problems = verify_frontend_release_smoke_checksums(package)
    add(
        "smoke_package_checksums",
        not package_checksum_problems,
        "Phase105P 冒烟演示包 checksum 复验通过。" if not package_checksum_problems else "冒烟演示包 checksum 问题：" + "; ".join(package_checksum_problems),
        severity="major",
    )

    leaks = scan_frontend_release_smoke_for_secret_leaks(package)
    add(
        "redaction_guard",
        not leaks,
        "冒烟演示包未发现原始 token/cookie/session/client_secret/traceback 泄露。" if not leaks else "发现疑似泄露：" + "; ".join(leaks),
    )

    zip_path = package / FRONTEND_RELEASE_SMOKE_ZIP
    if require_zip:
        if not zip_path.exists():
            add("smoke_zip", False, "缺少 Phase105P 冒烟演示包 zip。", severity="major")
        else:
            try:
                with zipfile.ZipFile(zip_path) as archive:
                    names = set(archive.namelist())
                required_zip = {
                    f"{RELEASE_PACKAGE_DIR}/{FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON}",
                    f"{RELEASE_PACKAGE_DIR}/{FRONTEND_PREVIEW_RELEASE_ZIP}",
                    f"{SMOKE_ARTIFACT_DIR}/{FRONTEND_RELEASE_SMOKE_REPORT_MD}",
                    RUN_DEMO_PS1,
                    SMOKE_DEMO_PS1,
                    DEMO_QUICKSTART_MD,
                    FRONTEND_RELEASE_SMOKE_MANIFEST_JSON,
                }
                missing_zip = sorted(required_zip - names)
                add("smoke_zip", not missing_zip, "zip 归档包含发布包、冒烟报告、一键脚本和 Quickstart。" if not missing_zip else "zip 缺少：" + ", ".join(missing_zip), severity="major")
            except zipfile.BadZipFile:
                add("smoke_zip", False, "Phase105P 冒烟演示包 zip 不可读。", severity="major")
    else:
        add("smoke_zip", True, "本次验收未要求 zip。", severity="minor")

    raw_score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    passed = raw_score >= min_score and all(check.passed for check in checks if check.severity == "critical")
    release_manifest = _read_json(release / FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON)
    report = FrontendReleaseSmokeReport(
        passed=passed,
        score=raw_score,
        version=PHASE105P_VERSION,
        release_version=PHASE105O_VERSION,
        preview_version=PHASE105M_VERSION,
        package_dir=str(package),
        release_dir=str(release),
        output_dir=str(output),
        checks=checks,
        route_summary=route_summary,
        artifacts={
            "preview_url": str(release_manifest.get("preview_url", f"http://127.0.0.1:{int(port)}/")),
            "run_demo_ps1": str(package / RUN_DEMO_PS1),
            "run_demo_cmd": str(package / RUN_DEMO_CMD),
            "smoke_ps1": str(package / SMOKE_DEMO_PS1),
            "quickstart": str(package / DEMO_QUICKSTART_MD),
            "release_package": str(release),
            "release_manifest": str(release / FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON),
            "smoke_manifest": str(package / FRONTEND_RELEASE_SMOKE_MANIFEST_JSON),
            "checksums": str(package / FRONTEND_RELEASE_SMOKE_CHECKSUMS),
            "zip": str(package / FRONTEND_RELEASE_SMOKE_ZIP),
        },
    )
    write_frontend_release_smoke_report(output, report)
    return report


def _build_manifest(package: Path, report: FrontendReleaseSmokeReport, *, scenario: str, port: int) -> dict[str, Any]:
    release = package / RELEASE_PACKAGE_DIR
    release_manifest = _read_json(release / FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON)
    release_report = _read_json(release / FRONTEND_PREVIEW_RELEASE_REPORT_JSON)
    manifest: dict[str, Any] = {
        "version": PHASE105P_VERSION,
        "generated_at": _now(),
        "scenario": scenario,
        "package_dir": str(package),
        "release_dir": str(release),
        "preview_url": str(release_manifest.get("preview_url", f"http://127.0.0.1:{int(port)}/")),
        "preview_port": int(port),
        "release_version": PHASE105O_VERSION,
        "preview_version": PHASE105M_VERSION,
        "release_acceptance_passed": bool(release_report.get("passed")),
        "release_acceptance_score": int(release_report.get("score", 0) or 0),
        "smoke_passed": bool(report.passed),
        "smoke_score": int(report.score),
        "route_summary": report.route_summary,
        "one_click_commands": {
            "demo_powershell": RUN_DEMO_PS1,
            "demo_cmd": RUN_DEMO_CMD,
            "smoke_powershell": SMOKE_DEMO_PS1,
            "smoke_cmd": SMOKE_DEMO_CMD,
        },
        "api_routes": list(API_ROUTES),
        "static_routes": list(EXPECTED_STATIC_ROUTES),
        "required_files": list(REQUIRED_SMOKE_FILES),
        "redaction_status": "safe" if not scan_frontend_release_smoke_for_secret_leaks(package) else "leak_detected",
        "passed": bool(report.passed),
    }
    return redact_value(manifest)


def build_frontend_release_smoke_demo(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
    host: str = "127.0.0.1",
    port: int = 8795,
    create_zip: bool = True,
    clean: bool = True,
) -> dict[str, Any]:
    package = Path(output_dir)
    if clean and package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True, exist_ok=True)
    release = package / RELEASE_PACKAGE_DIR
    smoke = package / SMOKE_ARTIFACT_DIR

    build_frontend_preview_release_package(
        release,
        scenario=scenario,
        api_base_url=api_base_url,
        host=host,
        port=port,
        create_zip=True,
    )

    _write_text(package / RUN_DEMO_PS1, _render_run_demo_ps1(port))
    _write_text(package / RUN_DEMO_CMD, _render_run_demo_cmd(port))
    _write_text(package / SMOKE_DEMO_PS1, _render_smoke_ps1(port))
    _write_text(package / SMOKE_DEMO_CMD, _render_smoke_cmd(port))

    draft_manifest = {
        "preview_url": f"http://{host}:{int(port)}/",
    }
    _write_text(package / DEMO_QUICKSTART_MD, _render_quickstart(draft_manifest))

    write_frontend_release_smoke_checksums(package)
    report = validate_frontend_release_smoke_demo(package, output_dir=smoke, scenario=scenario, port=port, require_zip=False)
    manifest = _build_manifest(package, report, scenario=scenario, port=port)
    _write_text(package / FRONTEND_RELEASE_SMOKE_MANIFEST_JSON, _json_dump(manifest))
    _write_text(package / FRONTEND_RELEASE_SMOKE_MANIFEST_MD, _render_manifest_md(manifest))
    _write_text(package / DEMO_QUICKSTART_MD, _render_quickstart(manifest))

    write_frontend_release_smoke_checksums(package)
    if create_zip:
        _zip_smoke_package(package)
    final_report = validate_frontend_release_smoke_demo(package, output_dir=smoke, scenario=scenario, port=port, require_zip=create_zip)
    final_manifest = _build_manifest(package, final_report, scenario=scenario, port=port)
    _write_text(package / FRONTEND_RELEASE_SMOKE_MANIFEST_JSON, _json_dump(final_manifest))
    _write_text(package / FRONTEND_RELEASE_SMOKE_MANIFEST_MD, _render_manifest_md(final_manifest))
    write_frontend_release_smoke_checksums(package)
    if create_zip:
        _zip_smoke_package(package)
    final_report = validate_frontend_release_smoke_demo(package, output_dir=smoke, scenario=scenario, port=port, require_zip=create_zip)
    final_manifest = _build_manifest(package, final_report, scenario=scenario, port=port)
    _write_text(package / FRONTEND_RELEASE_SMOKE_MANIFEST_JSON, _json_dump(final_manifest))
    _write_text(package / FRONTEND_RELEASE_SMOKE_MANIFEST_MD, _render_manifest_md(final_manifest))
    write_frontend_release_smoke_checksums(package)
    if create_zip:
        _zip_smoke_package(package)
    return final_manifest | {"smoke": final_report.to_dict()}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or validate the Phase105 frontend release smoke demo package.")
    parser.add_argument("--output-dir", default="outputs/phase105_frontend_release_smoke_demo")
    parser.add_argument("--package-dir", default=None)
    parser.add_argument("--scenario", default="manufacturing", choices=["manufacturing", "ecommerce", "saas"])
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8790")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8795)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--no-zip", action="store_true")
    parser.add_argument("--no-clean", action="store_true")
    parser.add_argument("--min-score", type=int, default=90)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.validate_only:
        package = Path(args.package_dir or args.output_dir)
        report = validate_frontend_release_smoke_demo(
            package,
            output_dir=args.output_dir,
            scenario=args.scenario,
            port=args.port,
            min_score=args.min_score,
            require_zip=not args.no_zip,
        )
        print(_json_dump(report.to_dict()))
        return 0 if report.passed else 1
    result = build_frontend_release_smoke_demo(
        args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        host=args.host,
        port=args.port,
        create_zip=not args.no_zip,
        clean=not args.no_clean,
    )
    print(_json_dump(result))
    smoke = result.get("smoke", {}) if isinstance(result, Mapping) else {}
    return 0 if smoke.get("passed") else 1


__all__ = [
    "PHASE105P_VERSION",
    "RELEASE_PACKAGE_DIR",
    "SMOKE_ARTIFACT_DIR",
    "FRONTEND_RELEASE_SMOKE_REPORT_JSON",
    "FRONTEND_RELEASE_SMOKE_REPORT_MD",
    "FRONTEND_RELEASE_SMOKE_MANIFEST_JSON",
    "FRONTEND_RELEASE_SMOKE_MANIFEST_MD",
    "FRONTEND_RELEASE_SMOKE_CHECKSUMS",
    "FRONTEND_RELEASE_SMOKE_ZIP",
    "RUN_DEMO_PS1",
    "RUN_DEMO_CMD",
    "SMOKE_DEMO_PS1",
    "SMOKE_DEMO_CMD",
    "DEMO_QUICKSTART_MD",
    "FrontendReleaseSmokeCheck",
    "FrontendReleaseSmokeReport",
    "build_frontend_release_smoke_demo",
    "validate_frontend_release_smoke_demo",
    "run_frontend_release_route_smoke",
    "write_frontend_release_smoke_checksums",
    "verify_frontend_release_smoke_checksums",
    "scan_frontend_release_smoke_for_secret_leaks",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

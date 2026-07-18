from __future__ import annotations

"""Phase105O: release-ready frontend preview package.

Phase105L packages the frontend display layer, Phase105M serves that package as
an offline read-only preview site, and Phase105N proves the preview surface is
safe for customer demos.  Phase105O binds those outputs into a release package
that sales, implementation, and frontend teams can run with one local preview
command and re-validate without opening a network socket.
"""

import argparse
import hashlib
import json
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase105_frontend_delivery_bundle import (
    FRONTEND_DELIVERY_MANIFEST_JSON,
    FRONTEND_DELIVERY_REPORT_JSON,
    FRONTEND_DELIVERY_REPORT_MD,
    FRONTEND_DELIVERY_ZIP,
    HUB_DIR,
    build_frontend_delivery_bundle,
    scan_frontend_delivery_for_secret_leaks,
    validate_frontend_delivery_bundle,
)
from ai_test_asset_center.phase105_frontend_preview_acceptance import (
    FRONTEND_PREVIEW_ACCEPTANCE_JSON,
    FRONTEND_PREVIEW_ACCEPTANCE_MANIFEST,
    FRONTEND_PREVIEW_ACCEPTANCE_MD,
    PHASE105N_VERSION,
    run_frontend_preview_acceptance,
)
from ai_test_asset_center.phase105_frontend_preview_server import PHASE105M_VERSION, PREVIEW_API_PREFIX, PREVIEW_MANIFEST_JSON

PHASE105O_VERSION = "phase105o-frontend-preview-release-package-v1"

FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON = "phase105_frontend_preview_release_manifest.json"
FRONTEND_PREVIEW_RELEASE_MANIFEST_MD = "phase105_frontend_preview_release_manifest.md"
FRONTEND_PREVIEW_RELEASE_REPORT_JSON = "frontend_preview_release_acceptance_report.json"
FRONTEND_PREVIEW_RELEASE_REPORT_MD = "frontend_preview_release_acceptance_report.md"
FRONTEND_PREVIEW_RELEASE_CHECKSUMS = "CHECKSUMS.sha256"
FRONTEND_PREVIEW_RELEASE_ZIP = "phase105_frontend_preview_release_package.zip"

DELIVERY_BUNDLE_DIR = "frontend_delivery_bundle"
PREVIEW_ACCEPTANCE_DIR = "preview_acceptance"
RELEASE_HANDOFF_DIR = "release"

API_ROUTES: tuple[str, ...] = (
    f"{PREVIEW_API_PREFIX}/health",
    f"{PREVIEW_API_PREFIX}/manifest",
    f"{PREVIEW_API_PREFIX}/pages",
    f"{PREVIEW_API_PREFIX}/acceptance",
    f"{PREVIEW_API_PREFIX}/delivery",
    f"{PREVIEW_API_PREFIX}/handoff",
    f"{PREVIEW_API_PREFIX}/checksums",
)

REQUIRED_RELEASE_FILES: tuple[str, ...] = (
    f"{DELIVERY_BUNDLE_DIR}/{HUB_DIR}/index.html",
    f"{DELIVERY_BUNDLE_DIR}/{HUB_DIR}/pages/test_execution/test_execution.html",
    f"{DELIVERY_BUNDLE_DIR}/{FRONTEND_DELIVERY_MANIFEST_JSON}",
    f"{DELIVERY_BUNDLE_DIR}/{FRONTEND_DELIVERY_REPORT_JSON}",
    f"{DELIVERY_BUNDLE_DIR}/{FRONTEND_DELIVERY_REPORT_MD}",
    f"{DELIVERY_BUNDLE_DIR}/{PREVIEW_MANIFEST_JSON}",
    f"{DELIVERY_BUNDLE_DIR}/{FRONTEND_DELIVERY_ZIP}",
    f"{PREVIEW_ACCEPTANCE_DIR}/{FRONTEND_PREVIEW_ACCEPTANCE_JSON}",
    f"{PREVIEW_ACCEPTANCE_DIR}/{FRONTEND_PREVIEW_ACCEPTANCE_MD}",
    f"{PREVIEW_ACCEPTANCE_DIR}/{FRONTEND_PREVIEW_ACCEPTANCE_MANIFEST}",
    f"{RELEASE_HANDOFF_DIR}/README_FRONTEND_PREVIEW_RELEASE.md",
    f"{RELEASE_HANDOFF_DIR}/DEMO_PREVIEW_RELEASE_RUNBOOK.md",
    f"{RELEASE_HANDOFF_DIR}/PREVIEW_API_CONTRACT.md",
    f"{RELEASE_HANDOFF_DIR}/RELEASE_CHECKLIST.md",
    f"{RELEASE_HANDOFF_DIR}/START_PREVIEW_SERVER.ps1",
    f"{RELEASE_HANDOFF_DIR}/START_PREVIEW_SERVER.cmd",
    FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON,
    FRONTEND_PREVIEW_RELEASE_MANIFEST_MD,
    FRONTEND_PREVIEW_RELEASE_CHECKSUMS,
)

FORBIDDEN_FRONTEND_PREVIEW_RELEASE_PATTERNS: tuple[str, ...] = (
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

RELEASE_COPY_KEYWORDS: tuple[str, ...] = (
    "客户演示",
    "预览服务",
    "只读 API",
    "/health",
    "/manifest",
    "/pages",
    "/acceptance",
    "脱敏",
    "checksum",
)


@dataclass(frozen=True)
class FrontendPreviewReleaseCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class FrontendPreviewReleaseAcceptanceReport:
    passed: bool
    score: int
    version: str
    preview_version: str
    preview_acceptance_version: str
    release_dir: str
    output_dir: str
    checks: list[FrontendPreviewReleaseCheck] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return redact_value(
            {
                "passed": self.passed,
                "score": self.score,
                "version": self.version,
                "preview_version": self.preview_version,
                "preview_acceptance_version": self.preview_acceptance_version,
                "release_dir": self.release_dir,
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


def _scan_files(root: Path, patterns: Sequence[str]) -> list[str]:
    leaks: list[str] = []
    if not root.exists():
        return [f"missing_release_dir:{root}"]
    text_suffixes = {".html", ".css", ".js", ".json", ".md", ".txt", ".ps1", ".cmd", ".yml", ".yaml"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in text_suffixes:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in patterns:
            if pattern in text:
                leaks.append(f"{path.relative_to(root).as_posix()} contains forbidden pattern {pattern}")
    return leaks


def scan_frontend_preview_release_for_secret_leaks(release_dir: str | Path) -> list[str]:
    root = Path(release_dir)
    leaks = _scan_files(root, FORBIDDEN_FRONTEND_PREVIEW_RELEASE_PATTERNS)
    delivery_dir = root / DELIVERY_BUNDLE_DIR
    if delivery_dir.exists():
        leaks.extend(f"{DELIVERY_BUNDLE_DIR}/{item}" for item in scan_frontend_delivery_for_secret_leaks(delivery_dir))
    return sorted(set(leaks))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_checksum_files(root: Path) -> list[Path]:
    excluded = {FRONTEND_PREVIEW_RELEASE_CHECKSUMS, FRONTEND_PREVIEW_RELEASE_ZIP}
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in excluded and not path.name.endswith(".pyc")
    ]


def write_frontend_preview_release_checksums(release_dir: str | Path) -> dict[str, str]:
    root = Path(release_dir)
    checksums = {path.relative_to(root).as_posix(): _sha256(path) for path in _iter_checksum_files(root)}
    lines = [f"{digest}  {name}" for name, digest in sorted(checksums.items())]
    _write_text(root / FRONTEND_PREVIEW_RELEASE_CHECKSUMS, "\n".join(lines) + "\n")
    return checksums


def verify_frontend_preview_release_checksums(release_dir: str | Path) -> list[str]:
    root = Path(release_dir)
    checksums_path = root / FRONTEND_PREVIEW_RELEASE_CHECKSUMS
    if not checksums_path.exists():
        return ["missing CHECKSUMS.sha256"]
    problems: list[str] = []
    for line in checksums_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            expected, rel = line.split("  ", 1)
        except ValueError:
            problems.append(f"invalid checksum line: {line}")
            continue
        path = root / rel
        if not path.exists():
            problems.append(f"missing file: {rel}")
            continue
        actual = _sha256(path)
        if actual != expected:
            problems.append(f"checksum mismatch: {rel}")
    return problems


def _zip_release_package(release_dir: Path) -> str:
    zip_path = release_dir / FRONTEND_PREVIEW_RELEASE_ZIP
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(release_dir.rglob("*")):
            if not path.is_file() or path == zip_path:
                continue
            archive.write(path, path.relative_to(release_dir).as_posix())
    return zip_path.name


def _render_start_preview_ps1(port: int) -> str:
    return f'''param(
    [int]$Port = {int(port)}
)

$ErrorActionPreference = "Stop"
$BundleDir = Join-Path $PSScriptRoot "..\\{DELIVERY_BUNDLE_DIR}"
Write-Host "Starting QualiBug frontend preview server on http://127.0.0.1:$Port/"
python -m ai_test_asset_center.phase105_frontend_preview_server --no-build-bundle --bundle-dir $BundleDir --port $Port
'''


def _render_start_preview_cmd(port: int) -> str:
    return f'''@echo off
set PORT=%1
if "%PORT%"=="" set PORT={int(port)}
echo Starting QualiBug frontend preview server on http://127.0.0.1:%PORT%/
python -m ai_test_asset_center.phase105_frontend_preview_server --no-build-bundle --bundle-dir "%~dp0..\\{DELIVERY_BUNDLE_DIR}" --port %PORT%
'''


def _render_release_readme(manifest: Mapping[str, Any]) -> str:
    api_rows = "\n".join(f"| `{route}` | 只读 API |" for route in manifest.get("api_routes", []))
    return f'''# Phase105O 前端预览服务发布包

这个发布包用于客户演示、售前交付和内部复验。它把 Phase105L 前端交付包、Phase105M 本地预览服务元数据、Phase105N 预览服务验收报告绑定到一个可复验目录里。

## 快速启动

```powershell
cd <release-package-root>\\{RELEASE_HANDOFF_DIR}
.\\START_PREVIEW_SERVER.ps1
```

默认打开：`{manifest.get('preview_url')}`

## 发布包内容

- `{DELIVERY_BUNDLE_DIR}/`：完整前端交付包，包含 Hub V2、页面、交互验收、handoff 文档和交付 zip。
- `{PREVIEW_ACCEPTANCE_DIR}/`：前端预览服务验收报告。
- `{RELEASE_HANDOFF_DIR}/START_PREVIEW_SERVER.ps1`：Windows PowerShell 启动脚本。
- `{RELEASE_HANDOFF_DIR}/START_PREVIEW_SERVER.cmd`：Windows CMD 启动脚本。
- `{RELEASE_HANDOFF_DIR}/PREVIEW_API_CONTRACT.md`：预览服务只读 API 说明。
- `{FRONTEND_PREVIEW_RELEASE_CHECKSUMS}`：release package checksum 完整性复验。
- `{FRONTEND_PREVIEW_RELEASE_ZIP}`：发布包归档。

## 只读 API

| 路由 | 用途 |
|---|---|
{api_rows}

## 安全说明

预览服务只读，不接收 POST 写入请求；页面和接口默认脱敏，不展示原始 token、cookie、session、password、client_secret 或 Python traceback。
'''


def _render_release_runbook(manifest: Mapping[str, Any]) -> str:
    return f'''# Demo Preview Release Runbook

## 1. 启动

```powershell
cd <release-package-root>\\{RELEASE_HANDOFF_DIR}
.\\START_PREVIEW_SERVER.ps1
```

打开 `{manifest.get('preview_url')}`。

## 2. 演示路径

1. 先说明这是 QualiBug AI 企业质量指挥中心的前端预览服务发布包。
2. 打开首页，展示客户资料导入、环境诊断、业务流程地图、AI 测试计划、实时测试执行、风险证据链、领导层报告和 ROI。
3. 打开 `{PREVIEW_API_PREFIX}/health`，说明服务健康和脱敏状态。
4. 打开 `{PREVIEW_API_PREFIX}/pages`，说明 8 个核心页面已经接入。
5. 打开 `{PREVIEW_API_PREFIX}/acceptance`，说明交付验收和交互验收已通过。
6. 打开 `{PREVIEW_ACCEPTANCE_DIR}/{FRONTEND_PREVIEW_ACCEPTANCE_MD}`，说明预览服务已经完成复验。
7. 打开 `{FRONTEND_PREVIEW_RELEASE_MANIFEST_MD}`，说明发布包可复验、可归档。

## 3. 客户讲解重点

- 先证明环境和认证是否可测，再谈执行。
- 用业务链路地图说明 AI 不是随机扫接口。
- 用风险证据链证明 Bug 真实、可复现、可修复、可关闭。
- 用领导层报告和 ROI 说明上线决策价值。
'''


def _render_api_contract(manifest: Mapping[str, Any]) -> str:
    route_rows = "\n".join(f"| `{route}` | GET / HEAD / OPTIONS | JSON envelope：success/data/error/meta |" for route in API_ROUTES)
    return f'''# Frontend Preview API Contract

Phase105M 本地预览服务只开放只读路由。所有 API 返回统一 envelope，便于前端或验收脚本读取。

| 路由 | 方法 | 返回 |
|---|---|---|
{route_rows}

## 静态入口

- `{manifest.get('preview_url')}`：Hub V2 总入口。
- `/pages/customer_intake/customer_intake.html`
- `/pages/environment_diagnosis/environment_diagnosis.html`
- `/pages/business_flow_map/business_flow_map.html`
- `/pages/test_execution/test_execution.html`
- `/pages/risk_evidence/risk_evidence.html`
- `/pages/report_roi/report_roi.html`

## 保护要求

- POST/PUT/PATCH/DELETE 必须拒绝。
- 目录穿越必须拒绝。
- API 和页面不得输出原始 token、cookie、session、client_secret 或 traceback。
'''


def _render_release_checklist() -> str:
    items = [
        "运行 START_PREVIEW_SERVER.ps1 能打开本地预览服务",
        "Hub V2 首页可访问",
        "8 个核心页面可访问",
        "health / manifest / pages / acceptance / delivery / handoff / checksums API 均可读",
        "Phase105N 预览服务验收报告通过",
        "Phase105L 前端交付包验收报告通过",
        "CHECKSUMS.sha256 复验通过",
        "phase105_frontend_preview_release_package.zip 可读取",
        "未发现 token、cookie、session、client_secret、traceback 原文泄露",
    ]
    return "# Frontend Preview Release Checklist\n\n" + "\n".join(f"- [ ] {item}" for item in items) + "\n"


def _render_release_manifest_md(manifest: Mapping[str, Any]) -> str:
    rows = "\n".join(
        f"| `{rel}` | {'存在' if (Path(str(manifest.get('release_dir', '.'))) / rel).exists() else '缺失'} |"
        for rel in REQUIRED_RELEASE_FILES
    )
    return f'''# Phase105O 前端预览服务发布包 Manifest

- 版本：`{manifest.get('version')}`
- 生成时间：`{manifest.get('generated_at')}`
- 场景：`{manifest.get('scenario')}`
- 预览 URL：`{manifest.get('preview_url')}`
- 交付验收：`{manifest.get('delivery_passed')}` / score `{manifest.get('delivery_score')}`
- 预览验收：`{manifest.get('preview_acceptance_passed')}` / score `{manifest.get('preview_acceptance_score')}`
- 脱敏状态：`{manifest.get('redaction_status')}`

## 关键文件

| 文件 | 状态 |
|---|---|
{rows}
'''


def _build_release_manifest(
    release: Path,
    *,
    scenario: str,
    api_base_url: str,
    host: str,
    port: int,
    delivery_report: Mapping[str, Any],
    preview_acceptance: Mapping[str, Any],
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "version": PHASE105O_VERSION,
        "generated_at": _now(),
        "scenario": scenario,
        "api_base_url": api_base_url.rstrip("/"),
        "release_dir": str(release),
        "delivery_bundle_dir": str(release / DELIVERY_BUNDLE_DIR),
        "preview_acceptance_dir": str(release / PREVIEW_ACCEPTANCE_DIR),
        "release_handoff_dir": str(release / RELEASE_HANDOFF_DIR),
        "preview_url": f"http://{host}:{int(port)}/",
        "preview_host": host,
        "preview_port": int(port),
        "delivery_passed": bool(delivery_report.get("passed")),
        "delivery_score": int(delivery_report.get("score", 0) or 0),
        "preview_acceptance_passed": bool(preview_acceptance.get("passed")),
        "preview_acceptance_score": int(preview_acceptance.get("score", 0) or 0),
        "preview_server_version": PHASE105M_VERSION,
        "preview_acceptance_version": PHASE105N_VERSION,
        "api_routes": list(API_ROUTES),
        "start_scripts": [f"{RELEASE_HANDOFF_DIR}/START_PREVIEW_SERVER.ps1", f"{RELEASE_HANDOFF_DIR}/START_PREVIEW_SERVER.cmd"],
        "required_files": list(REQUIRED_RELEASE_FILES),
        "redaction_status": "safe",
        "passed": bool(delivery_report.get("passed")) and bool(preview_acceptance.get("passed")),
    }
    return redact_value(manifest)


def build_frontend_preview_release_package(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8088",
    host: str = "127.0.0.1",
    port: int = 8795,
    create_zip: bool = True,
) -> dict[str, Any]:
    release = Path(output_dir)
    release.mkdir(parents=True, exist_ok=True)
    delivery = release / DELIVERY_BUNDLE_DIR
    preview_acceptance_dir = release / PREVIEW_ACCEPTANCE_DIR
    handoff = release / RELEASE_HANDOFF_DIR

    build_frontend_delivery_bundle(delivery, scenario=scenario, api_base_url=api_base_url, create_zip=True)
    delivery_report = validate_frontend_delivery_bundle(delivery).to_dict()
    preview_report = run_frontend_preview_acceptance(
        bundle_dir=delivery,
        output_dir=preview_acceptance_dir,
        scenario=scenario,
        build_first=False,
        host=host,
        port=port,
    ).to_dict()

    manifest = _build_release_manifest(
        release,
        scenario=scenario,
        api_base_url=api_base_url,
        host=host,
        port=port,
        delivery_report=delivery_report,
        preview_acceptance=preview_report,
    )

    _write_text(handoff / "START_PREVIEW_SERVER.ps1", _render_start_preview_ps1(port))
    _write_text(handoff / "START_PREVIEW_SERVER.cmd", _render_start_preview_cmd(port))
    _write_text(handoff / "README_FRONTEND_PREVIEW_RELEASE.md", _render_release_readme(manifest))
    _write_text(handoff / "DEMO_PREVIEW_RELEASE_RUNBOOK.md", _render_release_runbook(manifest))
    _write_text(handoff / "PREVIEW_API_CONTRACT.md", _render_api_contract(manifest))
    _write_text(handoff / "RELEASE_CHECKLIST.md", _render_release_checklist())

    manifest["redaction_status"] = "safe" if not scan_frontend_preview_release_for_secret_leaks(release) else "leak_detected"
    manifest["passed"] = bool(manifest.get("passed")) and manifest["redaction_status"] == "safe"
    _write_text(release / FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON, _json_dump(manifest))
    _write_text(release / FRONTEND_PREVIEW_RELEASE_MANIFEST_MD, _render_release_manifest_md(manifest))

    write_frontend_preview_release_checksums(release)
    if create_zip:
        _zip_release_package(release)
    report = validate_frontend_preview_release_package(release, output_dir=release, require_zip=create_zip)
    write_frontend_preview_release_checksums(release)
    if create_zip:
        _zip_release_package(release)
    final_report = validate_frontend_preview_release_package(release, output_dir=release, require_zip=create_zip)
    return _read_json(release / FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON) | {"acceptance": final_report.to_dict()}


def validate_frontend_preview_release_package(
    release_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    min_score: int = 90,
    require_zip: bool = True,
) -> FrontendPreviewReleaseAcceptanceReport:
    release = Path(release_dir)
    output = Path(output_dir) if output_dir is not None else release
    output.mkdir(parents=True, exist_ok=True)
    delivery = release / DELIVERY_BUNDLE_DIR
    preview_acceptance_dir = release / PREVIEW_ACCEPTANCE_DIR
    checks: list[FrontendPreviewReleaseCheck] = []

    def add(key: str, passed: bool, detail: str, severity: str = "critical") -> None:
        checks.append(FrontendPreviewReleaseCheck(key=key, passed=passed, detail=detail, severity=severity))

    manifest = _read_json(release / FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON)
    add("release_manifest", bool(manifest), "发布包 manifest 可读取。" if manifest else "缺少或无法读取发布包 manifest。")

    missing_required = [rel for rel in REQUIRED_RELEASE_FILES if not (release / rel).exists()]
    add("required_release_files", not missing_required, "发布包关键文件完整。" if not missing_required else "缺少关键文件：" + ", ".join(missing_required))

    delivery_report = _read_json(delivery / FRONTEND_DELIVERY_REPORT_JSON)
    delivery_score = int(delivery_report.get("score", 0) or 0)
    add(
        "delivery_bundle_acceptance",
        bool(delivery_report.get("passed")) and delivery_score >= min_score,
        f"Phase105L 交付包验收 score={delivery_score}。" if delivery_report.get("passed") else "Phase105L 交付包验收未通过或报告缺失。",
    )

    preview_report = _read_json(preview_acceptance_dir / FRONTEND_PREVIEW_ACCEPTANCE_JSON)
    preview_score = int(preview_report.get("score", 0) or 0)
    add(
        "preview_acceptance",
        bool(preview_report.get("passed")) and preview_score >= min_score,
        f"Phase105N 预览服务验收 score={preview_score}。" if preview_report.get("passed") else "Phase105N 预览服务验收未通过或报告缺失。",
    )

    preview_manifest = _read_json(delivery / PREVIEW_MANIFEST_JSON)
    api_routes = preview_manifest.get("api_routes", {}) if isinstance(preview_manifest.get("api_routes"), Mapping) else {}
    present_routes = {str(path) for path in api_routes.values()}
    missing_api = [route for route in API_ROUTES if route not in present_routes]
    add(
        "preview_api_contract",
        not missing_api and bool(api_routes),
        "预览服务只读 API 合同完整。" if not missing_api and api_routes else "预览服务 API 缺口：" + ", ".join(missing_api),
    )

    script_text = _read_text(release / RELEASE_HANDOFF_DIR / "START_PREVIEW_SERVER.ps1") + _read_text(release / RELEASE_HANDOFF_DIR / "START_PREVIEW_SERVER.cmd")
    add(
        "start_scripts",
        "phase105_frontend_preview_server" in script_text and "--no-build-bundle" in script_text and DELIVERY_BUNDLE_DIR in script_text,
        "启动脚本指向本地只读预览服务和交付包。" if "phase105_frontend_preview_server" in script_text else "启动脚本未包含预览服务启动命令。",
    )

    release_docs_text = "\n".join(
        _read_text(release / RELEASE_HANDOFF_DIR / name)
        for name in ("README_FRONTEND_PREVIEW_RELEASE.md", "DEMO_PREVIEW_RELEASE_RUNBOOK.md", "PREVIEW_API_CONTRACT.md", "RELEASE_CHECKLIST.md")
    )
    missing_keywords = [keyword for keyword in RELEASE_COPY_KEYWORDS if keyword not in release_docs_text]
    add(
        "release_handoff_copy",
        not missing_keywords,
        "发布文档覆盖客户演示、预览服务、只读 API、checksum 和脱敏说明。" if not missing_keywords else "发布文档缺少关键词：" + ", ".join(missing_keywords),
        severity="major",
    )

    checksum_problems = verify_frontend_preview_release_checksums(release)
    add("release_checksums", not checksum_problems, "发布包 CHECKSUMS.sha256 复验通过。" if not checksum_problems else "checksum 问题：" + "; ".join(checksum_problems))

    leaks = scan_frontend_preview_release_for_secret_leaks(release)
    add("redaction_guard", not leaks, "发布包未发现原始 token/cookie/session/client_secret/traceback 泄露。" if not leaks else "发现疑似泄露：" + "; ".join(leaks))

    zip_path = release / FRONTEND_PREVIEW_RELEASE_ZIP
    if require_zip:
        if not zip_path.exists():
            add("release_zip", False, "缺少前端预览服务发布包 zip。", severity="major")
        else:
            try:
                with zipfile.ZipFile(zip_path) as archive:
                    names = set(archive.namelist())
                required_zip = {
                    f"{DELIVERY_BUNDLE_DIR}/{HUB_DIR}/index.html",
                    f"{DELIVERY_BUNDLE_DIR}/{PREVIEW_MANIFEST_JSON}",
                    f"{PREVIEW_ACCEPTANCE_DIR}/{FRONTEND_PREVIEW_ACCEPTANCE_MD}",
                    f"{RELEASE_HANDOFF_DIR}/START_PREVIEW_SERVER.ps1",
                    f"{RELEASE_HANDOFF_DIR}/PREVIEW_API_CONTRACT.md",
                    FRONTEND_PREVIEW_RELEASE_CHECKSUMS,
                    FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON,
                }
                missing_zip = sorted(required_zip - names)
                add("release_zip", not missing_zip, "zip 归档包含交付包、预览验收、启动脚本和 API 合同。" if not missing_zip else "zip 缺少：" + ", ".join(missing_zip), severity="major")
            except zipfile.BadZipFile:
                add("release_zip", False, "前端预览服务发布包 zip 不可读。", severity="major")
    else:
        add("release_zip", True, "本次验收未要求 zip。", severity="minor")

    raw_score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    passed = raw_score >= min_score and all(check.passed for check in checks if check.severity == "critical")
    report = FrontendPreviewReleaseAcceptanceReport(
        passed=passed,
        score=raw_score,
        version=PHASE105O_VERSION,
        preview_version=PHASE105M_VERSION,
        preview_acceptance_version=PHASE105N_VERSION,
        release_dir=str(release),
        output_dir=str(output),
        checks=checks,
        artifacts={
            "preview_url": str(manifest.get("preview_url", "http://127.0.0.1:8795/")),
            "release_manifest": str(release / FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON),
            "delivery_bundle": str(delivery),
            "preview_acceptance": str(preview_acceptance_dir / FRONTEND_PREVIEW_ACCEPTANCE_MD),
            "start_script_ps1": str(release / RELEASE_HANDOFF_DIR / "START_PREVIEW_SERVER.ps1"),
            "api_contract": str(release / RELEASE_HANDOFF_DIR / "PREVIEW_API_CONTRACT.md"),
            "checksums": str(release / FRONTEND_PREVIEW_RELEASE_CHECKSUMS),
            "zip": str(release / FRONTEND_PREVIEW_RELEASE_ZIP),
        },
    )
    write_frontend_preview_release_acceptance_report(output, report)
    return report


def render_frontend_preview_release_acceptance_markdown(report: FrontendPreviewReleaseAcceptanceReport) -> str:
    rows = "\n".join(f"| {check.key} | {'PASS' if check.passed else 'FAIL'} | {check.severity} | {check.detail} |" for check in report.checks)
    return f'''# Phase105O 前端预览服务发布包验收报告

- 验收状态：{'通过' if report.passed else '未通过'}
- 验收分数：{report.score}
- 版本：`{report.version}`
- 预览服务版本：`{report.preview_version}`
- 预览验收版本：`{report.preview_acceptance_version}`
- 发布包目录：`{report.release_dir}`

## 验收项

| 检查项 | 结果 | 严重级别 | 详情 |
|---|---|---|---|
{rows}

## 验收结论

Phase105O 用于确认前端显示层已经达到“可发布演示包”的标准：交付包完整、预览服务可启动、只读 API 可说明、预览验收通过、checksum 可复验、zip 可归档，并且没有原始凭证或 traceback 泄露。
'''


def write_frontend_preview_release_acceptance_report(output_dir: str | Path, report: FrontendPreviewReleaseAcceptanceReport) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / FRONTEND_PREVIEW_RELEASE_REPORT_JSON, _json_dump(report.to_dict()))
    _write_text(output / FRONTEND_PREVIEW_RELEASE_REPORT_MD, render_frontend_preview_release_acceptance_markdown(report))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or validate the Phase105 frontend preview release package.")
    parser.add_argument("--output-dir", default="outputs/phase105_frontend_preview_release_package")
    parser.add_argument("--release-dir", default=None)
    parser.add_argument("--scenario", default="manufacturing", choices=["manufacturing", "ecommerce", "saas"])
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8795)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--no-zip", action="store_true")
    parser.add_argument("--min-score", type=int, default=90)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target = Path(args.release_dir or args.output_dir)
    if args.validate_only:
        report = validate_frontend_preview_release_package(target, output_dir=args.output_dir, min_score=args.min_score, require_zip=not args.no_zip)
        print(_json_dump(report.to_dict()))
        return 0 if report.passed else 1
    result = build_frontend_preview_release_package(target, scenario=args.scenario, api_base_url=args.api_base_url, host=args.host, port=args.port, create_zip=not args.no_zip)
    print(_json_dump(result))
    acceptance = result.get("acceptance", {}) if isinstance(result, Mapping) else {}
    return 0 if acceptance.get("passed") else 1


__all__ = [
    "PHASE105O_VERSION",
    "FRONTEND_PREVIEW_RELEASE_MANIFEST_JSON",
    "FRONTEND_PREVIEW_RELEASE_MANIFEST_MD",
    "FRONTEND_PREVIEW_RELEASE_REPORT_JSON",
    "FRONTEND_PREVIEW_RELEASE_REPORT_MD",
    "FRONTEND_PREVIEW_RELEASE_CHECKSUMS",
    "FRONTEND_PREVIEW_RELEASE_ZIP",
    "DELIVERY_BUNDLE_DIR",
    "PREVIEW_ACCEPTANCE_DIR",
    "RELEASE_HANDOFF_DIR",
    "FrontendPreviewReleaseCheck",
    "FrontendPreviewReleaseAcceptanceReport",
    "build_frontend_preview_release_package",
    "validate_frontend_preview_release_package",
    "write_frontend_preview_release_checksums",
    "verify_frontend_preview_release_checksums",
    "scan_frontend_preview_release_for_secret_leaks",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


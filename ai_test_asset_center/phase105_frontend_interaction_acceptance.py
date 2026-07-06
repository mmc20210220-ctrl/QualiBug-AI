from __future__ import annotations

"""Phase105K: frontend interaction acceptance gate for QualiBug.

Phase105A-J created the static frontend display layer and unified the full
customer journey.  Phase105K turns that visual layer into an explicit acceptance
standard: it validates navigation closure, core customer actions, data backing,
page-level business copy, journey continuity, archive completeness, and secret
redaction for the Phase105J hub.

The gate is intentionally dependency-free and can run locally or in CI.  It can
build a fresh Phase105J hub first, or validate an already generated hub.
"""

import argparse
import json
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase105_frontend_experience_hub_v2 import (
    FRONTEND_HUB_V2_MANIFEST,
    FRONTEND_HUB_V2_ZIP,
    PAGE_SPECS_V2,
    build_frontend_experience_hub_v2,
)

PHASE105K_VERSION = "phase105k-frontend-interaction-acceptance-v1"

FRONTEND_INTERACTION_ACCEPTANCE_JSON = "frontend_interaction_acceptance_report.json"
FRONTEND_INTERACTION_ACCEPTANCE_MD = "frontend_interaction_acceptance_report.md"
FRONTEND_INTERACTION_ACCEPTANCE_MANIFEST = "frontend_interaction_acceptance_manifest.json"

REQUIRED_INTERACTION_OUTPUTS: tuple[str, ...] = (
    FRONTEND_INTERACTION_ACCEPTANCE_JSON,
    FRONTEND_INTERACTION_ACCEPTANCE_MD,
    FRONTEND_INTERACTION_ACCEPTANCE_MANIFEST,
)

FORBIDDEN_INTERACTION_PATTERNS: tuple[str, ...] = (
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

PAGE_INTERACTION_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "product_shell": ("质量驾驶舱", "客户资料导入", "环境诊断", "业务流程", "风险", "ROI"),
    "dashboard": ("上线", "质量", "Top 风险", "环境", "ROI", "下一步"),
    "customer_intake": ("客户资料", "AI", "行业", "业务链路", "环境", "补料"),
    "environment_diagnosis": ("环境", "URL", "DNS", "HTTP", "认证", "API Smoke", "补料"),
    "business_flow_map": ("业务", "节点", "风险", "覆盖", "证据", "阻断"),
    "test_execution": ("测试计划", "实时", "可执行", "阻断", "探针", "证据"),
    "risk_evidence": ("风险", "证据链", "复现", "业务影响", "修复", "上线"),
    "report_roi": ("领导", "ROI", "上线建议", "执行摘要", "节省工时", "下一步"),
}

CUSTOMER_JOURNEY_KEYWORDS: tuple[str, ...] = (
    "客户资料导入",
    "环境诊断",
    "业务流程地图",
    "AI 测试计划",
    "实时测试执行",
    "风险与证据链",
    "领导层报告",
    "ROI",
)

CUSTOMER_ACTION_KEYWORDS: tuple[str, ...] = (
    "复制",
    "打开",
    "进入",
    "查看",
    "下一步",
    "重新",
    "生成",
)


@dataclass(frozen=True)
class FrontendInteractionCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class FrontendInteractionAcceptanceReport:
    passed: bool
    score: int
    version: str
    hub_dir: str
    output_dir: str
    checks: list[FrontendInteractionCheck] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return redact_value(
            {
                "passed": self.passed,
                "score": self.score,
                "version": self.version,
                "hub_dir": self.hub_dir,
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


def _safe_text(value: Any, default: str = "—") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _scan_files(root: Path, patterns: Sequence[str]) -> list[str]:
    leaks: list[str] = []
    if not root.exists():
        return [f"missing_output_dir:{root}"]
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js", ".json", ".md", ".txt", ".yml", ".yaml"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in patterns:
            if pattern in text:
                leaks.append(f"{path.relative_to(root)} contains forbidden pattern {pattern}")
    return leaks


def scan_frontend_interaction_for_secret_leaks(hub_dir: str | Path) -> list[str]:
    return _scan_files(Path(hub_dir), FORBIDDEN_INTERACTION_PATTERNS)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


def _page_url(spec_key: str) -> str:
    for spec in PAGE_SPECS_V2:
        if spec.key == spec_key:
            return f"{spec.relative_dir}/{spec.entrypoint}"
    return spec_key


def _page_html_path(hub: Path, spec_key: str) -> Path:
    for spec in PAGE_SPECS_V2:
        if spec.key == spec_key:
            return hub / spec.relative_dir / spec.entrypoint
    return hub / spec_key


def _all_page_text(hub: Path) -> str:
    parts = [_read_text(hub / "index.html")]
    for spec in PAGE_SPECS_V2:
        parts.append(_read_text(hub / spec.relative_dir / spec.entrypoint))
    return "\n".join(parts)


def _all_js_text(hub: Path) -> str:
    return "\n".join(_read_text(path) for path in sorted((hub / "assets").glob("*.js")))


def _missing_keywords(text: str, keywords: Sequence[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword not in text]


def validate_frontend_interaction_acceptance(
    hub_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    min_score: int = 90,
) -> FrontendInteractionAcceptanceReport:
    hub = Path(hub_dir)
    output = Path(output_dir) if output_dir is not None else hub
    output.mkdir(parents=True, exist_ok=True)
    checks: list[FrontendInteractionCheck] = []

    def add(key: str, passed: bool, detail: str, severity: str = "critical") -> None:
        checks.append(FrontendInteractionCheck(key=key, passed=passed, detail=detail, severity=severity))

    manifest = _read_json(hub / FRONTEND_HUB_V2_MANIFEST)
    data = _read_json(hub / "data" / "frontend_experience_hub_v2_data.json")
    index_text = _read_text(hub / "index.html")
    all_text = _all_page_text(hub)

    add("hub_manifest", bool(manifest), "Phase105J hub manifest 可读取。" if manifest else "缺少或无法读取 Phase105J hub manifest。")
    add("hub_entrypoint", (hub / "index.html").exists(), "总入口 index.html 存在。" if (hub / "index.html").exists() else "缺少总入口 index.html。")

    required_page_urls = [_page_url(spec.key) for spec in PAGE_SPECS_V2]
    missing_pages = [url for url in required_page_urls if not (hub / url).exists()]
    add("page_files", not missing_pages, "8 个核心页面文件完整。" if not missing_pages else "缺少页面文件：" + ", ".join(missing_pages))

    missing_links = [url for url in required_page_urls if url not in index_text]
    add("navigation_closure", not missing_links, "总入口已包含所有核心页面跳转。" if not missing_links else "缺少导航链接：" + ", ".join(missing_links))

    pages = data.get("pages") if isinstance(data.get("pages"), list) else []
    ready_pages = [page for page in pages if isinstance(page, Mapping) and page.get("status") == "ready"]
    add("data_page_readiness", len(ready_pages) == len(PAGE_SPECS_V2), f"{len(ready_pages)}/{len(PAGE_SPECS_V2)} 个页面数据状态 ready。")

    journey_steps = data.get("journey_steps") if isinstance(data.get("journey_steps"), list) else []
    journey_text = json.dumps(journey_steps, ensure_ascii=False)
    missing_journey = _missing_keywords(journey_text + all_text, CUSTOMER_JOURNEY_KEYWORDS)
    add("customer_journey_copy", not missing_journey, "客户旅程文案覆盖资料、环境、业务、执行、风险、报告和 ROI。" if not missing_journey else "客户旅程缺少关键词：" + ", ".join(missing_journey))

    page_copy_gaps: list[str] = []
    for page_key, keywords in PAGE_INTERACTION_REQUIREMENTS.items():
        path = _page_html_path(hub, page_key)
        text = _read_text(path)
        missing = _missing_keywords(text, keywords)
        if missing:
            page_copy_gaps.append(f"{page_key}:" + "/".join(missing))
    add("page_business_copy", not page_copy_gaps, "所有核心页面均包含业务价值与操作语义文案。" if not page_copy_gaps else "页面文案缺口：" + "; ".join(page_copy_gaps))

    action_missing = _missing_keywords(all_text, CUSTOMER_ACTION_KEYWORDS)
    add("customer_actions", not action_missing, "页面包含复制、打开、进入、查看、下一步、重新、生成等客户动作。" if not action_missing else "客户动作缺失：" + ", ".join(action_missing))

    js_text = _all_js_text(hub)
    has_interactive_js = "addEventListener" in js_text or "onclick" in all_text or "navigator.clipboard" in js_text
    add("interactive_scripts", has_interactive_js, "前端 JS 包含点击/复制等交互脚本。" if has_interactive_js else "未检测到交互脚本。", severity="major")

    phase104_actions_count = 0
    for path in hub.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        text = json.dumps(payload, ensure_ascii=False)
        if "phase104_actions" in text or "/api/v1/" in text:
            phase104_actions_count += 1
    add("api_handoff_actions", phase104_actions_count >= 4, f"检测到 {phase104_actions_count} 个数据文件包含 Phase104 API 交接信息。" if phase104_actions_count >= 4 else f"Phase104 API 交接信息不足：{phase104_actions_count} 个文件。", severity="major")

    next_action_hits = sum(1 for keyword in ("下一步", "补料", "修复", "复验", "上线") if keyword in all_text)
    add("next_step_guidance", next_action_hits >= 4, f"客户下一步动作语义覆盖 {next_action_hits}/5。" if next_action_hits >= 4 else f"客户下一步动作语义不足：{next_action_hits}/5。")

    leaks = scan_frontend_interaction_for_secret_leaks(hub)
    add("redaction_guard", not leaks, "未发现原始 token/cookie/session/client_secret/traceback 泄露。" if not leaks else "发现疑似泄露：" + "; ".join(leaks))

    zip_path = hub / FRONTEND_HUB_V2_ZIP
    if zip_path.exists():
        try:
            with zipfile.ZipFile(zip_path) as archive:
                names = set(archive.namelist())
            missing_zip = [url for url in required_page_urls if url not in names]
            add("zip_navigation_archive", not missing_zip, "Hub V2 zip 归档包含所有核心页面。" if not missing_zip else "zip 缺少页面：" + ", ".join(missing_zip), severity="major")
        except zipfile.BadZipFile:
            add("zip_navigation_archive", False, "Hub V2 zip 归档不可读。", severity="major")
    else:
        add("zip_navigation_archive", True, "未发现 Hub V2 zip，按 validate-only 场景跳过。", severity="minor")

    raw_score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    passed = raw_score >= min_score and all(check.passed for check in checks if check.severity == "critical")
    report = FrontendInteractionAcceptanceReport(
        passed=passed,
        score=raw_score,
        version=PHASE105K_VERSION,
        hub_dir=str(hub),
        output_dir=str(output),
        checks=checks,
        artifacts={
            "hub_manifest": str(hub / FRONTEND_HUB_V2_MANIFEST),
            "hub_entrypoint": str(hub / "index.html"),
            "acceptance_report_json": str(output / FRONTEND_INTERACTION_ACCEPTANCE_JSON),
            "acceptance_report_md": str(output / FRONTEND_INTERACTION_ACCEPTANCE_MD),
        },
    )
    return report


def render_frontend_interaction_acceptance_markdown(report: FrontendInteractionAcceptanceReport) -> str:
    rows = "\n".join(
        f"| {check.key} | {'PASS' if check.passed else 'FAIL'} | {check.severity} | {check.detail} |"
        for check in report.checks
    )
    status = "通过" if report.passed else "未通过"
    return f"""# Phase105K 前端显示层交互验收报告

- 验收状态：{status}
- 验收分数：{report.score}
- 版本：{report.version}
- Hub 目录：`{report.hub_dir}`

## 验收项

| 检查项 | 结果 | 严重级别 | 详情 |
|---|---|---|---|
{rows}

## 验收结论

Phase105K 用于确认 Phase105J 前端体验 Hub V2 不只是“能打开”，还具备客户演示所需的导航闭环、关键动作、业务文案、数据支撑、下一步动作、归档完整性和默认脱敏状态。
"""


def write_frontend_interaction_acceptance_report(
    output_dir: str | Path,
    report: FrontendInteractionAcceptanceReport,
    *,
    hub_dir: str | Path | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report_dict = report.to_dict()
    _write_text(output / FRONTEND_INTERACTION_ACCEPTANCE_JSON, _json_dump(report_dict))
    _write_text(output / FRONTEND_INTERACTION_ACCEPTANCE_MD, render_frontend_interaction_acceptance_markdown(report))
    manifest = redact_value(
        {
            "version": PHASE105K_VERSION,
            "generated_at": _now(),
            "passed": report.passed,
            "score": report.score,
            "hub_dir": str(hub_dir or report.hub_dir),
            "output_dir": str(output),
            "required_outputs": list(REQUIRED_INTERACTION_OUTPUTS),
            "acceptance_report": FRONTEND_INTERACTION_ACCEPTANCE_JSON,
            "acceptance_markdown": FRONTEND_INTERACTION_ACCEPTANCE_MD,
            "redaction_status": "safe" if not scan_frontend_interaction_for_secret_leaks(hub_dir or report.hub_dir) else "leak_detected",
        }
    )
    _write_text(output / FRONTEND_INTERACTION_ACCEPTANCE_MANIFEST, _json_dump(manifest))
    return {"acceptance": report_dict, "manifest": manifest}


def run_frontend_interaction_acceptance(
    *,
    hub_dir: str | Path,
    output_dir: str | Path | None = None,
    build_first: bool = False,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8088",
    min_score: int = 90,
) -> dict[str, Any]:
    hub = Path(hub_dir)
    output = Path(output_dir) if output_dir is not None else hub
    if build_first:
        build_frontend_experience_hub_v2(hub, scenario=scenario, api_base_url=api_base_url)
    report = validate_frontend_interaction_acceptance(hub, output_dir=output, min_score=min_score)
    return write_frontend_interaction_acceptance_report(output, report, hub_dir=hub)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Phase105 frontend display-layer interaction acceptance.")
    parser.add_argument("--hub-dir", default="outputs/phase105_frontend_experience_hub_v2")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--build-first", action="store_true")
    parser.add_argument("--scenario", default="manufacturing")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--min-score", type=int, default=90)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run_frontend_interaction_acceptance(
        hub_dir=args.hub_dir,
        output_dir=args.output_dir,
        build_first=args.build_first,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        min_score=args.min_score,
    )
    print(_json_dump(result["acceptance"]))
    return 0 if result["acceptance"]["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


from __future__ import annotations

"""Phase103Y: delivery bundle acceptance gate.

Phase103X can build a customer-safe delivery bundle that includes static product
prototype pages, page-ready JSON data, preview manifests, acceptance reports,
and commercial handoff material.  This module adds the next release gate: validate
an already-built bundle before it is sent to a customer or used in a sales/demo
handoff.

The validator is intentionally dependency-free and offline-safe.  It checks the
bundle directory and optional zip archive, verifies required scenarios and files,
ensures reports and page data contain business value, and scans the final payload
for known raw secret patterns.
"""

import argparse
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_delivery_bundle import DEFAULT_SCENARIOS, SECRET_GUARD_PATTERNS, build_delivery_bundle
from ai_test_asset_center.phase103_enterprise_command_center import redact_value

PHASE103Y_VERSION = "phase103y-delivery-acceptance-v1"

REQUIRED_COMMERCIAL_FILES: tuple[str, ...] = (
    "commercial/01_one_pager.md",
    "commercial/02_sales_demo_script.md",
    "commercial/03_customer_handoff_checklist.md",
)

REQUIRED_SITE_FILES: tuple[str, ...] = (
    "index.html",
    "dashboard.html",
    "environment.html",
    "test_plan.html",
    "live_map.html",
    "risks.html",
    "report.html",
    "value.html",
    "assets/phase103_ui.css",
    "assets/phase103_demo_data.js",
)

REQUIRED_DATA_FILES: tuple[str, ...] = (
    "project.json",
    "business_model.json",
    "environment_readiness.json",
    "test_plan.json",
    "command_center.json",
    "live_map.json",
    "risks.json",
    "risk_details.json",
    "value_metrics.json",
    "executive_report.json",
    "frontend_pages.json",
    "manifest.json",
)

REQUIRED_REPORT_KEYWORDS: tuple[str, ...] = (
    "上线建议",
    "业务风险",
    "AI",
    "价值",
)

EXTENDED_SECRET_PATTERNS: tuple[str, ...] = SECRET_GUARD_PATTERNS + (
    "Bearer raw",
    "client_secret=",
    "password=",
    "SESSIONID=raw",
)


@dataclass(frozen=True)
class DeliveryAcceptanceCheck:
    """Single delivery acceptance check."""

    key: str
    title: str
    passed: bool
    severity: str = "critical"
    detail: str = ""
    suggested_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "passed": self.passed,
            "severity": self.severity,
            "detail": self.detail,
            "suggested_action": self.suggested_action,
        }


@dataclass
class DeliveryAcceptanceReport:
    """Acceptance report for one Phase103 delivery bundle."""

    bundle_dir: str
    version: str = PHASE103Y_VERSION
    checks: list[DeliveryAcceptanceCheck] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(check.passed or check.severity != "critical" for check in self.checks)

    @property
    def score(self) -> int:
        if not self.checks:
            return 0
        passed_count = sum(1 for check in self.checks if check.passed)
        return int(round((passed_count / len(self.checks)) * 100))

    @property
    def failed_checks(self) -> list[DeliveryAcceptanceCheck]:
        return [check for check in self.checks if not check.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "bundle_dir": self.bundle_dir,
            "passed": self.passed,
            "score": self.score,
            "checks": [check.to_dict() for check in self.checks],
            "failed_checks": [check.to_dict() for check in self.failed_checks],
            "manifest": redact_value(self.manifest),
            "artifacts": redact_value(self.artifacts),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Phase103Y 交付包验收报告",
            "",
            f"- 版本：{self.version}",
            f"- 交付目录：`{self.bundle_dir}`",
            f"- 结论：{'通过' if self.passed else '未通过'}",
            f"- 验收分：{self.score}/100",
            "",
            "## 验收项",
            "",
            "| 项 | 结果 | 严重性 | 说明 |",
            "| --- | --- | --- | --- |",
        ]
        for check in self.checks:
            result = "通过" if check.passed else "未通过"
            detail = check.detail.replace("\n", " ")
            lines.append(f"| {check.title} | {result} | {check.severity} | {detail} |")
        if self.failed_checks:
            lines.extend(["", "## 待处理项", ""])
            for check in self.failed_checks:
                lines.append(f"- **{check.title}**：{check.suggested_action or check.detail or '请检查交付包。'}")
        lines.extend(
            [
                "",
                "## 交付结论",
                "",
                "通过该门禁表示交付包具备客户演示所需的页面、数据、报告、商业材料和脱敏安全基础。",
                "",
            ]
        )
        return "\n".join(lines)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact_value(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def _scan_for_secret_patterns(root: Path, patterns: Sequence[str] = EXTENDED_SECRET_PATTERNS) -> list[str]:
    findings: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg", ".webp"}:
            continue
        text = _safe_read_text(path)
        for pattern in patterns:
            if pattern and pattern in text:
                findings.append(f"{path.relative_to(root)}:{pattern}")
    return findings


def _relative_exists(root: Path, relative: str) -> bool:
    return (root / relative).exists()


def _validate_zip(bundle_dir: Path, manifest: Mapping[str, Any]) -> tuple[bool, str]:
    zip_path_raw = manifest.get("zip_path")
    if not zip_path_raw:
        return True, "manifest 未要求 zip；该项跳过。"
    zip_path = Path(str(zip_path_raw))
    if not zip_path.is_absolute():
        zip_path = bundle_dir.parent / zip_path.name
    if not zip_path.exists():
        return False, f"zip 不存在：{zip_path}"
    required_names = {"delivery_manifest.json", "README_DELIVERY_BUNDLE.md", *REQUIRED_COMMERCIAL_FILES}
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        return False, f"zip 损坏或不可读取：{zip_path}"
    missing = sorted(required_names - names)
    if missing:
        return False, "zip 缺少关键文件：" + ", ".join(missing)
    return True, f"zip 可读取，包含 {len(names)} 个文件。"


def _scenario_entries(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    scenarios = manifest.get("scenarios")
    if isinstance(scenarios, list):
        return [item for item in scenarios if isinstance(item, Mapping)]
    return []


def validate_delivery_bundle(
    bundle_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    min_acceptance_score: int = 90,
    require_zip: bool = False,
) -> dict[str, Any]:
    """Validate a Phase103X delivery bundle and optionally write reports."""
    root = Path(bundle_dir)
    report = DeliveryAcceptanceReport(bundle_dir=str(root))

    manifest_path = root / "delivery_manifest.json"
    if not root.exists():
        report.checks.append(
            DeliveryAcceptanceCheck(
                key="bundle_dir_exists",
                title="交付目录存在",
                passed=False,
                detail=f"目录不存在：{root}",
                suggested_action="请先运行 phase103_delivery_bundle 生成交付包。",
            )
        )
        return report.to_dict()

    report.checks.append(DeliveryAcceptanceCheck("bundle_dir_exists", "交付目录存在", True, detail=str(root)))

    if not manifest_path.exists():
        report.checks.append(
            DeliveryAcceptanceCheck(
                key="manifest_exists",
                title="交付 manifest 存在",
                passed=False,
                detail="缺少 delivery_manifest.json",
                suggested_action="请重新生成交付包。",
            )
        )
        return _finalize_report(report, output_dir)

    manifest = _load_json(manifest_path)
    report.manifest = manifest
    scenarios = _scenario_entries(manifest)

    report.checks.append(DeliveryAcceptanceCheck("manifest_exists", "交付 manifest 存在", True, detail="delivery_manifest.json"))
    report.checks.append(
        DeliveryAcceptanceCheck(
            "manifest_passed",
            "交付 manifest 结论通过",
            bool(manifest.get("passed")) and str(manifest.get("redaction_status")) == "safe",
            detail=f"passed={manifest.get('passed')} redaction_status={manifest.get('redaction_status')}",
            suggested_action="请检查各场景验收报告和脱敏扫描结果。",
        )
    )
    report.checks.append(
        DeliveryAcceptanceCheck(
            "scenario_count",
            "交付场景数量有效",
            len(scenarios) >= 1 and int(manifest.get("scenario_count") or len(scenarios)) == len(scenarios),
            detail=f"场景数：{len(scenarios)}",
            suggested_action="请确认 delivery_manifest.json 中 scenarios 与 scenario_count 一致。",
        )
    )
    report.checks.append(
        DeliveryAcceptanceCheck(
            "average_acceptance_score",
            "平均验收分达标",
            int(manifest.get("average_acceptance_score") or 0) >= min_acceptance_score,
            detail=f"平均分：{manifest.get('average_acceptance_score')}/100，门槛：{min_acceptance_score}/100",
            suggested_action="请运行 Phase103W 验收门禁并修复未通过项。",
        )
    )

    for relative in ("README_DELIVERY_BUNDLE.md", "delivery_manifest.md", *REQUIRED_COMMERCIAL_FILES):
        report.checks.append(
            DeliveryAcceptanceCheck(
                key=f"file:{relative}",
                title=f"关键交付文件存在：{relative}",
                passed=_relative_exists(root, relative),
                detail=relative,
                suggested_action=f"请补齐 {relative}。",
            )
        )

    one_pager = _safe_read_text(root / "commercial" / "01_one_pager.md")
    demo_script = _safe_read_text(root / "commercial" / "02_sales_demo_script.md")
    report.checks.append(
        DeliveryAcceptanceCheck(
            "commercial_language",
            "商业化材料包含核心价值语言",
            all(keyword in (one_pager + demo_script) for keyword in ("质量驾驶舱", "证据链", "ROI", "上线")),
            detail="检查一页纸与演示脚本是否包含质量驾驶舱、证据链、ROI、上线等核心表达。",
            suggested_action="请重新生成商业化材料或补充核心价值话术。",
        )
    )

    for scenario in scenarios:
        _append_scenario_checks(report, root, scenario)

    zip_passed, zip_detail = _validate_zip(root, manifest)
    if require_zip and not manifest.get("zip_path"):
        zip_passed = False
        zip_detail = "要求 zip，但 manifest 未包含 zip_path。"
    report.checks.append(
        DeliveryAcceptanceCheck(
            "zip_archive",
            "交付 zip 归档可用",
            zip_passed,
            severity="critical" if require_zip or manifest.get("zip_path") else "info",
            detail=zip_detail,
            suggested_action="请重新运行交付包生成器并启用 zip 输出。",
        )
    )

    leaks = _scan_for_secret_patterns(root)
    report.artifacts["secret_findings"] = leaks
    report.checks.append(
        DeliveryAcceptanceCheck(
            "redaction_guard",
            "交付包未发现已知原始凭证模式",
            not leaks,
            detail="未发现泄露。" if not leaks else "; ".join(leaks[:10]),
            suggested_action="请删除原始凭证并重新生成脱敏交付包。",
        )
    )

    return _finalize_report(report, output_dir)


def _append_scenario_checks(report: DeliveryAcceptanceReport, root: Path, scenario: Mapping[str, Any]) -> None:
    scenario_name = str(scenario.get("scenario") or "unknown")
    site_dir = root / str(scenario.get("site_dir") or "")
    data_dir = root / str(scenario.get("data_dir") or "")
    acceptance_report = root / str(scenario.get("acceptance_report") or "")

    report.checks.append(
        DeliveryAcceptanceCheck(
            f"scenario:{scenario_name}:acceptance",
            f"场景验收通过：{scenario_name}",
            bool(scenario.get("acceptance_passed")) and int(scenario.get("acceptance_score") or 0) >= 90,
            detail=f"score={scenario.get('acceptance_score')}",
            suggested_action="请查看该场景 acceptance_report.md 并修复未通过项。",
        )
    )

    missing_site = [name for name in REQUIRED_SITE_FILES if not (site_dir / name).exists()]
    report.checks.append(
        DeliveryAcceptanceCheck(
            f"scenario:{scenario_name}:site_files",
            f"静态页面完整：{scenario_name}",
            not missing_site,
            detail="完整" if not missing_site else "缺少：" + ", ".join(missing_site),
            suggested_action="请重新运行静态前端导出器。",
        )
    )

    missing_data = [name for name in REQUIRED_DATA_FILES if not (data_dir / name).exists()]
    report.checks.append(
        DeliveryAcceptanceCheck(
            f"scenario:{scenario_name}:data_files",
            f"前端数据完整：{scenario_name}",
            not missing_data,
            detail="完整" if not missing_data else "缺少：" + ", ".join(missing_data),
            suggested_action="请重新运行 demo runner 或 delivery bundle 生成器。",
        )
    )

    report.checks.append(
        DeliveryAcceptanceCheck(
            f"scenario:{scenario_name}:acceptance_report_file",
            f"验收报告存在：{scenario_name}",
            acceptance_report.exists(),
            detail=str(acceptance_report.relative_to(root)) if acceptance_report.exists() else "缺失",
            suggested_action="请重新运行 Phase103W 验收门禁。",
        )
    )

    _append_data_value_checks(report, data_dir, scenario_name)


def _append_data_value_checks(report: DeliveryAcceptanceReport, data_dir: Path, scenario_name: str) -> None:
    command_center_path = data_dir / "command_center.json"
    risk_details_path = data_dir / "risk_details.json"
    report_path = data_dir / "executive_report.json"
    value_path = data_dir / "value_metrics.json"
    live_map_path = data_dir / "live_map.json"

    try:
        command_center = _load_json(command_center_path)
    except Exception:
        command_center = {}
    try:
        risk_details = _load_json(risk_details_path)
    except Exception:
        risk_details = {}
    try:
        executive_report = _load_json(report_path)
    except Exception:
        executive_report = {}
    try:
        value_metrics = _load_json(value_path)
    except Exception:
        value_metrics = {}
    try:
        live_map = _load_json(live_map_path)
    except Exception:
        live_map = {}

    report.checks.append(
        DeliveryAcceptanceCheck(
            f"scenario:{scenario_name}:command_center_value",
            f"驾驶舱价值数据有效：{scenario_name}",
            bool(command_center.get("quality_health_score")) and bool(command_center.get("top_risks")),
            detail="包含质量分与 Top 风险。" if command_center else "command_center.json 无法读取或为空。",
            suggested_action="请确认测试运行已生成风险和驾驶舱聚合数据。",
        )
    )

    first_detail = None
    if isinstance(risk_details, Mapping):
        values = list(risk_details.values())
        first_detail = values[0] if values and isinstance(values[0], Mapping) else None
    elif isinstance(risk_details, list) and risk_details:
        first_detail = risk_details[0] if isinstance(risk_details[0], Mapping) else None
    risk_payload = first_detail.get("risk") if isinstance(first_detail, Mapping) and isinstance(first_detail.get("risk"), Mapping) else first_detail
    evidence = None
    if isinstance(first_detail, Mapping):
        evidence = first_detail.get("evidence") or first_detail.get("evidence_bundle")
    report.checks.append(
        DeliveryAcceptanceCheck(
            f"scenario:{scenario_name}:risk_evidence",
            f"风险详情含业务影响与证据链：{scenario_name}",
            bool(risk_payload and risk_payload.get("business_impact") and evidence and evidence.get("reproduction_steps")),
            detail="包含业务影响、证据链和复现步骤。" if first_detail else "未找到风险详情。",
            suggested_action="请确认 RiskFinding 与 EvidenceBundle 已生成。",
        )
    )

    executive_text = json.dumps(executive_report, ensure_ascii=False)
    language_ok = "上线" in executive_text and "风险" in executive_text and "AI" in executive_text and ("价值" in executive_text or "节省" in executive_text)
    report.checks.append(
        DeliveryAcceptanceCheck(
            f"scenario:{scenario_name}:executive_report_language",
            f"成果战报可用于领导汇报：{scenario_name}",
            language_ok,
            detail="包含上线、风险、AI 和价值/节省等关键表达。",
            suggested_action="请重新生成 ExecutiveReport 或补充领导层摘要。",
        )
    )

    report.checks.append(
        DeliveryAcceptanceCheck(
            f"scenario:{scenario_name}:roi_metrics",
            f"ROI 指标含计算口径：{scenario_name}",
            bool(value_metrics.get("estimated_hours_saved")) and bool(value_metrics.get("calculation_notes")),
            detail="包含节省工时和计算口径。" if value_metrics else "value_metrics.json 无法读取或为空。",
            suggested_action="请重新计算 QualityValueMetric。",
        )
    )

    report.checks.append(
        DeliveryAcceptanceCheck(
            f"scenario:{scenario_name}:map_risks",
            f"实时地图含节点与风险层：{scenario_name}",
            bool(live_map.get("nodes")) and bool(live_map.get("risk_overlays")),
            detail="包含地图节点和风险覆盖层。" if live_map else "live_map.json 无法读取或为空。",
            suggested_action="请重新生成 RealtimeMapSnapshot。",
        )
    )


def _finalize_report(report: DeliveryAcceptanceReport, output_dir: str | Path | None) -> dict[str, Any]:
    payload = report.to_dict()
    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        _write_json(out / "delivery_acceptance_report.json", payload)
        (out / "delivery_acceptance_report.md").write_text(report.to_markdown(), encoding="utf-8")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Phase103 customer delivery bundle before handoff.")
    parser.add_argument("--bundle-dir", help="Existing delivery bundle directory to validate.")
    parser.add_argument("--output-dir", default=None, help="Optional directory for JSON/Markdown acceptance reports.")
    parser.add_argument("--min-score", type=int, default=90, help="Minimum average/scenario acceptance score.")
    parser.add_argument("--require-zip", action="store_true", help="Fail if the delivery zip archive is missing.")
    parser.add_argument("--build-first", action="store_true", help="Build a delivery bundle before validation.")
    parser.add_argument("--scenario", action="append", choices=sorted(DEFAULT_SCENARIOS), help="Scenario(s) for --build-first.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    bundle_dir = args.bundle_dir
    if args.build_first:
        if not bundle_dir:
            bundle_dir = "outputs/phase103_delivery_bundle"
        build_delivery_bundle(scenarios=tuple(args.scenario or DEFAULT_SCENARIOS), output_dir=bundle_dir, create_zip=True)
    if not bundle_dir:
        parser.error("--bundle-dir is required unless --build-first is used")

    report = validate_delivery_bundle(
        bundle_dir,
        output_dir=args.output_dir,
        min_acceptance_score=args.min_score,
        require_zip=args.require_zip,
    )
    print(json.dumps({"passed": report["passed"], "score": report["score"], "failed_checks": len(report["failed_checks"])}, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


__all__ = [
    "DeliveryAcceptanceCheck",
    "DeliveryAcceptanceReport",
    "PHASE103Y_VERSION",
    "REQUIRED_COMMERCIAL_FILES",
    "REQUIRED_DATA_FILES",
    "REQUIRED_SITE_FILES",
    "validate_delivery_bundle",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

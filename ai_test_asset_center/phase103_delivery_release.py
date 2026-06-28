from __future__ import annotations

"""Phase103Z: delivery release ledger and checksum attestation.

Phase103X builds a customer-safe delivery bundle and Phase103Y validates that
bundle before handoff.  This module adds a release ledger layer for the final
handoff step: create checksums, customer release notes, a receipt, and a
machine-readable manifest that can be verified after files are copied or zipped.

The release ledger is deliberately offline-safe and dependency-free.  It does
not sign files cryptographically, but it gives product, sales, QA, and customer
success teams a tamper-evident SHA256 ledger and a clear customer-facing release
record.
"""

import argparse
import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_delivery_acceptance import validate_delivery_bundle
from ai_test_asset_center.phase103_delivery_bundle import DEFAULT_SCENARIOS, SECRET_GUARD_PATTERNS, build_delivery_bundle
from ai_test_asset_center.phase103_enterprise_command_center import redact_value

PHASE103Z_VERSION = "phase103z-delivery-release-ledger-v1"

RELEASE_REQUIRED_FILES: tuple[str, ...] = (
    "release_manifest.json",
    "release_manifest.md",
    "CHECKSUMS.sha256",
    "CUSTOMER_RELEASE_NOTES.md",
    "RELEASE_RECEIPT.md",
)

RELEASE_SECRET_PATTERNS: tuple[str, ...] = SECRET_GUARD_PATTERNS + (
    "Bearer raw",
    "client_secret=",
    "password=",
    "SESSIONID=raw",
)

SKIP_CHECKSUM_SUFFIXES: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp")


@dataclass(frozen=True)
class ReleaseArtifact:
    """One file included in a release ledger."""

    path: str
    sha256: str
    size_bytes: int
    category: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "category": self.category,
        }


@dataclass(frozen=True)
class ReleaseCheck:
    """Single release verification check."""

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
class DeliveryReleaseLedger:
    """Customer handoff release ledger."""

    bundle_dir: str
    release_name: str
    version: str = PHASE103Z_VERSION
    acceptance_passed: bool = False
    acceptance_score: int = 0
    release_status: str = "pending"
    artifacts: list[ReleaseArtifact] = field(default_factory=list)
    checks: list[ReleaseCheck] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.acceptance_passed and all(check.passed or check.severity != "critical" for check in self.checks)

    @property
    def artifact_count(self) -> int:
        return len(self.artifacts)

    @property
    def total_size_bytes(self) -> int:
        return sum(item.size_bytes for item in self.artifacts)

    @property
    def failed_checks(self) -> list[ReleaseCheck]:
        return [check for check in self.checks if not check.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "release_name": self.release_name,
            "bundle_dir": self.bundle_dir,
            "release_status": "passed" if self.passed else "failed",
            "passed": self.passed,
            "acceptance_passed": self.acceptance_passed,
            "acceptance_score": self.acceptance_score,
            "artifact_count": self.artifact_count,
            "total_size_bytes": self.total_size_bytes,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "checks": [check.to_dict() for check in self.checks],
            "failed_checks": [check.to_dict() for check in self.failed_checks],
            "notes": list(self.notes),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Phase103Z 交付发布账本",
            "",
            f"- 版本：{self.version}",
            f"- 发布名称：{self.release_name}",
            f"- 交付目录：`{self.bundle_dir}`",
            f"- 结论：{'通过' if self.passed else '未通过'}",
            f"- 验收分：{self.acceptance_score}/100",
            f"- 归档文件数：{self.artifact_count}",
            f"- 归档总大小：{self.total_size_bytes} bytes",
            "",
            "## 发布检查",
            "",
            "| 项 | 结果 | 严重性 | 说明 |",
            "| --- | --- | --- | --- |",
        ]
        for check in self.checks:
            result = "通过" if check.passed else "未通过"
            lines.append(f"| {check.title} | {result} | {check.severity} | {check.detail.replace('|', '/')} |")
        lines.extend(["", "## 关键交付物", ""])
        for artifact in self.artifacts[:30]:
            lines.append(f"- `{artifact.path}` ({artifact.category}, {artifact.size_bytes} bytes)")
        if len(self.artifacts) > 30:
            lines.append(f"- 其余 {len(self.artifacts) - 30} 个文件见 `release_manifest.json`。")
        if self.failed_checks:
            lines.extend(["", "## 待处理项", ""])
            for check in self.failed_checks:
                lines.append(f"- **{check.title}**：{check.suggested_action or check.detail or '请检查发布包。'}")
        lines.extend(
            [
                "",
                "## 安全说明",
                "",
                "本发布账本仅记录脱敏后的交付物校验信息，不包含 token、cookie、password、session 或 client_secret 原值。",
                "",
            ]
        )
        return "\n".join(lines)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any, *, redact: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_payload = redact_value(payload) if redact else payload
    path.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def _classify_artifact(relative_path: str) -> str:
    if relative_path.startswith("commercial/"):
        return "commercial_material"
    if "/site/" in relative_path or relative_path.endswith(".html") or relative_path.endswith(".css") or relative_path.endswith(".js"):
        return "static_frontend"
    if "/data/" in relative_path or relative_path.endswith(".json"):
        return "frontend_data"
    if "acceptance_report" in relative_path:
        return "acceptance_report"
    if relative_path.endswith(".zip"):
        return "zip_archive"
    if relative_path.endswith(".md"):
        return "handoff_document"
    return "other"


def _iter_bundle_files(bundle_dir: Path, release_dir: Path | None = None) -> list[Path]:
    files: list[Path] = []
    release_resolved = release_dir.resolve() if release_dir is not None and release_dir.exists() else None
    for path in sorted(bundle_dir.rglob("*")):
        if not path.is_file():
            continue
        if release_resolved is not None:
            try:
                path.resolve().relative_to(release_resolved)
                continue
            except ValueError:
                pass
        if path.suffix.lower() in SKIP_CHECKSUM_SUFFIXES:
            continue
        files.append(path)
    zip_path = bundle_dir.with_suffix(".zip")
    if zip_path.exists():
        files.append(zip_path)
    return files


def _scan_files_for_secret_patterns(files: Sequence[Path], root: Path) -> list[str]:
    findings: list[str] = []
    for path in files:
        if path.suffix.lower() in {".zip", ".png", ".jpg", ".jpeg", ".webp"}:
            continue
        text = _safe_read_text(path)
        for pattern in RELEASE_SECRET_PATTERNS:
            if pattern and pattern in text:
                try:
                    rel = str(path.relative_to(root))
                except ValueError:
                    rel = path.name
                findings.append(f"{rel}:{pattern}")
    return findings


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_checksums(path: Path, artifacts: Sequence[ReleaseArtifact]) -> None:
    lines = [f"{artifact.sha256}  {artifact.path}" for artifact in artifacts]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _customer_release_notes(ledger: DeliveryReleaseLedger) -> str:
    lines = [
        "# QualiBug Phase103 客户交付发布说明",
        "",
        f"- 发布名称：{ledger.release_name}",
        f"- 发布结论：{'通过，可交付' if ledger.passed else '未通过，暂不建议交付'}",
        f"- 交付验收分：{ledger.acceptance_score}/100",
        f"- 交付文件数：{ledger.artifact_count}",
        "",
        "## 本次交付包含",
        "",
        "- 企业质量驾驶舱静态原型。",
        "- 客户环境适配中心、AI 测试计划、实时地图、风险发现、成果战报和 ROI 页面。",
        "- 多行业场景数据与脱敏证据链。",
        "- 售前演示脚本、商业化一页纸和客户交接清单。",
        "- 交付包验收报告与 SHA256 校验账本。",
        "",
        "## 使用建议",
        "",
        "1. 先阅读 `README_DELIVERY_BUNDLE.md`。",
        "2. 打开 `scenarios/<scenario>/site/index.html` 查看静态原型。",
        "3. 查看 `delivery_acceptance_report.md` 与本目录下的 `CHECKSUMS.sha256` 确认交付完整性。",
        "4. 对外演示前优先使用制造、电商或 SaaS 场景中的一个完整故事线。",
        "",
        "## 安全说明",
        "",
        "本交付包已经通过脱敏门禁，不应包含 token、cookie、password、session、client_secret 原值或客户敏感业务数据原文。",
        "",
    ]
    return "\n".join(lines)


def _release_receipt(ledger: DeliveryReleaseLedger) -> str:
    return "\n".join(
        [
            "# Phase103 交付发布签收单",
            "",
            f"- 发布名称：{ledger.release_name}",
            f"- 发布状态：{'通过' if ledger.passed else '未通过'}",
            f"- 验收分：{ledger.acceptance_score}/100",
            f"- 归档文件数：{ledger.artifact_count}",
            f"- 总大小：{ledger.total_size_bytes} bytes",
            "",
            "## 签收确认",
            "",
            "- 交付方确认：已完成页面、数据、报告、商业材料和脱敏门禁检查。",
            "- 接收方确认：已收到交付包与校验账本。",
            "- 校验方式：使用 `CHECKSUMS.sha256` 和 `release_manifest.json` 核对文件完整性。",
            "",
        ]
    )


def build_delivery_release(
    bundle_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    release_name: str = "QualiBug_Phase103_Enterprise_Command_Center_Release",
    min_acceptance_score: int = 90,
    require_zip: bool = True,
) -> dict[str, Any]:
    """Build a release ledger for a Phase103 delivery bundle."""
    root = Path(bundle_dir)
    release_dir = Path(output_dir) if output_dir is not None else root / "release"
    release_dir.mkdir(parents=True, exist_ok=True)

    acceptance_report = validate_delivery_bundle(
        root,
        output_dir=release_dir / "delivery_acceptance",
        min_acceptance_score=min_acceptance_score,
        require_zip=require_zip,
    )

    files = _iter_bundle_files(root, release_dir)
    artifacts = [
        ReleaseArtifact(
            path=str(path.relative_to(root)) if path.is_relative_to(root) else path.name,
            sha256=_sha256_file(path),
            size_bytes=path.stat().st_size,
            category=_classify_artifact(str(path.relative_to(root)) if path.is_relative_to(root) else path.name),
        )
        for path in files
    ]

    checks = [
        ReleaseCheck(
            key="acceptance_gate",
            title="交付包验收门禁",
            passed=bool(acceptance_report.get("passed")) and int(acceptance_report.get("score", 0)) >= min_acceptance_score,
            detail=f"验收分 {acceptance_report.get('score', 0)}/100。",
            suggested_action="先修复 Phase103Y 交付包验收失败项，再重新生成发布账本。",
        ),
        ReleaseCheck(
            key="manifest_exists",
            title="交付 Manifest 存在",
            passed=(root / "delivery_manifest.json").exists(),
            detail="delivery_manifest.json 已找到。" if (root / "delivery_manifest.json").exists() else "缺少 delivery_manifest.json。",
            suggested_action="重新执行 Phase103X 交付包生成器。",
        ),
        ReleaseCheck(
            key="artifact_checksums",
            title="交付物校验和",
            passed=bool(artifacts),
            detail=f"已记录 {len(artifacts)} 个交付文件 SHA256。",
            suggested_action="确认交付包目录非空并重新生成。",
        ),
    ]

    secret_findings = _scan_files_for_secret_patterns(files, root)
    checks.append(
        ReleaseCheck(
            key="redaction_guard",
            title="敏感凭证泄露扫描",
            passed=not secret_findings,
            detail="未发现原始凭证模式。" if not secret_findings else "; ".join(secret_findings[:10]),
            suggested_action="移除 token/cookie/password/session/client_secret 原值后重新打包。",
        )
    )

    ledger = DeliveryReleaseLedger(
        bundle_dir=str(root),
        release_name=release_name,
        acceptance_passed=bool(acceptance_report.get("passed")),
        acceptance_score=int(acceptance_report.get("score", 0)),
        artifacts=artifacts,
        checks=checks,
        notes=["Release ledger generated after Phase103Y delivery acceptance."],
    )

    _write_json(release_dir / "release_manifest.json", ledger.to_dict(), redact=False)
    (release_dir / "release_manifest.md").write_text(ledger.to_markdown(), encoding="utf-8")
    _write_checksums(release_dir / "CHECKSUMS.sha256", artifacts)
    (release_dir / "CUSTOMER_RELEASE_NOTES.md").write_text(_customer_release_notes(ledger), encoding="utf-8")
    (release_dir / "RELEASE_RECEIPT.md").write_text(_release_receipt(ledger), encoding="utf-8")
    return ledger.to_dict()


def verify_delivery_release(bundle_dir: str | Path, release_dir: str | Path) -> dict[str, Any]:
    """Verify a previously generated release ledger."""
    root = Path(bundle_dir)
    release_root = Path(release_dir)
    checks: list[ReleaseCheck] = []
    manifest_path = release_root / "release_manifest.json"

    missing_release_files = [name for name in RELEASE_REQUIRED_FILES if not (release_root / name).exists()]
    checks.append(
        ReleaseCheck(
            key="release_files",
            title="发布账本文件完整性",
            passed=not missing_release_files,
            detail="发布账本文件完整。" if not missing_release_files else "缺少：" + ", ".join(missing_release_files),
            suggested_action="重新执行 Phase103Z 发布账本生成器。",
        )
    )

    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = _load_json(manifest_path)
        except json.JSONDecodeError:
            checks.append(
                ReleaseCheck(
                    key="manifest_parse",
                    title="发布 Manifest 可解析",
                    passed=False,
                    detail="release_manifest.json 不是合法 JSON。",
                    suggested_action="重新生成发布账本。",
                )
            )
    artifacts = manifest.get("artifacts", []) if isinstance(manifest, Mapping) else []
    checksum_failures: list[str] = []
    for artifact in artifacts if isinstance(artifacts, list) else []:
        if not isinstance(artifact, Mapping):
            continue
        rel = str(artifact.get("path", ""))
        expected = str(artifact.get("sha256", ""))
        if not rel or not expected:
            checksum_failures.append(rel or "<missing-path>")
            continue
        candidate = root / rel
        if not candidate.exists() and rel.endswith(".zip"):
            candidate = root.with_suffix(".zip")
        if not candidate.exists():
            checksum_failures.append(f"{rel}:missing")
            continue
        actual = _sha256_file(candidate)
        if actual != expected:
            checksum_failures.append(f"{rel}:checksum-mismatch")
    checks.append(
        ReleaseCheck(
            key="checksum_verify",
            title="SHA256 完整性校验",
            passed=not checksum_failures and bool(artifacts),
            detail="所有交付物校验通过。" if not checksum_failures and artifacts else "; ".join(checksum_failures[:10]) or "未找到 artifacts。",
            suggested_action="确认交付包未被修改；如为正常变更，请重新生成发布账本。",
        )
    )

    acceptance_passed = bool(manifest.get("acceptance_passed")) if isinstance(manifest, Mapping) else False
    checks.append(
        ReleaseCheck(
            key="acceptance_in_manifest",
            title="验收结论已入账",
            passed=acceptance_passed,
            detail=f"acceptance_passed={acceptance_passed}",
            suggested_action="先通过 Phase103Y 交付包验收。",
        )
    )

    passed = all(check.passed or check.severity != "critical" for check in checks)
    report = {
        "version": PHASE103Z_VERSION,
        "bundle_dir": str(root),
        "release_dir": str(release_root),
        "passed": passed,
        "checks": [check.to_dict() for check in checks],
        "failed_checks": [check.to_dict() for check in checks if not check.passed],
    }
    _write_json(release_root / "release_verification_report.json", report)
    (release_root / "release_verification_report.md").write_text(_verification_markdown(report), encoding="utf-8")
    return report


def _verification_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Phase103Z 发布账本复验报告",
        "",
        f"- 结论：{'通过' if report.get('passed') else '未通过'}",
        f"- 交付目录：`{report.get('bundle_dir')}`",
        f"- 发布目录：`{report.get('release_dir')}`",
        "",
        "## 复验项",
        "",
        "| 项 | 结果 | 说明 |",
        "| --- | --- | --- |",
    ]
    for check in report.get("checks", []):
        if not isinstance(check, Mapping):
            continue
        lines.append(f"| {check.get('title')} | {'通过' if check.get('passed') else '未通过'} | {str(check.get('detail', '')).replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or verify a Phase103 delivery release ledger.")
    parser.add_argument("--bundle-dir", required=True, help="Existing Phase103X delivery bundle directory.")
    parser.add_argument("--output-dir", default=None, help="Release ledger output directory. Defaults to <bundle>/release.")
    parser.add_argument("--release-name", default="QualiBug_Phase103_Enterprise_Command_Center_Release")
    parser.add_argument("--min-acceptance-score", type=int, default=90)
    parser.add_argument("--no-require-zip", action="store_true", help="Do not require the delivery zip during acceptance.")
    parser.add_argument("--build-first", action="store_true", help="Build the delivery bundle first before release ledger generation.")
    parser.add_argument("--scenario", action="append", choices=DEFAULT_SCENARIOS, help="Scenario(s) used with --build-first.")
    parser.add_argument("--verify", action="store_true", help="Verify an existing release ledger instead of building it.")
    args = parser.parse_args(argv)

    bundle_dir = Path(args.bundle_dir)
    output_dir = Path(args.output_dir) if args.output_dir else bundle_dir / "release"
    if args.build_first:
        build_delivery_bundle(scenarios=tuple(args.scenario or DEFAULT_SCENARIOS), output_dir=bundle_dir)
    if args.verify:
        report = verify_delivery_release(bundle_dir, output_dir)
    else:
        report = build_delivery_release(
            bundle_dir,
            output_dir=output_dir,
            release_name=args.release_name,
            min_acceptance_score=args.min_acceptance_score,
            require_zip=not args.no_require_zip,
        )
    print(json.dumps(redact_value(report), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

"""Data-driven adaptive strategy projection.

Runtime policy may consume only evaluator-approved strategy metadata. Concrete
routes and benchmark template answers are intentionally excluded; binding to
real operations happens from current project facts.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .artifact_redactor import write_json_redacted


SEVERITY_WEIGHT = {"P0": 1.0, "P1": 0.82, "P2": 0.55, "P3": 0.35}
PRIVATE_KEYS = {
    "bug_id",
    "bug_instance_id",
    "trigger_condition",
    "actual_bug_behavior",
    "enabled_bugs",
    "ground_truth_bugs",
    "current_bug_set",
}
FORBIDDEN_RUNTIME_FIELDS = {
    "path",
    "api_template",
    "endpoint",
    "ground_truth",
    "ground_truth_path",
}
SAFE_STRATEGY_FIELDS = (
    "strategy_id",
    "template_id",
    "risk_type",
    "severity",
    "probe_type",
    "actor",
    "method",
    "expected_status",
    "strategy",
    "priority_score",
    "recommended_variants",
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _empty_policy(project: str) -> dict[str, Any]:
    return {
        "schema_version": "qualibug.strategy-bundle-projection.v1",
        "project": project,
        "measurement_status": "NOT_MEASURED",
        "private_answers_allowed": False,
        "contains_instance_answers": False,
        "template_policies": [],
        "source_receipt": None,
    }


def validate_policy_is_safe(policy: dict[str, Any]) -> None:
    text = json.dumps(policy, ensure_ascii=False).lower()
    for token in PRIVATE_KEYS:
        if token in text:
            raise ValueError(f"Unsafe adaptive policy contains private token: {token}")
    for row in policy.get("template_policies", []):
        if not isinstance(row, dict):
            raise ValueError("Adaptive strategy row must be an object")
        forbidden = FORBIDDEN_RUNTIME_FIELDS.intersection(row)
        if forbidden:
            raise ValueError(
                "Adaptive strategy row contains runtime-forbidden fields: "
                + ",".join(sorted(forbidden))
            )


def recommended_variants(priority: float, missed_count: int) -> int:
    if priority >= 0.85 or missed_count >= 10:
        return 3
    if priority >= 0.65 or missed_count >= 4:
        return 2
    return 1


def build_learned_probe_policy(
    root: Path = Path("."),
    project: str = "project",
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Load an explicit evaluator receipt projection; never mine benchmark files."""
    policy = _empty_policy(project)
    bundle_path = (
        Path(root)
        / "platform_workspace"
        / project
        / "discovery_evaluation"
        / "strategy_bundle.json"
    )
    if bundle_path.exists():
        bundle = read_json(bundle_path)
        if not isinstance(bundle, dict):
            raise ValueError("Strategy bundle must be a JSON object")
        rows = bundle.get("strategy_bundle") or bundle.get("template_policies") or []
        if not isinstance(rows, list):
            raise ValueError("Strategy bundle rows must be a list")
        projected: list[dict[str, Any]] = []
        for raw in rows:
            if not isinstance(raw, dict):
                raise ValueError("Strategy bundle row must be an object")
            forbidden = FORBIDDEN_RUNTIME_FIELDS.intersection(raw)
            if forbidden:
                raise ValueError(
                    "Strategy bundle contains forbidden binding data: "
                    + ",".join(sorted(forbidden))
                )
            row = {key: raw[key] for key in SAFE_STRATEGY_FIELDS if key in raw}
            if not str(row.get("risk_type") or "").strip():
                continue
            row["priority_score"] = max(
                0.0,
                min(1.0, float(row.get("priority_score") or 0.0)),
            )
            row["recommended_variants"] = max(
                1,
                min(3, int(row.get("recommended_variants") or 1)),
            )
            projected.append(row)
        policy.update({
            "measurement_status": str(bundle.get("measurement_status") or "NOT_MEASURED"),
            "template_policies": projected,
            "source_receipt": bundle.get("evaluator_receipt_id"),
        })
    validate_policy_is_safe(policy)
    if output_path is not None:
        write_json_redacted(Path(output_path), policy)
    return policy


def build_adaptive_probe_plan(
    findings: list[dict[str, Any]],
    *,
    base_url: str = "",
    max_probes: int = 50,
) -> list[dict[str, Any]]:
    """Prioritize source-bound finding identities without injecting route recipes."""
    del base_url
    probes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    severity_weight = {"P0": 3.0, "P1": 2.0, "P2": 1.0, "P3": 0.5}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        method = str(finding.get("method") or "").upper()
        path = str(finding.get("path") or "")
        risk_type = str(finding.get("risk_type") or finding.get("category") or "")
        if not method or not path or not risk_type:
            continue
        key = (method, path, risk_type)
        if key in seen:
            continue
        seen.add(key)
        severity = str(finding.get("severity") or "P2")
        confidence = max(0.0, min(1.0, float(finding.get("confidence_score") or 0.0)))
        probes.append({
            "id": f"ADAPT-{len(probes)}",
            "finding_id": str(finding.get("finding_id") or finding.get("id") or ""),
            "method": method,
            "path": path,
            "expected_status": finding.get("expected_status"),
            "actor": str(finding.get("actor") or ""),
            "severity": severity,
            "risk_type": risk_type,
            "strategy": "source_finding_replay",
            "priority": severity_weight.get(severity, 1.0) * confidence,
            "execution_status": "not_executed",
        })
    probes.sort(key=lambda row: (-float(row["priority"]), row["id"]))
    return probes[:max(0, int(max_probes))]


def main() -> int:
    policy = build_learned_probe_policy(Path("."))
    print(json.dumps({
        "schema_version": policy["schema_version"],
        "strategies": len(policy["template_policies"]),
        "measurement_status": policy["measurement_status"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

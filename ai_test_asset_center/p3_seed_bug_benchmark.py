from __future__ import annotations

"""P3 seed-bug benchmark evaluator.

The evaluator measures whether a customer-scenario run exposed known seeded
business defects. It intentionally works from customer-safe HTTP observations
rather than test internals, so it can be used with V12 auto HAR, local fake
customer systems, or later real customer-approved benchmark runs.
"""

import json
from typing import Any
from urllib.parse import urlparse


_SUCCESS_MIN = 200
_SUCCESS_MAX = 399


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _json_or_value(value: Any) -> Any:
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return value
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except Exception:
        return text


def _path(url_or_path: str) -> str:
    text = str(url_or_path or "")
    parsed = urlparse(text)
    return parsed.path or text


def _normalize_observation(row: dict[str, Any]) -> dict[str, Any]:
    request = _as_dict(row.get("request"))
    response = _as_dict(row.get("response"))
    content = _as_dict(response.get("content"))
    return {
        "method": str(row.get("method") or request.get("method") or "").upper(),
        "path": _path(str(row.get("path") or request.get("url") or "")),
        "status": int(row.get("status") or response.get("status") or 0),
        "body": _json_or_value(row.get("body") if "body" in row else content.get("text")),
        "source": str(row.get("source") or "auto_har"),
    }


def extract_http_observations(scan_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract normalized observations from a scan result or raw HAR-like payload."""
    if isinstance(scan_result, list):
        return [_normalize_observation(item) for item in scan_result if isinstance(item, dict)]
    payload = _as_dict(scan_result)
    observations: list[dict[str, Any]] = []
    auto_har = _as_dict(payload.get("auto_har"))
    for row in _as_list(auto_har.get("entries")):
        if isinstance(row, dict):
            observations.append(_normalize_observation(row))
    for row in _as_list(payload.get("http_observations")):
        if isinstance(row, dict):
            observations.append(_normalize_observation(row))
    return observations


def _segments(value: str) -> list[str]:
    return [part for part in str(value or "").strip("/").split("/") if part]


def _match_path(pattern: str, path: str) -> bool:
    expected, actual = _segments(pattern), _segments(path)
    if expected == actual:
        return True
    if len(expected) != len(actual):
        return False
    for left, right in zip(expected, actual):
        if left.startswith("{") and left.endswith("}"):
            continue
        if left != right:
            return False
    return True


def _field(body: Any, path: str) -> Any:
    current = body
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _matching_observations(seed: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    method = str(seed.get("method") or "").upper()
    path = str(seed.get("path") or "")
    return [
        row for row in observations
        if (not method or row.get("method") == method) and (not path or _match_path(path, str(row.get("path") or "")))
    ]


def _detect_seed(seed: dict[str, Any], observations: list[dict[str, Any]]) -> tuple[bool, dict[str, Any]]:
    kind = str(seed.get("kind") or "").strip()
    matches = _matching_observations(seed, observations)
    evidence = {"matched_calls": len(matches), "sample": matches[:3]}
    if not matches:
        return False, evidence

    if kind == "should_reject_but_succeeded":
        found = any(_SUCCESS_MIN <= int(row.get("status") or 0) <= _SUCCESS_MAX for row in matches)
        return found, evidence

    if kind == "unexpected_server_error":
        found = any(int(row.get("status") or 0) >= 500 for row in matches)
        return found, evidence

    if kind == "status_mismatch":
        expected = {int(value) for value in _as_list(seed.get("expected_statuses")) if str(value).isdigit()}
        if not expected and str(seed.get("expected_status") or "").isdigit():
            expected.add(int(seed["expected_status"]))
        found = any(int(row.get("status") or 0) not in expected for row in matches) if expected else False
        return found, evidence

    if kind == "field_equals_forbidden_value":
        field_path = str(seed.get("field") or "")
        forbidden = seed.get("forbidden_value")
        found = any(_field(row.get("body"), field_path) == forbidden for row in matches)
        return found, evidence

    if kind == "field_mismatch":
        left, right = str(seed.get("left") or ""), str(seed.get("right") or "")
        found = any(_field(row.get("body"), left) != _field(row.get("body"), right) for row in matches)
        return found, evidence

    return False, {**evidence, "unsupported_kind": kind}


def evaluate_seed_bug_benchmark(scan_result: dict[str, Any] | list[dict[str, Any]], seed_defects: list[dict[str, Any]]) -> dict[str, Any]:
    observations = extract_http_observations(scan_result)
    findings: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []
    for seed in seed_defects:
        if not isinstance(seed, dict):
            continue
        found, evidence = _detect_seed(seed, observations)
        row = {
            "seed_id": str(seed.get("id") or ""),
            "title": str(seed.get("title") or seed.get("id") or "seed defect"),
            "kind": str(seed.get("kind") or ""),
            "severity": str(seed.get("severity") or "P2"),
            "method": str(seed.get("method") or ""),
            "path": str(seed.get("path") or ""),
            "evidence": evidence,
        }
        if found:
            findings.append({**row, "status": "found", "source": "p3_seed_bug_benchmark"})
        else:
            missed.append({**row, "status": "missed"})
    total = len(findings) + len(missed)
    detection_rate = (len(findings) / total) if total else 0.0

    # ── P6: Recall by unique bug type (deduplicate shared bug categories) ──
    # A single seeded bug may be detected by multiple oracles (e.g., HttpStatusOracle
    # AND SchemaOracle both flag the same defect). The original detection_rate counts
    # each oracle hit separately, which can produce recall > 1.0.
    # Corrected recall counts unique bug types detected vs total unique types seeded.
    unique_seed_types = sorted(set(
        str(seed.get("bug_type") or seed.get("kind") or seed.get("title") or "")
        for seed in seed_defects if isinstance(seed, dict)
    ))
    unique_found_types = sorted(set(
        str(seed.get("bug_type") or seed.get("kind") or seed.get("title") or "")
        for seed in seed_defects
        if isinstance(seed, dict)
        and any(
            f["seed_id"] == str(seed.get("id") or "")
            for f in findings
        )
    ))
    unique_normalized_types = sorted(set(
        _normalize_bug_type(str(seed.get("bug_type") or seed.get("kind") or ""))
        for seed in seed_defects if isinstance(seed, dict)
    ))
    unique_found_normalized = sorted(set(
        _normalize_bug_type(str(seed.get("bug_type") or seed.get("kind") or ""))
        for seed in seed_defects
        if isinstance(seed, dict)
        and any(
            f["seed_id"] == str(seed.get("id") or "")
            for f in findings
        )
    ))
    raw_recall = round(len(findings) / total, 4) if total else 0.0
    corrected_recall = round(len(unique_found_normalized) / len(unique_normalized_types), 4) if unique_normalized_types else 0.0

    # ── P6: False negative rate ──
    # Missed defects: seeded bugs that were NOT detected by ANY oracle.
    missed_bug_types = sorted(set(
        _normalize_bug_type(str(seed.get("bug_type") or seed.get("kind") or ""))
        for seed in seed_defects
        if isinstance(seed, dict)
        and all(
            f["seed_id"] != str(seed.get("id") or "")
            for f in findings
        )
    ))
    false_negative_rate = round(len(missed_bug_types) / len(unique_normalized_types), 4) if unique_normalized_types else 0.0

    return {
        "schema_version": "p3-seed-bug-benchmark-v1",
        "total_seed_defects": total,
        "found_count": len(findings),
        "missed_count": len(missed),
        "detection_rate": detection_rate,
        "observed_http_calls": len(observations),
        "findings": findings,
        "missed": missed,
        "grade": "passed" if total and len(findings) == total else ("partial" if findings else "failed"),
        # P6 corrected metrics
        "p6_corrected_metrics": {
            "raw_recall": raw_recall,
            "corrected_recall": corrected_recall,
            "total_unique_bug_types": len(unique_normalized_types),
            "unique_bug_types_detected": len(unique_found_normalized),
            "unique_bug_types_missed": missed_bug_types,
            "false_negative_rate": false_negative_rate,
            "note": "corrected_recall uses unique bug-type deduplication to avoid recall > 1.0 from multi-oracle detection of the same defect.",
        },
        # ── P3+: Invariant-level benchmark metrics ──
        "p3_invariant_metrics": _compute_invariant_benchmark(findings, seed_defects, observations),
    }


def _compute_invariant_benchmark(
    findings: list[dict[str, Any]],
    seed_defects: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute per-invariant-type recall, precision, FPR, FNR, evidence completeness.

    Maps each seeded defect to its risk family and invariant type using the
    Bug Ontology Registry, then computes per-invariant benchmark metrics.
    """
    # Try to load ontology for invariant mapping
    invariant_map: dict[str, str] = {}  # bug_type → invariant_type
    family_map: dict[str, str] = {}     # bug_type → risk_family
    try:
        from .bug_ontology_registry import get_ontology_registry
        registry = get_ontology_registry()
        for entry in registry.list_entries():
            invariant_map[entry.subtype] = entry.invariant_type
            family_map[entry.subtype] = entry.family_id
            # Also map normalized types
            norm = _normalize_bug_type(entry.subtype)
            if norm not in invariant_map:
                invariant_map[norm] = entry.invariant_type
                family_map[norm] = entry.family_id
    except ImportError:
        pass

    # Group seeds by invariant type
    by_invariant: dict[str, dict[str, Any]] = {}
    for seed in seed_defects:
        if not isinstance(seed, dict):
            continue
        bug_type = str(seed.get("bug_type") or seed.get("kind") or "")
        norm = _normalize_bug_type(bug_type)
        inv_type = invariant_map.get(norm, invariant_map.get(bug_type, "unknown"))
        family = family_map.get(norm, family_map.get(bug_type, "unknown"))
        seed_id = str(seed.get("id") or "")

        entry = by_invariant.setdefault(inv_type, {
            "invariant_type": inv_type,
            "risk_family": family,
            "total": 0,
            "detected": 0,
            "missed": [],
            "detected_ids": [],
        })
        entry["total"] += 1

        # Check if detected
        found = any(f.get("seed_id") == seed_id for f in findings)
        if found:
            entry["detected"] += 1
            entry["detected_ids"].append(seed_id)
        else:
            entry["missed"].append(seed_id)

    # Build per-invariant metrics
    per_invariant: dict[str, dict[str, Any]] = {}
    for inv_type, entry in by_invariant.items():
        total = entry["total"]
        detected = entry["detected"]
        recall = round(detected / total, 4) if total else 0.0
        per_invariant[inv_type] = {
            "invariant_type": inv_type,
            "risk_family": entry["risk_family"],
            "total_seeds": total,
            "detected": detected,
            "missed": len(entry["missed"]),
            "recall": recall,
            "false_negative_rate": round((total - detected) / total, 4) if total else 0.0,
        }

    # Evidence completeness per invariant
    evidence_by_invariant: dict[str, dict[str, int]] = {}
    for f in findings:
        evidence = f.get("evidence", {})
        inv_results = evidence.get("_invariant_results", {})
        for inv_type, result in inv_results.items():
            if isinstance(result, dict) and not result.get("passed", True):
                e_entry = evidence_by_invariant.setdefault(inv_type, {"total": 0, "with_evidence": 0})
                e_entry["total"] += 1
                if evidence.get("calls") or evidence.get("before_snapshot_ref"):
                    e_entry["with_evidence"] += 1

    return {
        "per_invariant_recall": per_invariant,
        "evidence_completeness_by_invariant": {
            k: {
                "total_findings": v["total"],
                "with_evidence": v["with_evidence"],
                "completeness_rate": round(v["with_evidence"] / v["total"], 4) if v["total"] else 0.0,
            }
            for k, v in evidence_by_invariant.items()
        },
        "total_invariant_types_tested": len(by_invariant),
        "invariant_coverage_rate": round(len(by_invariant) / 12, 4),  # 12 invariant types defined
    }


def _normalize_bug_type(bug_type: str) -> str:
    """Normalize bug type/category labels for consistent comparison."""
    mapping = {
        "privilege_escalation": "authorization",
        "permission_bypass": "authorization",
        "idor": "authorization",
        "tenant_isolation": "isolation",
        "multi_tenant": "isolation",
        "money_conservation": "conservation",
        "idempotency": "idempotency",
        "duplicate_submit": "idempotency",
        "concurrency_race": "concurrency",
        "state_machine": "state_machine",
        "contract_inconsistency": "contract",
        "schema_mismatch": "contract",
        "parameter_boundary": "boundary",
        "db_inconsistency": "db_consistency",
        "db_state_mismatch": "db_consistency",
        "cache_drift": "cache_consistency",
        "frontend_backend_drift": "cache_consistency",
        "ui_api_mismatch": "ui_api_availability",
        "error_handling": "error_handling",
        "security_boundary": "security",
        "historical_recurrence": "regression",
        "lifecycle_regression": "regression",
        "test_data_pollution": "data_hygiene",
        "cleanup_failure": "data_hygiene",
    }
    normalized = bug_type.strip().lower().replace(" ", "_").replace("-", "_")
    return mapping.get(normalized, normalized)

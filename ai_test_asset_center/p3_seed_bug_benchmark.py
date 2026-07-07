from __future__ import annotations

"""P3 seed-bug benchmark evaluator.

The evaluator measures whether a customer-scenario run exposed known seeded
business defects. It intentionally works from customer-safe HTTP observations
rather than test internals, so it can be used with V12 auto HAR, local fake
customer systems, or later real customer-approved benchmark runs.
"""

import json
import re
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


def _match_path(pattern: str, path: str) -> bool:
    expected = str(pattern or "")
    actual = str(path or "")
    if expected == actual:
        return True
    regex = "^" + re.sub(r"\{[^/]+\}", r"[^/]+", re.escape(expected)).replace(r"\[\^/\]\+", r"[^/]+") + "$"
    return re.match(regex, actual) is not None


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
    }

"""Typed privacy-field assertions over the current restricted DSL."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from . import assertion_dsl_privacy_base as _base
from .assertion_dsl_privacy_base import *  # noqa: F401,F403


PRIVACY_FIELD_ASSERTION_KIND = "privacy_field_policy"
SUPPORTED_KINDS = set(_base.SUPPORTED_KINDS) | {PRIVACY_FIELD_ASSERTION_KIND}
_core = _base._base._base


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    blob = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def _valid_tokens(value: Any) -> list[str | int]:
    tokens = _list(value)
    if not tokens or not all(
        (isinstance(token, str) and bool(token))
        or (isinstance(token, int) and not isinstance(token, bool) and token >= 0)
        for token in tokens
    ):
        return []
    return list(tokens)


def _meaningful_payload(value: Any) -> bool:
    if isinstance(value, list):
        return bool(value)
    if not isinstance(value, dict) or not value:
        return False
    normalized_keys = {
        re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
        for key in value
    }
    if normalized_keys.intersection({"error", "errors", "error_code"}) and not (
        normalized_keys.intersection({"id", "uuid", "key", "data", "items", "records", "results", "rows"})
    ):
        return False
    for wrapper in ("data", "items", "records", "results", "rows", "content"):
        if wrapper in value and isinstance(value[wrapper], (dict, list)):
            return _meaningful_payload(value[wrapper])
    return True


def _resolve_at(value: Any, tokens: list[str | int]) -> tuple[bool, Any]:
    current = value
    for token in tokens:
        if isinstance(token, int):
            if not isinstance(current, list) or not (0 <= token < len(current)):
                return False, None
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return False, None
            current = current[token]
    return True, current


def _field_name_matches(field_name: str, token: str) -> bool:
    """Normalized field-name match for name-mode tokens.

    A rule like 导出结果禁止包含 password names the forbidden field by its
    identifier; the observed body may nest it at any depth (rows[].password).
    Both sides normalize (lowercase, non-alphanumerics stripped) and the
    token must be a substring of the normalized field name (password matches
    password / user_password / credentials matches credential), so a
    response-side rule fires wherever the declared field appears.
    """
    normalized_field = re.sub(r"[^a-z0-9]+", "", str(field_name).lower())
    normalized_token = re.sub(r"[^a-z0-9]+", "", str(token).lower())
    return bool(normalized_token) and normalized_token in normalized_field


def _field_name_occurrences(value: Any, tokens: list[str | int]) -> list[Any]:
    """Recursive field-NAME scan for response-side privacy rules.

    The JSON-path mode (`_field_occurrences`) requires structured operand
    paths; a response-side rule (导出结果禁止包含 password) declares only the
    forbidden field name, whose nesting depth in the observed body is not
    part of the rule. This mode scans every dict key at every depth and
    reports the values under any key matching one of the tokens.
    """
    string_tokens = [token for token in tokens if isinstance(token, str)]
    if not string_tokens:
        return []
    occurrences: list[Any] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if any(_field_name_matches(str(key), token) for token in string_tokens):
                    occurrences.append(child)
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                if isinstance(child, (dict, list)):
                    visit(child)

    visit(value)
    return occurrences


def _field_occurrences(value: Any, tokens: list[str | int]) -> list[Any]:
    occurrences: list[Any] = []

    def visit(node: Any) -> None:
        found, resolved = _resolve_at(node, tokens)
        if found:
            occurrences.append(resolved)
        if isinstance(node, dict):
            for child in node.values():
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                if isinstance(child, (dict, list)):
                    visit(child)

    visit(value)
    return occurrences


def _privacy_receipt(
    *,
    probe: dict[str, Any],
    status: str,
    reason_code: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    error: str = "",
    harness_error: bool = False,
) -> dict[str, Any]:
    return _core._assertion_receipt(
        assertion_id=_text(probe.get("assertion_id")),
        kind=PRIVACY_FIELD_ASSERTION_KIND,
        status=status,
        reason_code=reason_code,
        expected=expected,
        actual=actual,
        error=error,
        observer_receipt_ids=[
            _text(value)
            for value in _list(probe.get("observer_receipt_ids"))
            if _text(value)
        ],
        source_refs=[
            dict(item)
            for item in _list(probe.get("source_refs"))
            if isinstance(item, dict)
        ],
        harness_error=harness_error,
        campaign_id=_text(probe.get("campaign_id")),
        execution_id=_text(probe.get("execution_id")),
    )


def evaluate_assertion(
    assertion: dict[str, Any],
    *,
    observations: dict[str, Any],
    source_refs: list[dict[str, Any]] | None = None,
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    spec = _dict(assertion)
    kind = _text(spec.get("kind") or spec.get("type"))
    if kind != PRIVACY_FIELD_ASSERTION_KIND:
        return _base.evaluate_assertion(
            assertion,
            observations=observations,
            source_refs=source_refs,
            campaign_id=campaign_id,
            execution_id=execution_id,
        )

    probe = _base.evaluate_assertion(
        {
            "assertion_id": _text(spec.get("assertion_id") or spec.get("id")),
            "kind": "json_path_exists",
            "path": "$",
            "source_refs": list(source_refs if source_refs is not None else spec.get("source_refs") or []),
        },
        observations=observations,
        source_refs=source_refs,
        campaign_id=campaign_id,
        execution_id=execution_id,
    )
    policy = _text(spec.get("privacy_policy"))
    tokens = _valid_tokens(spec.get("field_tokens"))
    path = _text(spec.get("json_path"))
    pattern = _text(spec.get("mask_pattern"))
    expected = {
        "privacy_policy": policy,
        "json_path": path,
        "mask_pattern_fingerprint": _fingerprint(pattern) if pattern else "",
    }
    actual: dict[str, Any] = {
        "status_code": _dict(observations).get("status_code"),
        "occurrence_count": None,
        "nonconforming_count": None,
        "value_types": [],
        "value_fingerprints": [],
    }

    if _text(probe.get("status")) == "INDETERMINATE" or probe.get("harness_error") is True:
        return _privacy_receipt(
            probe=probe,
            status="INDETERMINATE",
            reason_code=_text(probe.get("reason_code")) or "PRIVACY_OBSERVER_EVIDENCE_MISSING",
            expected=expected,
            actual=actual,
            error=_text(probe.get("error")),
            harness_error=probe.get("harness_error") is True,
        )
    if policy not in {"absent", "masked"} or not tokens:
        return _privacy_receipt(
            probe=probe,
            status="INDETERMINATE",
            reason_code="PRIVACY_ASSERTION_SPEC_INVALID",
            expected=expected,
            actual=actual,
            harness_error=True,
        )
    if policy == "masked":
        try:
            mask_re = re.compile(pattern)
        except re.error as exc:
            return _privacy_receipt(
                probe=probe,
                status="INDETERMINATE",
                reason_code="PRIVACY_MASK_PATTERN_INVALID",
                expected=expected,
                actual=actual,
                error=f"{type(exc).__name__}: {exc}",
                harness_error=True,
            )
        if not pattern:
            return _privacy_receipt(
                probe=probe,
                status="INDETERMINATE",
                reason_code="PRIVACY_MASK_PATTERN_MISSING",
                expected=expected,
                actual=actual,
                harness_error=True,
            )
    else:
        mask_re = None

    obs = _dict(observations)
    try:
        status_code = int(obs.get("status_code") or 0)
    except (TypeError, ValueError):
        status_code = 0
    if not (200 <= status_code < 300) or "body" not in obs:
        return _privacy_receipt(
            probe=probe,
            status="INDETERMINATE",
            reason_code="PRIVACY_RESPONSE_EVIDENCE_MISSING",
            expected=expected,
            actual=actual,
        )
    body = obs.get("body")
    if not _meaningful_payload(body):
        return _privacy_receipt(
            probe=probe,
            status="INDETERMINATE",
            reason_code="PRIVACY_RESOURCE_EVIDENCE_MISSING",
            expected=expected,
            actual=actual,
        )

    # Response-side rules declare the forbidden field NAME (导出结果禁止包含
    # password), not a JSON path — the field may nest at any depth. The
    # match_field_names mode (stamped by the response-side privacy obligation
    # pairing) scans field names recursively; structured-operand obligations
    # keep the exact JSON-path mode.
    if spec.get("match_field_names") is True:
        occurrences = _field_name_occurrences(body, tokens)
    else:
        occurrences = _field_occurrences(body, tokens)
    actual.update({
        "occurrence_count": len(occurrences),
        "value_types": sorted({type(value).__name__ for value in occurrences}),
        "value_fingerprints": sorted({_fingerprint(value) for value in occurrences}),
    })
    if policy == "absent":
        actual["nonconforming_count"] = len(occurrences)
        if occurrences:
            return _privacy_receipt(
                probe=probe,
                status="VIOLATION",
                reason_code="PRIVACY_FORBIDDEN_FIELD_EXPOSED",
                expected=expected,
                actual=actual,
            )
        return _privacy_receipt(
            probe=probe,
            status="PASS",
            reason_code="",
            expected=expected,
            actual=actual,
        )

    if not occurrences:
        if spec.get("allow_absent") is True:
            actual["nonconforming_count"] = 0
            return _privacy_receipt(
                probe=probe,
                status="PASS",
                reason_code="",
                expected=expected,
                actual=actual,
            )
        return _privacy_receipt(
            probe=probe,
            status="INDETERMINATE",
            reason_code="PRIVACY_MASKED_FIELD_NOT_OBSERVED",
            expected=expected,
            actual=actual,
        )
    nonconforming = [
        value
        for value in occurrences
        if not isinstance(value, str) or mask_re.fullmatch(value) is None
    ]
    actual["nonconforming_count"] = len(nonconforming)
    if nonconforming:
        return _privacy_receipt(
            probe=probe,
            status="VIOLATION",
            reason_code="PRIVACY_MASK_POLICY_VIOLATED",
            expected=expected,
            actual=actual,
        )
    return _privacy_receipt(
        probe=probe,
        status="PASS",
        reason_code="",
        expected=expected,
        actual=actual,
    )


def evaluate_assertions(
    assertions: list[dict[str, Any]],
    *,
    observations_by_id: dict[str, Any],
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    results = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            continue
        obs_key = _text(assertion.get("observer_id") or assertion.get("assertion_id") or "default")
        obs = _dict(observations_by_id.get(obs_key) or observations_by_id.get("default"))
        results.append(evaluate_assertion(
            assertion,
            observations=obs,
            campaign_id=campaign_id,
            execution_id=execution_id,
        ))
    return {
        "total": len(results),
        "passed": sum(1 for item in results if item.get("status") == "PASS"),
        "violations": sum(1 for item in results if item.get("status") == "VIOLATION"),
        "indeterminate": sum(1 for item in results if item.get("status") == "INDETERMINATE"),
        "failed": sum(1 for item in results if item.get("status") == "VIOLATION"),
        "harness_errors": sum(1 for item in results if item.get("harness_error")),
        "results": results,
    }

"""Finding severity / confidence differentiation grading (Task 10).

Problem: run3 findings were flat — every finding ``severity=P1``,
``confidence_score=0.85``, ``evidence_quality=validated/90`` — with no way for
customers to separate high-risk defects (privilege escalation, money /
conservation breach, destruction of others' data) from minor ones (parameter
boundaries, display copy), and no differentiation of evidence-chain strength.

This module provides an industry-generic, rule-table driven grading mechanism:

* ``severity_grade`` ∈ {critical, high, medium, low}: first-match-wins over a
  declarative rule table keyed on the *combination* of
  ``risk_family`` + assertion ``category`` + violation shape
  (``actual`` vs ``expected`` field content, HTTP method/path).  No benchmark
  or industry identifiers are hardcoded — every rule reads fields that the
  discovery pipeline itself compiled from source contracts at runtime.
* ``confidence`` ∈ [0, 0.97] (+ ``confidence_grade`` ∈ {high, medium, low}):
  computed dynamically from the evidence-chain completeness — reproduction
  success, dual-arm control evidence, cleanup completeness, independent
  validation, observer depth, and occurrence multiplicity.

Backward compatibility: the existing ``severity`` (P-level) field and
``evidence_quality.level/score`` are preserved byte-for-byte; the
differentiation is published in *new* fields (``severity_grade``,
``severity_grade_label``, ``grading``, ``evidence_quality.confidence`` /
``confidence_grade`` / ``grading_basis``), so existing consumers that read
``severity`` / ``evidence_quality.level`` keep working unchanged.

All functions are pure (stdlib only) and safe to call offline on persisted
findings (used by the run3 distribution check in ``.scratch/``).
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

# ────────────────────────────────────────────────────────────────────────────
# Vocabulary
# ────────────────────────────────────────────────────────────────────────────

SEVERITY_GRADES = ("critical", "high", "medium", "low")
SEVERITY_GRADE_ORDER = {grade: idx for idx, grade in enumerate(SEVERITY_GRADES)}

SEVERITY_GRADE_LABELS = {
    "critical": "严重（资金/守恒破坏、认证绕过、删除他人数据）",
    "high": "高危（越权/跨租户数据暴露、权限提升）",
    "medium": "中危（参数边界、校验缺失、状态机/幂等、状态码不符）",
    "low": "低危（仅展示/文案、轻微参数校验）",
}

CONFIDENCE_GRADES = ("high", "medium", "low")

# Risk families that express access-control semantics (industry-generic).
_ACCESS_FAMILIES = frozenset(
    {
        "authorization",
        "isolation",
        "tenant_isolation",
        "ownership",
        "permission",
        "access_control",
        "role",
        "ac",
    }
)

# Risk families that express money / resource conservation semantics.
_MONEY_FAMILIES = frozenset(
    {
        "conservation",
        "money",
        "finance",
        "balance",
        "fund",
        "ledger",
        "inventory",
        "stock",
    }
)

# Display / copy-only semantics.
_DISPLAY_FAMILIES = frozenset(
    {"visibility", "display", "ui", "copy", "presentation", "i18n", "wording"}
)

_VALIDATION_CATEGORIES = frozenset(
    {
        "validation_rejection",
        "parameter_boundary",
        "parameter_validation",
        "input_validation",
        "boundary",
        "idempotency",
        "idempotence",
        "state_machine",
        "state_transition",
        "illegal_state_transition",
        "http_status_class",
        "status_class",
    }
)

# Keywords that mark a debug / backdoor / bypass authentication endpoint.
_AUTH_BYPASS_PATH_KEYWORDS = (
    "debug",
    "backdoor",
    "bypass",
    "impersonate",
    "master-token",
    "mastertoken",
    "channel-test",
    "test-token",
)

_DESTRUCTION_SIGNALS = (
    "deleted",
    "removed",
    "destroyed",
    "purged",
    "hard_delete",
    "deletion",
)

_DISPLAY_DESCRIPTION_KEYWORDS = (
    "文字状态",
    "状态文字",
    "文案",
    "操作入口",
    "提示",
    "展示",
    "display",
    "copy",
    "wording",
    "label",
)


# ────────────────────────────────────────────────────────────────────────────
# Small helpers (stdlib only)
# ────────────────────────────────────────────────────────────────────────────

def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _finding_method(finding: dict[str, Any]) -> str:
    reproduction = _dict(finding.get("reproduction"))
    evidence = _dict(finding.get("evidence"))
    method = (
        _text(reproduction.get("method"))
        or _text(evidence.get("method"))
        or _text(evidence.get("request"))
        or _text(finding.get("repro_method"))
    )
    if " " in method:
        method = method.split(" ", 1)[0]
    return method.upper()


def _finding_path(finding: dict[str, Any]) -> str:
    reproduction = _dict(finding.get("reproduction"))
    evidence = _dict(finding.get("evidence"))
    return (
        _text(reproduction.get("path"))
        or _text(evidence.get("path"))
        or _text(evidence.get("target"))
        or _text(finding.get("repro_path"))
    )


def _iter_numeric_values(node: Any) -> Iterable[float]:
    """Yield all numeric leaves of actual/expected payloads (recursive)."""
    if isinstance(node, dict):
        for value in node.values():
            yield from _iter_numeric_values(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _iter_numeric_values(value)
    elif isinstance(node, bool):
        return
    elif isinstance(node, (int, float)):
        yield float(node)


def _effect_count(finding: dict[str, Any]) -> Optional[float]:
    """Observed effect magnitude (None when not observed)."""
    evidence = _dict(finding.get("evidence"))
    raw = _dict(finding.get("raw_evidence"))
    observations = _dict(raw.get("observations"))
    for candidate in (
        evidence.get("effect_count"),
        observations.get("effect_count"),
        _dict(finding.get("actual")).get("effect_count"),
        _dict(finding.get("actual")).get("treatment_effect_count"),
    ):
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, (int, float)):
            return float(candidate)
    return None


def _expected_effect_count(finding: dict[str, Any]) -> Optional[float]:
    expected = finding.get("expected")
    if isinstance(expected, dict):
        for key in ("effect_count", "treatment_effect_count"):
            value = expected.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return float(value)
    return None


def _occurrence_count(finding: dict[str, Any]) -> int:
    count = finding.get("delivery_occurrence_count")
    if isinstance(count, bool):
        count = None
    if not isinstance(count, (int, float)) or count <= 0:
        ids = _list(finding.get("delivery_occurrence_finding_ids"))
        count = len(ids)
    if not isinstance(count, (int, float)) or count <= 0:
        count = 1
    return int(count)


def _observer_depth(finding: dict[str, Any]) -> int:
    """Number of observer receipts tying the violation to runtime evidence."""
    depth = 0
    for assertion in _list(finding.get("failed_assertions")):
        if isinstance(assertion, dict):
            depth = max(depth, len(_list(assertion.get("observer_receipt_ids"))))
    depth = max(depth, len(_list(finding.get("contract_evidence_receipt_ids"))))
    evidence = _dict(finding.get("evidence"))
    assertion = _dict(evidence.get("assertion"))
    depth = max(depth, len(_list(assertion.get("observer_receipt_ids"))))
    return depth


# ────────────────────────────────────────────────────────────────────────────
# Severity rules (first-match wins, highest priority first)
# ────────────────────────────────────────────────────────────────────────────

def _rule_money_conservation_negative(finding: dict[str, Any]) -> bool:
    """critical: money / conservation integrity breach — negative values."""
    family = _text(finding.get("risk_family")).lower()
    category = _text(finding.get("category")).lower()
    if family not in _MONEY_FAMILIES and not any(
        token in category for token in ("non_negative", "negative", "conservation", "money")
    ):
        return False
    actual = finding.get("actual")
    return any(value < 0 for value in _iter_numeric_values(actual))


def _rule_auth_bypass_backdoor(finding: dict[str, Any]) -> bool:
    """critical: authentication bypass / backdoor endpoint grants access."""
    if _text(finding.get("risk_family")).lower() != "authorization":
        return False
    path = _finding_path(finding).lower()
    if not path:
        return False
    if "/auth/" not in path:
        return False
    if not any(keyword in path for keyword in _AUTH_BYPASS_PATH_KEYWORDS):
        return False
    actual = _dict(finding.get("actual"))
    return actual.get("viewer_can_access") is True or actual.get("leak_detected") is True


def _rule_destruction_of_others_data(finding: dict[str, Any]) -> bool:
    """critical: evidenced destruction / deletion of others' data."""
    if _text(finding.get("risk_family")).lower() not in _ACCESS_FAMILIES:
        return False
    method = _finding_method(finding)
    actual = finding.get("actual")
    text_actual = _text(actual) if not isinstance(actual, dict) else " ".join(
        _text(v) if not isinstance(v, (dict, list)) else "" for v in actual.values()
    )
    has_destruction_shape = method == "DELETE" or any(
        signal in text_actual.lower() for signal in _DESTRUCTION_SIGNALS
    )
    if not has_destruction_shape:
        return False
    effect = _effect_count(finding)
    if effect is not None and effect > 0:
        expected_effect = _expected_effect_count(finding)
        if expected_effect in (None, 0):
            return True
    # Direct flag in actual (e.g. deleted: true) while expected forbids it.
    if isinstance(actual, dict):
        for key, value in actual.items():
            if key.lower() in _DESTRUCTION_SIGNALS and value is True:
                expected = finding.get("expected")
                if not isinstance(expected, dict) or expected.get(key) is not True:
                    return True
    return False


def _rule_cross_tenant_exposure(finding: dict[str, Any]) -> bool:
    """high: cross-tenant / cross-role data exposure (privilege escalation)."""
    if _text(finding.get("risk_family")).lower() not in _ACCESS_FAMILIES:
        return False
    actual = _dict(finding.get("actual"))
    return actual.get("viewer_can_access") is True or actual.get("leak_detected") is True


def _rule_validation_parameter_state(finding: dict[str, Any]) -> bool:
    """medium: validation / parameter boundary / state machine / status class."""
    family = _text(finding.get("risk_family")).lower()
    category = _text(finding.get("category")).lower()
    if family in {"validation", "input", "parameter", "schema", "state", "idempotency"}:
        return True
    if category in _VALIDATION_CATEGORIES:
        return True
    # Parameter boundary: negative / out-of-range numeric input without money
    # impact (money families already handled as critical above).
    if "parameter" in family or "boundary" in category:
        return True
    return False


def _rule_display_copy(finding: dict[str, Any]) -> bool:
    """low: display / copy-only findings."""
    family = _text(finding.get("risk_family")).lower()
    category = _text(finding.get("category")).lower()
    if family in _DISPLAY_FAMILIES:
        return True
    if any(token in family for token in ("display", "ui", "copy", "wording", "label")):
        return True
    description = _text(finding.get("description"))
    return any(keyword in description for keyword in _DISPLAY_DESCRIPTION_KEYWORDS)


# Rule table: (grade, rule_name, predicate) — evaluated in order, first match wins.
_SEVERITY_RULES: tuple[tuple[str, str, Any], ...] = (
    ("critical", "money_conservation_negative", _rule_money_conservation_negative),
    ("critical", "auth_bypass_backdoor", _rule_auth_bypass_backdoor),
    ("critical", "destruction_of_others_data", _rule_destruction_of_others_data),
    ("high", "cross_tenant_exposure", _rule_cross_tenant_exposure),
    ("medium", "validation_parameter_state", _rule_validation_parameter_state),
    ("low", "display_copy", _rule_display_copy),
)

_DEFAULT_SEVERITY_GRADE = "medium"


def is_gradeable(finding: dict[str, Any]) -> bool:
    """A finding is gradeable when it carries violation identity (family /
    category) and a violation shape (actual or expected).  Internal clues
    (demotion reasons) are not gradeable."""
    if not isinstance(finding, dict):
        return False
    if not (_text(finding.get("risk_family")) or _text(finding.get("category"))):
        return False
    return finding.get("actual") is not None or finding.get("expected") is not None


def match_severity_rule(finding: dict[str, Any]) -> tuple[str, str]:
    """Return (severity_grade, rule_name) for a gradeable finding.

    First-match wins over the priority-ordered rule table.  Confirmed
    violations whose family/shape match no rule default to medium.
    """
    for grade, rule_name, predicate in _SEVERITY_RULES:
        try:
            if predicate(finding):
                return grade, rule_name
        except Exception:  # a broken rule must never crash grading
            continue
    return _DEFAULT_SEVERITY_GRADE, "default_unclassified"


def grade_severity(finding: dict[str, Any]) -> str:
    """Severity grade for a finding (``critical``/``high``/``medium``/``low``).

    Returns ``""`` for non-gradeable findings (internal clues, non-findings).
    """
    if not is_gradeable(finding):
        return ""
    grade, _rule_name = match_severity_rule(finding)
    return grade


# ────────────────────────────────────────────────────────────────────────────
# Confidence: dynamic from evidence-chain completeness
# ────────────────────────────────────────────────────────────────────────────

def _has_reproduction(finding: dict[str, Any]) -> bool:
    steps = _list(finding.get("reproduction_steps")) or _list(
        _dict(finding.get("reproduction")).get("reproduction_steps")
    )
    if not steps:
        return False
    raw = _dict(finding.get("raw_evidence"))
    if raw.get("has_real_evidence") is not True:
        return False
    response = _dict(raw.get("response_raw"))
    return bool(
        response.get("status_code") is not None
        or response.get("body")
        or _dict(finding.get("har_evidence")).get("status_code")
        or _dict(finding.get("evidence")).get("response")
    )


def _has_control_evidence(finding: dict[str, Any]) -> bool:
    """Dual-arm comparison evidence: control succeeded / target reached."""
    evidence = _dict(finding.get("evidence"))
    raw = _dict(finding.get("raw_evidence"))
    observations = _dict(raw.get("observations"))
    auth_receipt = _dict(finding.get("authorization_causality_receipt"))
    return bool(
        evidence.get("control_succeeded") is True
        or observations.get("control_succeeded") is True
        or _text(raw.get("control_actor"))
        or auth_receipt.get("control_target_reached") is True
        or _text(finding.get("control_actor"))
    )


def _cleanup_complete(finding: dict[str, Any]) -> bool:
    if isinstance(finding.get("cleanup_failures"), (int, float)):
        if int(finding.get("cleanup_failures")) > 0:
            return False
    evidence = _dict(finding.get("evidence"))
    raw = _dict(finding.get("raw_evidence"))
    status = _text(
        evidence.get("cleanup_status")
        or _dict(raw.get("observations")).get("cleanup_status")
    ).upper()
    if status in {"FAILED", "BLOCKED", "PENDING"}:
        return False
    return True


def _independently_validated(finding: dict[str, Any]) -> bool:
    return bool(
        finding.get("gate_passed") is True
        and _text(finding.get("semantic_verdict")).upper() == "SEMANTIC_CONFIRMED"
        and _text(finding.get("business_evidence_status")).upper() == "VALIDATED"
    )


def compute_confidence(finding: dict[str, Any]) -> tuple[float, list[str]]:
    """Dynamic confidence score (0..0.97) and its evidence basis.

    High: reproduction success + cleanup complete + dual-arm control evidence
          + independent validation + multi-occurrence (>= 3).
    Medium: reproduced but single occurrence / partial evidence.
    Low: unstable reproduction or broken evidence chain.
    """
    score = 0.0
    basis: list[str] = []

    reproduced = _has_reproduction(finding)
    if reproduced:
        score += 0.30
        basis.append("reproduced")
    else:
        basis.append("no_reproduction")

    if _has_control_evidence(finding):
        score += 0.20
        basis.append("dual_arm_control")
    else:
        basis.append("no_control_evidence")

    if _cleanup_complete(finding):
        score += 0.10
        basis.append("cleanup_complete")
    else:
        basis.append("cleanup_incomplete")

    if _independently_validated(finding):
        score += 0.10
        basis.append("independently_validated")

    if _observer_depth(finding) >= 2:
        score += 0.05
        basis.append("multi_observer")

    occurrences = _occurrence_count(finding)
    if occurrences >= 3:
        score += 0.20
        basis.append("occurrences>=3")
    elif occurrences == 2:
        score += 0.10
        basis.append("occurrences=2")
    else:
        basis.append("occurrences=1")

    if not reproduced:
        # A runtime finding without a real reproduction has a broken core
        # evidence link: cap so it never reaches medium/high regardless of
        # peripheral signals.
        score = min(score, 0.50)
        basis.append("no_reproduction_cap")

    return round(min(0.97, score), 2), basis


def confidence_grade_for(score: float) -> str:
    """Map a confidence score to a grade.

    high: 复现成功 + 清理完整 + 对照双组 + 独立验证 + 多次 occurrence
    medium: 复现成功但单次 occurrence 或证据部分缺失 (0.55..0.84)
    low: 复现不稳定或证据链缺环 (< 0.55)
    """
    if score >= 0.85:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


# ────────────────────────────────────────────────────────────────────────────
# Apply: publish grading on a finding (backward compatible)
# ────────────────────────────────────────────────────────────────────────────

def apply_finding_grading(finding: dict[str, Any]) -> dict[str, Any]:
    """Return a new finding dict with grading fields attached.

    Never mutates the input.  Preserves ``severity`` (P-level) and
    ``evidence_quality.level/score`` unchanged; adds:
      * ``severity_grade`` / ``severity_grade_label``
      * ``evidence_quality.confidence`` / ``confidence_grade`` / ``grading_basis``
      * ``grading`` metadata (schema_version, rules_applied, basis)
    Non-gradeable findings (internal clues) pass through unchanged.
    Idempotent: applying twice yields the same result.
    """
    if not isinstance(finding, dict):
        return finding
    out = dict(finding)
    if not is_gradeable(out):
        return out

    grade, rule_name = match_severity_rule(out)
    confidence, basis = compute_confidence(out)
    confidence_grade = confidence_grade_for(confidence)

    out["severity_grade"] = grade
    out["severity_grade_label"] = SEVERITY_GRADE_LABELS[grade]

    evidence_quality = dict(_dict(out.get("evidence_quality")))
    evidence_quality["confidence"] = confidence
    evidence_quality["confidence_grade"] = confidence_grade
    evidence_quality["grading_basis"] = list(basis)
    out["evidence_quality"] = evidence_quality

    out["grading"] = {
        "schema_version": "qualibug.finding-grading.v1",
        "severity_grade": grade,
        "confidence": confidence,
        "confidence_grade": confidence_grade,
        "rules_applied": [rule_name],
        "evidence_basis": list(basis),
    }
    return out

from __future__ import annotations

from typing import Any

# NOTE: This mapping is extensible. Add project-specific equivalence classes
# via RISK_EQUIVALENCE.update(custom_map) before evaluation.
RISK_EQUIVALENCE = {
    "privilege_escalation": {"permission_bypass", "auth_bypass", "privilege_escalation"},
    "permission_bypass": {"permission_bypass", "privilege_escalation"},
    "auth_bypass": {"auth_bypass", "permission_bypass"},
    "payment_callback": {"payment_callback", "idempotency", "money_consistency", "state_flow"},
    "refund_abuse": {"refund_abuse", "money_consistency", "state_consistency"},
    "locked_account_bypass": {"locked_account_bypass", "auth_bypass"},
    "stock_consistency": {"stock_consistency", "quantity_consistency"},
}


def risk_compatible(left: str | None, right: str | None) -> bool:
    l = str(left or "")
    r = str(right or "")
    return l == r or r in RISK_EQUIVALENCE.get(l, set()) or l in RISK_EQUIVALENCE.get(r, set())


def normalize_api(api: str) -> str:
    normalized = str(api or "").split("?")[0].strip().lower()
    if " " in normalized:
        method, path = normalized.split(" ", 1)
        return f"{method.upper()} {normalize_path(path)}"
    return normalize_path(normalized)


def normalize_path(path: str) -> str:
    """Normalize API path for matching — replaces path params and numeric IDs with '*'.

    Industry-agnostic: handles {any_param}, :param, and pure numeric segments.
    """
    import re
    normalized = path.strip().lower()
    # Replace any {param} or :param pattern
    normalized = re.sub(r"\{[^}]+\}", "*", normalized)
    normalized = re.sub(r":[a-zA-Z_][a-zA-Z0-9_]*", "*", normalized)
    parts = []
    for part in normalized.split("/"):
        # Replace pure numeric segments (IDs)
        if part.isdigit():
            parts.append("*")
        # Replace alphanumeric segments that look like IDs (e.g., "o123", "abc456def")
        elif re.match(r"^[a-zA-Z]+\d+[a-zA-Z]*$", part) and len(part) >= 3:
            parts.append("*")
        else:
            parts.append(part)
    return "/".join(parts)


def path_compatible(left: str, right: str) -> bool:
    left_method, left_path = split_method_path(left)
    right_method, right_path = split_method_path(right)
    if left_method and right_method and left_method != right_method:
        return False
    left_parts = left_path.split("/")
    right_parts = right_path.split("/")
    if len(left_parts) != len(right_parts):
        return False
    return all(a == b or a == "*" or b == "*" for a, b in zip(left_parts, right_parts))


def split_method_path(value: str) -> tuple[str | None, str]:
    if " " in value:
        method, path = value.split(" ", 1)
        return method.upper(), path
    return None, value


def api_match_score(discovered_apis: set[str], truth_apis: set[str]) -> float:
    if discovered_apis & truth_apis:
        return 1.0
    if any(path_compatible(a, t) for a in discovered_apis for t in truth_apis):
        return 0.82
    return 0.0


def text_blob(item: dict[str, Any]) -> str:
    keys = ["probe_id", "title", "risk_type", "predicted_template_id", "expected", "actual", "bug_signal", "evidence_signature", "template_id", "bug_instance_id", "trigger_condition", "expected_behavior", "actual_bug_behavior"]
    return " ".join(str(item.get(key, "")) for key in keys).lower().replace("_", " ")


def semantic_score(discovered: dict, truth: dict) -> float:
    score = 0.0
    if risk_compatible(discovered.get("risk_type"), truth.get("risk_type")):
        score += 0.25
    if str(discovered.get("severity")) == str(truth.get("severity")):
        score += 0.08
    if discovered.get("actor") and str(discovered.get("actor")) in str(truth.get("variant_dimensions", {})):
        score += 0.08
    predicted_template = discovered.get("predicted_template_id")
    if predicted_template and predicted_template == truth.get("template_id"):
        score += 0.32
    disc_text = text_blob(discovered)
    truth_text = text_blob(truth)
    for token in high_signal_tokens(truth):
        if token in disc_text:
            score += 0.04
    if any(word in disc_text and word in truth_text for word in ["admin", "tenant", "coupon", "stock", "refund", "payment", "idempotency", "order", "cancel"]):
        score += 0.08
    return min(score, 1.0)


def high_signal_tokens(truth: dict) -> list[str]:
    values = [truth.get("template_id", ""), truth.get("risk_type", ""), truth.get("trigger_condition", ""), truth.get("expected_behavior", ""), truth.get("actual_bug_behavior", "")]
    tokens: list[str] = []
    for value in values:
        text = str(value).lower().replace("_", " ")
        for token in text.split():
            if len(token) >= 5 and token not in {"status", "should", "requests", "returns", "缺陷版本违反"}:
                tokens.append(token)
    return tokens[:16]


def match_type_from(total: float, discovered: dict, truth: dict, api_score: float) -> str:
    if discovered.get("predicted_template_id") == truth.get("template_id") and api_score >= 0.82 and total >= 0.82:
        return "exact_instance"
    if discovered.get("predicted_template_id") == truth.get("template_id") and total >= 0.68:
        return "template_match"
    if total >= 0.72:
        return "partial_instance"
    return "no_match"


def match_bug(discovered: dict, truth: list[dict], used: set[str]) -> dict | None:
    discovered_apis = {normalize_api(api) for api in discovered.get("related_apis", [])}
    candidates: list[tuple[float, str, dict]] = []
    for item in truth:
        gt_id = str(item.get("bug_id") or item.get("bug_instance_id"))
        if gt_id in used:
            continue
        truth_apis = {normalize_api(api) for api in item.get("related_apis", [])}
        api_score = api_match_score(discovered_apis, truth_apis)
        if api_score == 0:
            continue
        sem = semantic_score(discovered, item)
        total = api_score * 0.42 + sem * 0.58
        mtype = match_type_from(total, discovered, item, api_score)
        if mtype == "no_match":
            continue
        enriched = dict(item)
        enriched["__match_type"] = mtype
        enriched["__match_score"] = round(total, 4)
        candidates.append((total, mtype, enriched))
    if not candidates:
        return None
    priority = {"exact_instance": 3, "partial_instance": 2, "template_match": 1}
    candidates.sort(key=lambda x: (priority.get(x[1], 0), x[0]), reverse=True)
    return candidates[0][2]

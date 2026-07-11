"""Restricted assertion DSL — no eval, typed operators only."""
from __future__ import annotations

import json
from typing import Any


SUPPORTED_KINDS = {
    "http_status",
    "http_status_class",
    "json_path_exists",
    "json_path_type",
    "json_path_compare",
    "equality",
    "delta",
    "cardinality",
    "state_transition",
    "owner_tenant_visibility",
    "conservation",
    "idempotency_effect",
    "concurrency_final_invariant",
    "eventual_consistency",
    "cross_surface_consistency",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_path(data: Any, path: str) -> Any:
    """Minimal JSON path: $.a.b[0] style without eval."""
    if not path or path == "$":
        return data
    cur: Any = data
    token = path[1:] if path.startswith("$") else path
    parts: list[str] = []
    buf = ""
    i = 0
    while i < len(token):
        ch = token[i]
        if ch == ".":
            if buf:
                parts.append(buf)
                buf = ""
            i += 1
            continue
        if ch == "[":
            if buf:
                parts.append(buf)
                buf = ""
            j = token.find("]", i)
            if j < 0:
                raise ValueError("invalid_json_path")
            parts.append(token[i : j + 1])
            i = j + 1
            continue
        buf += ch
        i += 1
    if buf:
        parts.append(buf)
    for part in parts:
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            idx = int(part[1:-1])
            if not isinstance(cur, list) or idx >= len(cur):
                raise KeyError(part)
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or part not in cur:
                raise KeyError(part)
            cur = cur[part]
    return cur


def evaluate_assertion(
    assertion: dict[str, Any],
    *,
    observations: dict[str, Any],
    source_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate one assertion. Returns expected/actual/pass/receipt fields."""
    spec = _dict(assertion)
    kind = _text(spec.get("kind") or spec.get("type"))
    obs = _dict(observations)
    expected = spec.get("expected")
    actual: Any = None
    passed = False
    error = ""

    try:
        if kind not in SUPPORTED_KINDS and kind not in {
            "authorization", "isolation", "state", "conservation", "idempotency",
            "concurrency", "validation", "visibility", "temporal", "privacy",
        }:
            raise ValueError(f"unsupported_assertion_kind:{kind}")

        if kind in {"http_status", "authorization", "validation"} and "status_code" in (spec.get("compare_field") or "status_code"):
            actual = obs.get("status_code")
            if expected is None:
                expected = spec.get("expected_status")
            passed = actual == expected if expected is not None else False
        elif kind == "http_status_class":
            actual = int(obs.get("status_code") or 0)
            expected_class = int(expected or spec.get("expected_class") or 0)
            passed = (actual // 100) == expected_class
            expected = expected_class
        elif kind == "json_path_exists":
            path = _text(spec.get("path") or "$.")
            try:
                actual = _json_path(obs.get("body"), path)
                passed = actual is not None
                expected = True
            except (KeyError, ValueError, TypeError, IndexError):
                actual = None
                passed = False
                expected = True
        elif kind == "json_path_type":
            path = _text(spec.get("path") or "$")
            expected_type = _text(expected or spec.get("expected_type") or spec.get("type_name")).lower()
            try:
                value = _json_path(obs.get("body"), path)
            except (KeyError, ValueError, TypeError, IndexError):
                value = None
            actual = type(value).__name__ if value is not None else None
            type_map = {
                "string": str,
                "str": str,
                "number": (int, float),
                "int": int,
                "integer": int,
                "float": float,
                "bool": bool,
                "boolean": bool,
                "object": dict,
                "dict": dict,
                "array": list,
                "list": list,
                "null": type(None),
            }
            py_type = type_map.get(expected_type)
            if py_type is None:
                raise ValueError(f"unsupported_json_path_type:{expected_type}")
            passed = value is not None and isinstance(value, py_type)
            expected = expected_type
        elif kind == "json_path_compare":
            path = _text(spec.get("path") or "$")
            actual = _json_path(obs.get("body"), path)
            op = _text(spec.get("operator") or "eq")
            if op == "eq":
                passed = actual == expected
            elif op == "neq":
                passed = actual != expected
            elif op == "gte":
                passed = actual >= expected
            elif op == "lte":
                passed = actual <= expected
            else:
                raise ValueError(f"unsupported_operator:{op}")
        elif kind == "equality":
            actual = obs.get("value")
            passed = actual == expected
        elif kind == "delta":
            before = obs.get("before")
            after = obs.get("after")
            actual = None if before is None or after is None else (after - before)
            passed = actual == expected
        elif kind == "cardinality":
            collection = obs.get("collection")
            actual = len(collection) if isinstance(collection, list) else None
            passed = actual == expected
        elif kind == "state_transition":
            actual = {"before": obs.get("before_state"), "after": obs.get("after_state")}
            expected = {"before": spec.get("from_state"), "after": spec.get("to_state")}
            passed = actual == expected
        elif kind in {"owner_tenant_visibility", "isolation", "visibility"}:
            actual = {
                "owner_can_access": bool(obs.get("owner_can_access")),
                "viewer_can_access": bool(obs.get("viewer_can_access")),
                "leak_detected": bool(obs.get("leak_detected")),
            }
            expected = {
                "owner_can_access": True,
                "viewer_can_access": False,
                "leak_detected": False,
            }
            # Formal pass only when control succeeded and treatment did not leak.
            passed = (
                bool(obs.get("control_succeeded"))
                and actual["owner_can_access"]
                and not actual["viewer_can_access"]
                and not actual["leak_detected"]
            )
        elif kind in {"conservation"}:
            equation = _dict(spec.get("equation"))
            before_vals = _dict(obs.get("before_values"))
            after_vals = _dict(obs.get("after_values"))
            operator = _text(equation.get("operator") or "unchanged_sum")
            terms = _list(equation.get("terms") or equation.get("fields"))
            if terms:
                before_sum = sum(float(before_vals.get(_text(t)) or 0) for t in terms)
                after_sum = sum(float(after_vals.get(_text(t)) or 0) for t in terms)
            else:
                before_sum = sum(float(v) for v in before_vals.values() if isinstance(v, (int, float)))
                after_sum = sum(float(v) for v in after_vals.values() if isinstance(v, (int, float)))
            actual = {"before_sum": before_sum, "after_sum": after_sum, "before": before_vals, "after": after_vals}
            expected = {"operator": operator, "terms": terms}
            if not before_vals or not after_vals:
                passed = False
                error = "conservation_values_missing"
            elif operator == "unchanged_sum":
                passed = before_sum == after_sum
            elif operator == "eq":
                passed = before_vals == after_vals
            else:
                raise ValueError(f"unsupported_conservation_operator:{operator}")
        elif kind in {"idempotency_effect", "idempotency"}:
            actual = {
                "effect_count": obs.get("effect_count"),
                "http_statuses": obs.get("http_statuses"),
            }
            expected = {"effect_count": spec.get("expected_effect_count", 1)}
            # Dual 2xx alone is insufficient.
            statuses = _list(obs.get("http_statuses"))
            if statuses and all(int(s) // 100 == 2 for s in statuses if str(s).isdigit() or isinstance(s, int)):
                if obs.get("effect_count") is None:
                    passed = False
                    error = "http_status_alone_insufficient"
                else:
                    passed = int(obs.get("effect_count") or 0) == int(expected["effect_count"])
            else:
                passed = int(obs.get("effect_count") or -1) == int(expected["effect_count"])
        elif kind in {"concurrency_final_invariant", "concurrency"}:
            actual = {
                "final_state": obs.get("final_state"),
                "invariant_held": obs.get("invariant_held"),
                "dual_2xx": obs.get("dual_2xx"),
            }
            expected = {"invariant_held": True}
            if obs.get("invariant_held") is None and obs.get("dual_2xx"):
                passed = False
                error = "dual_2xx_insufficient"
            else:
                passed = bool(obs.get("invariant_held"))
        elif kind == "eventual_consistency":
            actual = obs.get("converged")
            expected = True
            passed = bool(obs.get("converged")) and bool(obs.get("within_window"))
        elif kind == "cross_surface_consistency":
            actual = obs.get("surfaces_agree")
            expected = True
            passed = bool(obs.get("surfaces_agree"))
        else:
            # Generic property template fallback: require explicit expected/actual in observations
            actual = obs.get("actual", obs.get("treatment_result"))
            expected = expected if expected is not None else obs.get("expected")
            passed = actual == expected and expected is not None
    except Exception as exc:  # fail closed with structured error
        error = f"{type(exc).__name__}: {exc}"
        passed = False

    return {
        "assertion_id": _text(spec.get("assertion_id") or spec.get("id")),
        "kind": kind,
        "passed": bool(passed),
        "expected": expected,
        "actual": actual,
        "error": error,
        "observer_receipt": _dict(obs.get("observer_receipt")),
        "source_refs": list(source_refs or spec.get("source_refs") or []),
        "harness_error": bool(obs.get("harness_error")),
    }


def evaluate_assertions(
    assertions: list[dict[str, Any]],
    *,
    observations_by_id: dict[str, Any],
) -> dict[str, Any]:
    results = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            continue
        obs_key = _text(assertion.get("observer_id") or assertion.get("assertion_id") or "default")
        obs = _dict(observations_by_id.get(obs_key) or observations_by_id.get("default"))
        results.append(evaluate_assertion(assertion, observations=obs))
    return {
        "total": len(results),
        "passed": sum(1 for item in results if item.get("passed")),
        "failed": sum(1 for item in results if not item.get("passed")),
        "harness_errors": sum(1 for item in results if item.get("harness_error")),
        "results": results,
    }


def materialize_assertion(
    assertion: dict[str, Any],
    *,
    observations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn compiler templates into executable DSL specs without inventing expected values."""
    spec = dict(_dict(assertion))
    kind = _text(spec.get("kind") or spec.get("type"))
    prop = _dict(spec.get("property"))
    obs = _dict(observations)

    # Map obligation family aliases onto typed DSL kinds.
    family_map = {
        "authorization": "owner_tenant_visibility",
        "isolation": "owner_tenant_visibility",
        "visibility": "owner_tenant_visibility",
        "privacy": "owner_tenant_visibility",
        "idempotency": "idempotency_effect",
        "concurrency": "concurrency_final_invariant",
        "state": "state_transition",
        "conservation": "conservation",
        "validation": "http_status",
    }
    if kind in family_map:
        spec["kind"] = family_map[kind]
        kind = spec["kind"]

    if kind == "http_status" and spec.get("expected") is None and spec.get("expected_status") is None:
        # Prefer explicit expected from property; otherwise leave unset (fail closed).
        if prop.get("expected_status") is not None:
            spec["expected"] = prop.get("expected_status")
        elif obs.get("expected_status") is not None:
            spec["expected"] = obs.get("expected_status")

    if kind == "owner_tenant_visibility":
        spec.setdefault("require_control", True)

    if kind == "conservation" and not _dict(spec.get("equation")):
        equation = _dict(prop.get("equation"))
        if equation:
            spec["equation"] = equation

    if kind == "idempotency_effect" and spec.get("expected_effect_count") is None:
        if prop.get("expected_effect_count") is not None:
            spec["expected_effect_count"] = prop.get("expected_effect_count")

    return spec

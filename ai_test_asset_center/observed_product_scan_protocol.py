from __future__ import annotations

"""Minimal process-boundary protocol shared by evaluator and product worker."""

import re
from functools import lru_cache
from typing import Any


PRODUCT_SCAN_WORKER_REQUEST_SCHEMA = (
    "qualibug.observed-product-scan-worker-request.v1"
)

_EVALUATOR_PRIVATE_ENV_PREFIXES = (
    "QUALIBUG_BENCHMARK_",
    "QUALIBUG_EVALUATOR_",
    "QUALIBUG_PRIVATE_",
    "QUALIBUG_TRUSTED_OBSERVATION_",
)

_EVALUATOR_PRIVATE_ENV_MARKERS = (
    "GROUND_TRUTH",
    "PRIVATE_EVAL",
    "TRUSTED_OBSERVATION",
)

_EVALUATOR_PRIVATE_CONTEXT_KEYS = frozenset({
    "benchmark_ground_truth",
    "benchmark_match_keywords",
    "benchmark_scoring_rules",
    "benchmark_source",
    "evaluator_miss_labels",
    "evaluator_match_keywords",
    "evaluator_observation",
    "evaluator_observations",
    "evaluator_private",
    "evaluator_receipt",
    "evaluator_receipts",
    "evaluator_report",
    "evaluator_reports",
    "expected_bug_ids",
    "expected_defects",
    "ground_truth",
    "ground_truth_fingerprint",
    "ground_truth_path",
    "ground_truth_ref",
    "gt_bug_ids",
    "gt_fingerprint",
    "gt_path",
    "gt_ref",
    "hidden_ground_truth",
    "matched_bug_ids",
    "missed_bug_ids",
    "p3_http_observations",
    "p3_seed_defects",
    "private_evaluation",
    "private_evaluator",
    "reproduction_answers",
    "seed_bug_defects",
    "seed_defects",
    "trusted_execution_observations",
    "trusted_observation_pack",
})


# Hot path. ``find_evaluator_private_context_paths`` walks the entire context
# structure and classifies every key, and each classification used to run three
# ``re.sub`` passes over the same key strings again and again. py-spy sampling
# measured that normalization work at ~39% of execution CPU (``re.sub`` 30.5%
# plus ``re._compile`` 8.5%). Both classifiers are pure functions of their
# string form, so the patterns are precompiled and results are memoized behind
# a bounded cache; behaviour is unchanged and the public signatures still
# accept ``Any``.
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9]+")
_REPEATED_UNDERSCORE_RE = re.compile(r"_+")


@lru_cache(maxsize=8192)
def _normalized_context_key_cached(value: str) -> str:
    text = _CAMEL_BOUNDARY_RE.sub("_", value.strip())
    return (
        _REPEATED_UNDERSCORE_RE.sub("_", _NON_ALNUM_RE.sub("_", text))
        .strip("_")
        .lower()
    )


def _normalized_context_key(value: Any) -> str:
    """Normalize a context key. Accepts any value (coerced to ``str``)."""
    return _normalized_context_key_cached(str(value or ""))


@lru_cache(maxsize=8192)
def _is_evaluator_private_context_key_cached(value: str) -> bool:
    normalized = _normalized_context_key(value)
    if normalized in _EVALUATOR_PRIVATE_CONTEXT_KEYS:
        return True
    if "ground_truth" in normalized:
        return True
    if normalized.startswith(("private_evaluator_", "evaluator_private_")):
        return True
    if normalized.startswith("trusted_observation_"):
        return True
    return normalized.startswith("evaluator_") and normalized.endswith(
        ("_receipt", "_receipts", "_report", "_reports", "_observation", "_observations")
    )


def is_evaluator_private_context_key(value: Any) -> bool:
    """Classify answer-authority fields before they enter product runtime.

    The evaluator-private vocabulary is a name-level contract: any key that
    names an answer-authority carrier is rejected regardless of its current
    value, because an empty field can be filled later and the name itself
    marks the field as evaluator-owned.
    """
    return _is_evaluator_private_context_key_cached(str(value or ""))


def find_evaluator_private_context_paths(value: Any) -> list[str]:
    """Return recursive JSON-style paths containing evaluator answer authority."""

    found: list[str] = []
    visited: set[int] = set()

    def walk(item: Any, path: str) -> None:
        if isinstance(item, (dict, list)):
            identity = id(item)
            if identity in visited:
                return
            visited.add(identity)
        if isinstance(item, dict):
            for raw_key, child in item.items():
                key = str(raw_key)
                child_path = f"{path}.{key}"
                if is_evaluator_private_context_key(key):
                    found.append(child_path)
                walk(child, child_path)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                walk(child, f"{path}[{index}]")

    walk(value, "$")
    return sorted(set(found))


def is_evaluator_secret_environment_name(name: str) -> bool:
    """Return whether an environment variable belongs outside product runtime."""

    normalized = str(name or "").strip().upper()
    if normalized.startswith(_EVALUATOR_PRIVATE_ENV_PREFIXES):
        return True
    if any(marker in normalized for marker in _EVALUATOR_PRIVATE_ENV_MARKERS):
        return True
    return "EVALUATOR" in normalized and any(
        marker in normalized
        for marker in ("HMAC", "KEYRING", "SIGNING_KEY", "SECRET", "TOKEN")
    )

from __future__ import annotations

"""Minimal process-boundary protocol shared by evaluator and product worker."""

import re
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


def _normalized_context_key(value: Any) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(value or "").strip())
    return re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9]+", "_", text)).strip(
        "_"
    ).lower()


def _is_content_bearing(value: Any) -> bool:
    """Whether a field value can actually carry answer-authority content."""

    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    if isinstance(value, str):
        return bool(str(value).strip())
    return value not in (None, False, 0)


def is_evaluator_private_context_key(value: Any, child: Any = None) -> bool:
    """Classify answer-authority fields before they enter product runtime.

    Evaluator-private vocabulary is only answer authority when the field
    actually carries content. The product's own knowledge-asset schema
    legitimately uses bookkeeping names such as ``is_ground_truth``,
    ``ground_truth_loaded``, ``ground_truth_generated_from_product_output``
    and empty ``ground_truth_fingerprint`` fields; those carry no hidden-GT
    content and must not fail the agent semantic linker. A non-empty
    ``ground_truth_ref`` / ``ground_truth_path`` / ``ground_truth_fingerprint``
    / ``expected_defects`` value remains fail-closed. When the value is
    unknown (``child is None``) the key stays conservatively private.
    """

    normalized = _normalized_context_key(value)
    if normalized in _EVALUATOR_PRIVATE_CONTEXT_KEYS:
        if child is None or _is_content_bearing(child):
            return True
    if "ground_truth" in normalized:
        if child is None or _is_content_bearing(child):
            return True
    if normalized.startswith(("private_evaluator_", "evaluator_private_")):
        if child is None or _is_content_bearing(child):
            return True
    if normalized.startswith("trusted_observation_"):
        if child is None or _is_content_bearing(child):
            return True
    if normalized.startswith("evaluator_") and normalized.endswith(
        ("_receipt", "_receipts", "_report", "_reports", "_observation", "_observations")
    ):
        if child is None or _is_content_bearing(child):
            return True
    return False


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
                if is_evaluator_private_context_key(key, child):
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

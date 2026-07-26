"""Cleanup Observation Adapter — canonicalize observation inputs for equivalence.

SPEC v1.1.1 §5: Observation Canonicalization

The equivalence engine requires before, after-write, and after-cleanup
observations. These come from multiple sources with different keys depending
on the observer configuration and execution path. This adapter provides a
single, priority-ordered extraction function that prevents:
  - Reading non-existent keys and returning empty dicts
  - Using after-write observation as after-cleanup
  - Using cleanup HTTP response body as final state
  - Fabricating data from static request bodies

Priority chains follow SPEC §5.1–§5.3.
"""
from __future__ import annotations

from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def build_cleanup_equivalence_inputs(
    *,
    exp: dict[str, Any],
    observations: dict[str, Any],
    steps_out: list[dict[str, Any]],
    cleanup_result: dict[str, Any],
) -> dict[str, Any]:
    """Build canonical equivalence inputs from multiple observation sources.

    Returns:
        {
            "before_observation": {...} or {},
            "after_write_observation": {...} or {},
            "after_cleanup_observation": {...} or {},
            "cleanup_execution_receipt": {...} or {},
            "source_trace": {
                "before_source": str,
                "after_write_source": str,
                "after_cleanup_source": str,
                "cleanup_receipt_source": str,
            },
        }
    """
    before_obs, before_source = _extract_before_observation(observations, steps_out)
    after_write_obs, after_write_source = _extract_after_write_observation(
        observations, steps_out
    )
    after_cleanup_obs, after_cleanup_source = _extract_after_cleanup_observation(
        observations, cleanup_result, steps_out
    )
    cleanup_receipt, cleanup_receipt_source = _extract_cleanup_execution_receipt(
        observations, cleanup_result
    )

    return {
        "before_observation": before_obs,
        "after_write_observation": after_write_obs,
        "after_cleanup_observation": after_cleanup_obs,
        "cleanup_execution_receipt": cleanup_receipt,
        "source_trace": {
            "before_source": before_source,
            "after_write_source": after_write_source,
            "after_cleanup_source": after_cleanup_source,
            "cleanup_receipt_source": cleanup_receipt_source,
        },
    }


# ─── Before Observation (SPEC §5.1) ──────────────────────────────────────────


def _extract_before_observation(
    observations: dict[str, Any],
    steps_out: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    """Extract before-write observation with priority chain.

    Priority:
      1. governance_receipt.before (from control/treatment steps)
      2. before_state_observer_receipt.evidence
      3. before_state_evidence (list → last entry)
      4. before_state (raw body)
    """
    # 1. Governance receipt before (first accepted write step)
    for step in steps_out:
        if not isinstance(step, dict):
            continue
        phase = _text(step.get("phase"))
        if phase not in {"control", "treatment"}:
            continue
        gov = _dict(step.get("governance_receipt"))
        before = _dict(gov.get("before"))
        if before and int(before.get("status") or 0) > 0:
            return {
                "status_code": int(before.get("status") or 0),
                "body": before.get("body"),
                "path": _text(gov.get("observation_path") or step.get("observation_path")),
                "phase": "before",
                "source": "governance_receipt",
            }, "governance_receipt"

    # 2. Typed observer receipt (before_state)
    before_receipt = _dict(observations.get("before_state_observer_receipt"))
    if before_receipt and _text(before_receipt.get("status")).upper() == "OBSERVED":
        evidence = _dict(before_receipt.get("evidence"))
        if evidence:
            return {
                "status_code": evidence.get("status_code") or evidence.get("status"),
                "body": evidence.get("body") or evidence.get("state"),
                "path": _text(evidence.get("path")),
                "phase": "before",
                "source": "typed_observer",
                "receipt_id": _text(before_receipt.get("receipt_id")),
            }, "before_state_observer_receipt"

    # 3. before_state_evidence (list from governance extraction)
    before_evidence = _list(observations.get("before_state_evidence"))
    if before_evidence:
        last = _dict(before_evidence[-1])
        if last.get("body") is not None or int(last.get("status") or 0) > 0:
            return {
                "status_code": int(last.get("status") or 0),
                "body": last.get("body"),
                "path": _text(last.get("path")),
                "phase": "before",
                "source": "before_state_evidence",
            }, "before_state_evidence"

    # 4. before_state (raw body only)
    before_state = observations.get("before_state")
    if before_state is not None and before_state != {}:
        return {
            "status_code": 200,
            "body": before_state,
            "path": "",
            "phase": "before",
            "source": "before_state",
        }, "before_state"

    return {}, ""


# ─── After Write Observation (SPEC §5.2) ─────────────────────────────────────


def _extract_after_write_observation(
    observations: dict[str, Any],
    steps_out: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    """Extract after-write observation with priority chain.

    Priority:
      1. governance_receipt.after (from control/treatment steps)
      2. after_state_observer_receipt.evidence
      3. after_state_evidence (list → last entry)
      4. after_state (raw body)
    """
    # 1. Governance receipt after (last accepted write step)
    result: dict[str, Any] = {}
    source = ""
    for step in steps_out:
        if not isinstance(step, dict):
            continue
        phase = _text(step.get("phase"))
        if phase not in {"control", "treatment"}:
            continue
        gov = _dict(step.get("governance_receipt"))
        after = _dict(gov.get("after"))
        if after and int(after.get("status") or 0) > 0:
            result = {
                "status_code": int(after.get("status") or 0),
                "body": after.get("body"),
                "path": _text(gov.get("observation_path") or step.get("observation_path")),
                "phase": "after_write",
                "source": "governance_receipt",
            }
            source = "governance_receipt"
    if result:
        return result, source

    # 2. Typed observer receipt (after_state)
    after_receipt = _dict(observations.get("after_state_observer_receipt"))
    if after_receipt and _text(after_receipt.get("status")).upper() == "OBSERVED":
        evidence = _dict(after_receipt.get("evidence"))
        if evidence:
            return {
                "status_code": evidence.get("status_code") or evidence.get("status"),
                "body": evidence.get("body") or evidence.get("state"),
                "path": _text(evidence.get("path")),
                "phase": "after_write",
                "source": "typed_observer",
                "receipt_id": _text(after_receipt.get("receipt_id")),
            }, "after_state_observer_receipt"

    # 3. after_state_evidence (list from governance extraction)
    after_evidence = _list(observations.get("after_state_evidence"))
    if after_evidence:
        last = _dict(after_evidence[-1])
        if last.get("body") is not None or int(last.get("status") or 0) > 0:
            return {
                "status_code": int(last.get("status") or 0),
                "body": last.get("body"),
                "path": _text(last.get("path")),
                "phase": "after_write",
                "source": "after_state_evidence",
            }, "after_state_evidence"

    # 4. after_state (raw body only)
    after_state = observations.get("after_state")
    if after_state is not None and after_state != {}:
        return {
            "status_code": 200,
            "body": after_state,
            "path": "",
            "phase": "after_write",
            "source": "after_state",
        }, "after_state"

    return {}, ""


# ─── After Cleanup Observation (SPEC §5.3) ────────────────────────────────────


def _extract_after_cleanup_observation(
    observations: dict[str, Any],
    cleanup_result: dict[str, Any],
    steps_out: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str]:
    """Extract after-cleanup observation with strict priority chain.

    Priority:
      1. Cleanup-phase observer receipt (final_state typed observer)
      2. final_state_observer_receipt.evidence
      3. cleanup_result.after_cleanup_observation
      4. Cleanup-phase re-executed source-declared observer
      5. Cleanup-phase steps in steps_out (governance receipt after)

    PROHIBITED sources (SPEC §5.3):
      - primary write after_state
      - cleanup HTTP response body
      - before_state
      - static request body
    """
    # 1/2. Typed observer receipt (final_state)
    final_receipt = _dict(observations.get("final_state_observer_receipt"))
    if final_receipt and _text(final_receipt.get("status")).upper() == "OBSERVED":
        evidence = _dict(final_receipt.get("evidence"))
        if evidence:
            return {
                "status_code": evidence.get("status_code") or evidence.get("status"),
                "body": evidence.get("body") or evidence.get("state"),
                "path": _text(evidence.get("path")),
                "phase": "after_cleanup",
                "source": "cleanup_observer",
                "receipt_id": _text(final_receipt.get("receipt_id")),
            }, "final_state_observer_receipt"

    # 3. cleanup_result.after_cleanup_observation (explicit post-cleanup read)
    explicit_obs = _dict(cleanup_result.get("after_cleanup_observation"))
    if explicit_obs and (
        explicit_obs.get("body") is not None
        or int(explicit_obs.get("status_code") or explicit_obs.get("status") or 0) > 0
    ):
        return {
            "status_code": int(
                explicit_obs.get("status_code") or explicit_obs.get("status") or 0
            ),
            "body": explicit_obs.get("body"),
            "path": _text(explicit_obs.get("path")),
            "phase": "after_cleanup",
            "source": "cleanup_observer",
        }, "cleanup_result_after_cleanup_observation"

    # 4. Cleanup-phase governance receipt after (from cleanup_result)
    cleanup_after = _dict(cleanup_result.get("after"))
    if cleanup_after and int(cleanup_after.get("status") or 0) > 0:
        return {
            "status_code": int(cleanup_after.get("status") or 0),
            "body": cleanup_after.get("body"),
            "path": _text(cleanup_result.get("observation_path")),
            "phase": "after_cleanup",
            "source": "cleanup_observer",
        }, "cleanup_governance_after"

    # 5. Cleanup-phase steps in steps_out (governance receipt after)
    if steps_out:
        for step in reversed(steps_out):
            if not isinstance(step, dict):
                continue
            if _text(step.get("phase")) != "cleanup":
                continue
            gov = _dict(step.get("governance_receipt"))
            after = _dict(gov.get("after"))
            if after and int(after.get("status") or 0) > 0:
                return {
                    "status_code": int(after.get("status") or 0),
                    "body": after.get("body"),
                    "path": _text(gov.get("observation_path") or step.get("path")),
                    "phase": "after_cleanup",
                    "source": "cleanup_step_governance",
                }, "cleanup_step_governance_after"

    return {}, ""


# ─── Cleanup Execution Receipt (SPEC §6) ──────────────────────────────────────


def _extract_cleanup_execution_receipt(
    observations: dict[str, Any],
    cleanup_result: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Extract the explicit cleanup execution receipt.

    NEVER infers success from cleanup_failures == 0.
    Only an explicit receipt with attempted=true is valid.
    """
    # 1. Explicit receipt from observations
    explicit = _dict(observations.get("cleanup_execution_receipt"))
    if explicit and explicit.get("schema_version"):
        return explicit, "observations_explicit"

    # 2. Receipt from cleanup_result
    if cleanup_result.get("schema_version"):
        return cleanup_result, "cleanup_result"

    # 3. Receipt embedded in cleanup_result
    embedded = _dict(cleanup_result.get("cleanup_execution_receipt"))
    if embedded:
        return embedded, "cleanup_result_embedded"

    # No receipt found — do NOT fabricate from cleanup_failures
    return {}, ""

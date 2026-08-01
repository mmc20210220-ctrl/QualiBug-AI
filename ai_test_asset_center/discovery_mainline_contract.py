"""Immutable authority contract for one QualiBug discovery run.

The authority is selected before planning or execution.  Runtime failures may
never mutate it or switch to another discovery path.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, NotRequired, TypedDict, cast


MAINLINE_RUN_SCHEMA = "qualibug.discovery-mainline-run.v1"
MAINLINE_AUTHORITIES = frozenset({"legacy_champion", "experiment_candidate"})
EVALUATION_MODES = frozenset({"operational", "replay", "shadow"})


class MainlineContractError(ValueError):
    """A run cannot establish one immutable discovery authority."""


class MainlineRunContract(TypedDict):
    schema_version: str
    mainline_authority: str
    run_id: str
    campaign_id: str
    target_id: str
    environment_id: str
    policy_version: str
    evaluation_mode: str
    customer_outputs_published: bool
    product_evaluation_submission_published: bool
    private_evaluator_observation_allowed: bool
    contract_fingerprint: str
    source_snapshot_hash: NotRequired[str]


def _required(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MainlineContractError(f"{field}_missing")
    return text


def _fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_mainline_run_contract(
    *,
    mainline_authority: str,
    run_id: str,
    campaign_id: str,
    target_id: str,
    environment_id: str,
    policy_version: str,
    evaluation_mode: str,
    source_snapshot_hash: str = "",
) -> MainlineRunContract:
    """Build a content-addressed pre-run authority contract."""

    authority = _required(mainline_authority, "mainline_authority")
    mode = _required(evaluation_mode, "evaluation_mode")
    if authority not in MAINLINE_AUTHORITIES:
        raise MainlineContractError(f"mainline_authority_invalid:{authority}")
    if mode not in EVALUATION_MODES:
        raise MainlineContractError(f"evaluation_mode_invalid:{mode}")

    evaluator_owned = mode in {"replay", "shadow"}
    payload: dict[str, Any] = {
        "schema_version": MAINLINE_RUN_SCHEMA,
        "mainline_authority": authority,
        "run_id": _required(run_id, "run_id"),
        "campaign_id": _required(campaign_id, "campaign_id"),
        "target_id": _required(target_id, "target_id"),
        "environment_id": _required(environment_id, "environment_id"),
        "policy_version": _required(policy_version, "policy_version"),
        "evaluation_mode": mode,
        "customer_outputs_published": mode == "operational",
        "product_evaluation_submission_published": mode == "operational",
        "private_evaluator_observation_allowed": evaluator_owned,
    }
    snapshot = str(source_snapshot_hash or "").strip()
    if snapshot:
        payload["source_snapshot_hash"] = snapshot
    payload["contract_fingerprint"] = _fingerprint(payload)
    return cast(MainlineRunContract, payload)


def validate_mainline_run_contract(value: dict[str, Any]) -> MainlineRunContract:
    """Validate every derived field and the content fingerprint."""

    if not isinstance(value, dict):
        raise MainlineContractError("mainline_contract_not_object")
    if value.get("schema_version") != MAINLINE_RUN_SCHEMA:
        raise MainlineContractError("mainline_contract_schema_invalid")
    expected = build_mainline_run_contract(
        mainline_authority=str(value.get("mainline_authority") or ""),
        run_id=str(value.get("run_id") or ""),
        campaign_id=str(value.get("campaign_id") or ""),
        target_id=str(value.get("target_id") or ""),
        environment_id=str(value.get("environment_id") or ""),
        policy_version=str(value.get("policy_version") or ""),
        evaluation_mode=str(value.get("evaluation_mode") or ""),
        source_snapshot_hash=str(value.get("source_snapshot_hash") or ""),
    )
    for field, expected_value in expected.items():
        if value.get(field) != expected_value:
            if field == "contract_fingerprint":
                raise MainlineContractError("mainline_contract_fingerprint_mismatch")
            raise MainlineContractError(f"mainline_contract_field_mismatch:{field}")
    return expected

"""Focused coordinator for one immutable discovery authority."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .discovery_mainline_contract import (
    MainlineContractError,
    MainlineRunContract,
    validate_mainline_run_contract,
)


@dataclass(frozen=True)
class DiscoveryMainlineInputs:
    project: str
    root: Path
    prd_text: str
    api_spec_text: str
    db_schema_text: str
    approved_base_url: str
    campaign_context: dict[str, Any]
    existing_findings: Sequence[dict[str, Any]] = ()


@dataclass(frozen=True)
class DiscoveryPlanningBundle:
    mainline_run: MainlineRunContract
    behavior_ir: dict[str, Any]
    obligations: dict[str, Any]
    experiments: dict[str, Any]


CampaignBuilder = Callable[[DiscoveryMainlineInputs], Any]
PlanBuilder = Callable[[DiscoveryMainlineInputs, Any], Any]
MainlineRunner = Callable[[DiscoveryMainlineInputs, Any, Any], dict[str, Any]]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _campaign_id(campaign: Any) -> str:
    value = (
        campaign.get("campaign_id")
        if isinstance(campaign, dict)
        else getattr(campaign, "campaign_id", "")
    )
    campaign_id = _text(value)
    if not campaign_id:
        raise MainlineContractError("mainline_campaign_identity_missing")
    return campaign_id


def _plan_contract(plan: Any) -> MainlineRunContract:
    value = (
        plan.get("mainline_run")
        if isinstance(plan, dict)
        else getattr(plan, "mainline_run", None)
    )
    return validate_mainline_run_contract(value)


def assert_result_matches_authority(
    result: dict[str, Any],
    contract: MainlineRunContract,
) -> None:
    if not isinstance(result, dict):
        raise MainlineContractError("mainline_result_not_object")
    observed = validate_mainline_run_contract(result.get("mainline_run"))
    if observed["contract_fingerprint"] != contract["contract_fingerprint"]:
        raise MainlineContractError("mainline_result_authority_mismatch")


def run_discovery_mainline(
    inputs: DiscoveryMainlineInputs,
    *,
    build_campaign: CampaignBuilder,
    build_plan: PlanBuilder,
    legacy_runner: MainlineRunner,
    experiment_runner: MainlineRunner,
) -> dict[str, Any]:
    """Build campaign, plan once, then invoke exactly one authority runner."""

    if not isinstance(inputs, DiscoveryMainlineInputs):
        raise MainlineContractError("mainline_inputs_invalid")
    input_authority = _text(inputs.campaign_context.get("mainline_authority"))
    if not input_authority:
        raise MainlineContractError("mainline_input_authority_missing")

    campaign = build_campaign(inputs)
    campaign_id = _campaign_id(campaign)
    plan = build_plan(inputs, campaign)
    contract = _plan_contract(plan)
    if contract["campaign_id"] != campaign_id:
        raise MainlineContractError("mainline_campaign_identity_mismatch")
    if contract["mainline_authority"] != input_authority:
        raise MainlineContractError("mainline_input_authority_mismatch")

    runner = (
        legacy_runner
        if contract["mainline_authority"] == "legacy_champion"
        else experiment_runner
    )
    result = runner(inputs, campaign, plan)
    assert_result_matches_authority(result, contract)
    return result

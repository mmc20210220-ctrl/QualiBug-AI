from __future__ import annotations

import importlib

import pytest


def _contract_module():
    return importlib.import_module("ai_test_asset_center.discovery_mainline_contract")


def _contract(**overrides):
    values = {
        "mainline_authority": "experiment_candidate",
        "run_id": "RUN-1",
        "campaign_id": "CMP-1",
        "target_id": "TARGET-1",
        "environment_id": "ENV-1",
        "policy_version": "v2",
        "evaluation_mode": "replay",
    }
    values.update(overrides)
    return _contract_module().build_mainline_run_contract(**values)


def test_mainline_contract_requires_explicit_authority() -> None:
    module = _contract_module()

    with pytest.raises(module.MainlineContractError, match="mainline_authority_missing"):
        _contract(mainline_authority="")


def test_mainline_contract_rejects_unknown_authority() -> None:
    module = _contract_module()

    with pytest.raises(module.MainlineContractError, match="mainline_authority_invalid"):
        _contract(mainline_authority="automatic_fallback")


def test_shadow_contract_separates_product_and_private_evaluator_scopes() -> None:
    contract = _contract(evaluation_mode="shadow")

    assert contract["customer_outputs_published"] is False
    assert contract["product_evaluation_submission_published"] is False
    assert contract["private_evaluator_observation_allowed"] is True


def test_operational_contract_does_not_authorize_private_evaluator_observation() -> None:
    contract = _contract(evaluation_mode="operational")

    assert contract["customer_outputs_published"] is True
    assert contract["product_evaluation_submission_published"] is True
    assert contract["private_evaluator_observation_allowed"] is False


def test_replay_contract_is_private_and_does_not_publish_customer_output() -> None:
    contract = _contract(evaluation_mode="replay")

    assert contract["customer_outputs_published"] is False
    assert contract["product_evaluation_submission_published"] is False
    assert contract["private_evaluator_observation_allowed"] is True


def test_mainline_contract_fingerprint_detects_tampering() -> None:
    module = _contract_module()
    contract = _contract()
    contract["mainline_authority"] = "legacy_champion"

    with pytest.raises(module.MainlineContractError, match="mainline_contract_fingerprint_mismatch"):
        module.validate_mainline_run_contract(contract)

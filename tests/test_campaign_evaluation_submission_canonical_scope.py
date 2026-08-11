from __future__ import annotations

import pytest

from ai_test_asset_center.campaign_api_contract import (
    CampaignContractError,
    SUBMISSION_SCHEMA,
    _fingerprinted,
    _validate_redacted_evaluation_submission,
)
from ai_test_asset_center.discovery_mainline_contract import (
    build_mainline_run_contract,
)
from tests.phase3_gate_support import (
    build_formal_evaluation_scope,
    build_formal_scope_contract,
)


def _submission() -> tuple[dict, list[dict]]:
    mainline = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="run-canonical",
        campaign_id="campaign-canonical",
        target_id="target-1",
        environment_id="environment-1",
        policy_version="policy-1",
        evaluation_mode="replay",
    )
    occurrences, ledger = build_formal_evaluation_scope(
        [{"finding_id": "finding-1"}, {"finding_id": "finding-2"}],
        run_id="run-canonical",
        campaign_id="campaign-canonical",
        target_id="target-1",
        environment_id="environment-1",
        policy_version="policy-1",
        evaluation_mode="replay",
    )
    scope = build_formal_scope_contract(
        mainline_run=mainline,
        findings=occurrences,
        obligation_attempt_ledger=ledger,
    )
    representatives = scope["formal_count_projection"][
        "canonical_representative_findings"
    ]
    envelope = _fingerprinted({
        "schema_version": SUBMISSION_SCHEMA,
        "mainline_run": mainline,
        **scope,
        "scan_result": {
            "findings": representatives,
            "delivery_occurrences": occurrences,
            "candidate_findings": [],
            "obligation_attempt_ledger": ledger,
            **scope,
        },
    })
    return envelope, occurrences


def test_submission_scores_one_canonical_row_and_keeps_occurrences_as_evidence() -> None:
    envelope, occurrences = _submission()

    _validate_redacted_evaluation_submission(envelope)

    assert len(envelope["scan_result"]["findings"]) == 1
    assert len(occurrences) == 2


def test_submission_rejects_occurrences_in_the_scoring_scope() -> None:
    envelope, occurrences = _submission()
    envelope["scan_result"]["findings"] = occurrences
    envelope = _fingerprinted({
        key: value for key, value in envelope.items() if key != "fingerprint"
    })

    with pytest.raises(CampaignContractError, match="canonical_findings"):
        _validate_redacted_evaluation_submission(envelope)

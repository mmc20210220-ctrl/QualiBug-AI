from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ai_test_asset_center.discovery_mainline_contract import (
    build_mainline_run_contract,
)
from tools.normalize_evaluation_run_envelope import normalize_envelope
from tools.normalize_evaluation_run_envelope import main as normalize_main
from tests.phase3_gate_support import (
    build_formal_evaluation_scope,
    build_formal_scope_contract,
)


def _empty_authority_envelope() -> dict:
    mainline = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="run-1",
        campaign_id="campaign-1",
        target_id="target-1",
        environment_id="environment-1",
        policy_version="policy-1",
        evaluation_mode="replay",
    )
    findings, ledger = build_formal_evaluation_scope(
        [
            {"finding_id": "finding-1"},
            {"finding_id": "finding-2"},
        ],
        run_id="run-1",
        campaign_id="campaign-1",
        target_id="target-1",
        environment_id="environment-1",
        policy_version="policy-1",
        evaluation_mode="replay",
    )
    scope = build_formal_scope_contract(
        mainline_run=mainline,
        findings=findings,
        obligation_attempt_ledger=ledger,
    )
    return {
        "run_id": "run-1",
        "campaign_id": "campaign-1",
        "policy_id": "policy-1",
        "evaluation_mode": "replay",
        "pipeline_health": {"status": "OK"},
        "operational_metrics": {},
        "mainline_run": mainline,
        **scope,
        "scan_result": {
            "findings": list(
                scope["formal_count_projection"][
                    "canonical_representative_findings"
                ]
            ),
            "delivery_occurrences": findings,
            "candidate_findings": [],
            "obligation_attempt_ledger": ledger,
            **scope,
        },
    }


def test_normalizer_preserves_complete_formal_authority() -> None:
    raw = _empty_authority_envelope()

    normalized = normalize_envelope(raw)

    assert normalized["run_id"] == "run-1"
    assert normalized["campaign_id"] == "campaign-1"
    assert normalized["mainline_run"] == raw["mainline_run"]
    assert normalized["scan_result"]["obligation_attempt_ledger"] == (
        raw["scan_result"]["obligation_attempt_ledger"]
    )
    assert normalized["formal_delivery_authority"] == (
        raw["formal_delivery_authority"]
    )
    assert normalized["scan_result"]["findings"] == raw[
        "formal_count_projection"
    ]["canonical_representative_findings"]
    assert normalized["scan_result"]["findings"] != normalized[
        "scan_result"
    ]["delivery_occurrences"]


def test_normalizer_preserves_evaluator_execution_boundary_without_synthesis() -> None:
    raw = _empty_authority_envelope()
    raw["process_boundary"] = {"schema_version": "boundary-test"}
    raw["scan_result"]["process_boundary"] = raw["process_boundary"]
    raw["execution_attestation"] = {
        "schema_version": "attestation-test",
        "status": "VERIFIED",
    }

    normalized = normalize_envelope(raw)

    assert normalized["process_boundary"] == raw["process_boundary"]
    assert normalized["scan_result"]["process_boundary"] == (
        raw["process_boundary"]
    )
    assert normalized["execution_attestation"] == (
        raw["execution_attestation"]
    )


@pytest.mark.parametrize("field", ["run_id", "policy_id", "mainline_run"])
def test_normalizer_never_invents_missing_authority_identity(field: str) -> None:
    raw = _empty_authority_envelope()
    raw.pop(field, None)

    with pytest.raises(ValueError, match=field):
        normalize_envelope(raw)


def test_normalizer_redacts_occurrence_evidence_without_changing_canonical_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mainline = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="run-secret",
        campaign_id="campaign-secret",
        target_id="target-1",
        environment_id="environment-1",
        policy_version="policy-1",
        evaluation_mode="replay",
    )
    findings, ledger = build_formal_evaluation_scope(
        [{
            "finding_id": "finding-secret",
            "request_headers": {
                "Authorization": "Bearer evaluator-secret-token"
            },
        }],
        run_id="run-secret",
        campaign_id="campaign-secret",
        target_id="target-1",
        environment_id="environment-1",
        policy_version="policy-1",
        evaluation_mode="replay",
    )
    authority = build_formal_scope_contract(
        mainline_run=mainline,
        findings=findings,
        obligation_attempt_ledger=ledger,
    )
    raw = {
        "run_id": "run-secret",
        "campaign_id": "campaign-secret",
        "policy_id": "policy-1",
        "evaluation_mode": "replay",
        "pipeline_health": {"status": "OK"},
        "operational_metrics": {},
        "mainline_run": mainline,
        **authority,
        "scan_result": {
            "findings": list(
                authority["formal_count_projection"][
                    "canonical_representative_findings"
                ]
            ),
            "delivery_occurrences": findings,
            "candidate_findings": [],
            "obligation_attempt_ledger": ledger,
            **authority,
        },
    }
    source = tmp_path / "input.json"
    destination = tmp_path / "output.json"
    source.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "normalize_evaluation_run_envelope.py",
            "--input",
            str(source),
            "--output",
            str(destination),
        ],
    )

    normalize_main()

    assert destination.exists()
    serialized = destination.read_text(encoding="utf-8")
    assert "evaluator-secret-token" not in serialized
    normalized = json.loads(serialized)
    assert len(normalized["scan_result"]["findings"]) == 1
    assert len(normalized["scan_result"]["delivery_occurrences"]) == 1

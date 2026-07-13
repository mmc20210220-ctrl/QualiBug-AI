from __future__ import annotations

import json

import pytest

from ai_test_asset_center.__main__ import (
    CanonicalProductScopeError,
    _canonical_product_scope,
)
from ai_test_asset_center.canonical_defect_registry import (
    _semantic_value,
    canonical_representative_findings,
)
from ai_test_asset_center.artifact_redactor import redact_and_validate
from ai_test_asset_center.customer_safe_report import report_findings
from ai_test_asset_center.discovery_mainline_contract import (
    build_mainline_run_contract,
)
from ai_test_asset_center.display_ready_formatter import (
    format_findings_display_ready,
)
from ai_test_asset_center.evidence_artifact_store import (
    load_evidence_bundle,
    persist_evidence_bundle,
)
from ai_test_asset_center.private_pilot_service import (
    PrivatePilotHandler,
    _canonical_display_scope_matches,
    _get_continuous_state,
    _update_continuous_state,
)
from ai_test_asset_center.discovery_quality_projection import (
    attach_quality_projection_to_scan_result,
)
import ai_test_asset_center.private_pilot_service as private_pilot_service
from tests.phase3_gate_support import (
    build_formal_evaluation_scope,
    build_formal_scope_contract,
)


def _canonical_payload(evaluation_mode: str = "operational") -> dict:
    identity = {
        "run_id": "run-product-canonical",
        "campaign_id": "campaign-product-canonical",
        "target_id": "target-product-canonical",
        "environment_id": "environment-product-canonical",
        "policy_version": "policy-product-canonical",
        "evaluation_mode": evaluation_mode,
    }
    mainline = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        **identity,
    )
    occurrences, ledger = build_formal_evaluation_scope(
        [
            {
                "finding_id": "occurrence-1",
                "title": "display wording must not define identity",
                "severity": "P1",
            },
            {
                "finding_id": "occurrence-2",
                "title": "different wording for the same observed defect",
                "severity": "P2",
            },
        ],
        mainline_authority="experiment_candidate",
        **identity,
    )
    assert ledger is not None
    scope = build_formal_scope_contract(
        mainline_run=mainline,
        findings=occurrences,
        obligation_attempt_ledger=ledger,
    )
    representatives = canonical_representative_findings(
        scope["canonical_defect_registry"],
        deliverable_occurrences=occurrences,
    )
    return {
        "mainline_run": mainline,
        "obligation_attempt_ledger": ledger,
        **scope,
        "findings": representatives,
        "candidate_findings": [
            {
                "finding_id": "candidate-1",
                "title": "unverified clue",
                "mainline_run": {
                    "contract_fingerprint": mainline["contract_fingerprint"]
                },
            }
        ],
    }


def test_scan_product_scope_uses_one_registry_representative_not_occurrences() -> None:
    payload = _canonical_payload()

    scope = _canonical_product_scope(payload)

    assert len(payload["delivery_occurrences"]) == 2
    assert len(scope["findings"]) == 1
    assert scope["findings"][0]["canonical_defect_id"].startswith("cdef_")
    assert scope["delivery_occurrences"] == payload["delivery_occurrences"]
    assert scope["candidates"] == payload["candidate_findings"]


def test_scan_product_scope_rejects_finding_outside_registry() -> None:
    payload = _canonical_payload()
    payload["findings"] = [
        *payload["findings"],
        {"canonical_defect_id": "cdef_" + "f" * 32, "title": "forged"},
    ]

    with pytest.raises(CanonicalProductScopeError, match="canonical_finding_scope_mismatch"):
        _canonical_product_scope(payload)


def test_display_formatter_does_not_title_dedupe_canonical_defects() -> None:
    risks = [
        {
            "canonical_defect_id": "cdef_" + "1" * 32,
            "title": "same display title",
            "severity": "P1",
        },
        {
            "canonical_defect_id": "cdef_" + "2" * 32,
            "title": "same display title",
            "severity": "P1",
        },
    ]

    displayed, metrics = format_findings_display_ready(risks, {}, {})

    assert {row["canonical_defect_id"] for row in displayed} == {
        "cdef_" + "1" * 32,
        "cdef_" + "2" * 32,
    }
    assert metrics["display_contract"]["canonical_defect_count"] == 2


def test_display_scope_accepts_customer_sorting_but_rejects_duplicates() -> None:
    first = "cdef_" + "1" * 32
    second = "cdef_" + "2" * 32

    assert _canonical_display_scope_matches([first, second], [second, first])
    assert not _canonical_display_scope_matches([first, second], [first, first])
    assert not _canonical_display_scope_matches([first, second], [first])


def test_quality_classification_uses_canonical_representatives_not_occurrences() -> None:
    payload = _canonical_payload()
    payload["findings"] = list(payload["delivery_occurrences"])

    projected = attach_quality_projection_to_scan_result(payload)
    classification = projected["finding_classification"]

    assert projected["formal_count_projection"]["canonical_defect_count"] == 1
    assert classification["counts"]["deliverable"] == 1
    assert classification["deliverable"][0]["canonical_defect_id"].startswith(
        "cdef_"
    )


def test_shadow_quality_classification_never_publishes_deliverables() -> None:
    payload = _canonical_payload("shadow")

    projected = attach_quality_projection_to_scan_result(payload)
    classification = projected["finding_classification"]

    assert projected["mainline_run"]["customer_outputs_published"] is False
    assert classification["deliverable"] == []
    assert classification["counts"]["deliverable"] == 0
    assert classification["counts"]["shadow"] >= 1


def test_canonical_semantic_signature_never_persists_secret_text() -> None:
    secret = "Authorization: Bearer very-secret-token"
    signature = _semantic_value(secret, assertion_kind="equals")
    payload = {"actual_signature": signature}

    redacted, receipt = redact_and_validate(payload)

    assert redacted == payload
    assert receipt["safe_to_persist"] is True
    assert "very-secret-token" not in json.dumps(payload)
    assert signature == _semantic_value(secret, assertion_kind="equals")
    assert signature != _semantic_value(
        "Authorization: Bearer another-token",
        assertion_kind="equals",
    )


def test_customer_report_ignores_noncanonical_and_occurrence_rows() -> None:
    payload = _canonical_payload()
    canonical = payload["findings"][0]
    report = {
        **payload,
        "real_findings": [
            canonical,
            {"finding_id": "legacy-row", "title": "legacy title row"},
        ],
    }

    findings = report_findings(report)

    assert [row["canonical_defect_id"] for row in findings] == [
        canonical["canonical_defect_id"]
    ]


def test_evidence_bundle_separates_canonical_defects_from_occurrences(tmp_path) -> None:
    payload = _canonical_payload()
    bundle = persist_evidence_bundle(
        "project-canonical",
        root=tmp_path,
        run_id=payload["mainline_run"]["run_id"],
        campaign={"campaign_id": payload["mainline_run"]["campaign_id"]},
        runtime_contract={},
        execution_status="executed",
        auto_har={"status": "captured"},
        evidence_graphs=[],
        findings=payload["findings"],
        canonical_defect_registry=payload["canonical_defect_registry"],
        delivery_occurrences=payload["delivery_occurrences"],
    )

    manifest = load_evidence_bundle(
        "project-canonical", bundle["bundle_id"], root=tmp_path
    )
    artifact_names = {row["name"] for row in manifest["artifacts"]}

    assert manifest["canonical_defect_count"] == 1
    assert manifest["delivery_occurrence_count"] == 2
    assert "canonical_defect_registry" in artifact_names
    assert "delivery_occurrences" in artifact_names


def test_private_pilot_current_scope_ignores_history_and_title_dedupe() -> None:
    payload = _canonical_payload()
    payload["real_findings"] = [
        *payload["findings"],
        {"finding_id": "historical", "title": "historical shelf row"},
    ]
    payload["total_findings"] = 99

    scope = PrivatePilotHandler._canonical_current_report_scope(payload)

    assert len(scope["defects"]) == 1
    assert scope["formal_customer_deliverable_count"] == 1
    assert scope["candidates"] == payload["candidate_findings"]
    assert scope["legacy_diagnostics"]["ignored_declared_total"] == 99


def test_command_center_counts_only_current_canonical_registry(
    monkeypatch, tmp_path
) -> None:
    payload = _canonical_payload()
    payload.update({
        "project_name": "canonical-project",
        "generated_at_utc": "2026-07-12T00:00:00Z",
        "total_findings": 99,
        "raw_total": 99,
        "real_findings": [
            *payload["findings"],
            {"finding_id": "historical", "title": "history must not count"},
        ],
    })
    handler = PrivatePilotHandler.__new__(PrivatePilotHandler)
    handler.headers = {}
    monkeypatch.setattr(handler, "_load_v12_report", lambda *_: payload)
    monkeypatch.setattr(handler, "_load_current_scan_report", lambda *_: payload)
    monkeypatch.setattr(handler, "_load_enterprise_docs", lambda *_: [])
    monkeypatch.setattr(handler, "_load_knowledge_summary", lambda *_: {})
    monkeypatch.setattr(handler, "_load_db_findings", lambda *_: [])
    monkeypatch.setattr(handler, "_load_perf_regressions", lambda *_: [])
    monkeypatch.setattr(handler, "_load_spectrum_findings", lambda *_: [])
    monkeypatch.setattr(handler, "_load_multi_layer_findings", lambda *_: [])
    monkeypatch.setattr(handler, "_scan_counter", lambda *_: {})
    monkeypatch.setattr(
        private_pilot_service,
        "_load_real_project_discovery_payload",
        lambda *_: {},
    )

    envelope = handler._build_command_center("canonical-project", tmp_path)
    data = envelope["data"]

    assert len(data["defects"]) == 1
    assert data["defects"][0]["canonical_defect_id"] == payload["findings"][0][
        "canonical_defect_id"
    ]
    assert data["scan_meta"]["total_findings"] == 1
    assert data["scan_meta"]["customer_ready_defects"] == 1
    assert data["executive_summary"]["total_bugs_found"] == 1
    assert data["legacy_product_path_diagnostics"][
        "affects_current_counts_or_readiness"
    ] is False


def test_continuous_state_is_disabled_without_canonical_ids(tmp_path) -> None:
    _update_continuous_state(
        tmp_path,
        "project-no-canonical",
        {"total_findings": 25, "findings": [{"title": "legacy"}]},
    )

    state_path = (
        tmp_path
        / "platform_workspace"
        / "project-no-canonical"
        / "defect_discovery"
        / "continuous_discovery_state.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert state["status"] == "disabled"
    assert state["reason"] == "canonical_defect_registry_required_for_delta"
    assert state["runs"] == []
    projected = _get_continuous_state(tmp_path, "project-no-canonical")
    assert projected["status"] == "disabled"
    assert projected["reason"] == state["reason"]


def test_continuous_state_delta_uses_canonical_ids(tmp_path) -> None:
    payload = _canonical_payload()

    _update_continuous_state(tmp_path, "project-canonical", payload)
    _update_continuous_state(tmp_path, "project-canonical", payload)

    state_path = (
        tmp_path
        / "platform_workspace"
        / "project-canonical"
        / "defect_discovery"
        / "continuous_discovery_state.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert state["runs"][0]["new_canonical_defect_count"] == 1
    assert state["runs"][1]["new_canonical_defect_count"] == 0
    assert state["runs"][1]["canonical_defect_ids"] == state["canonical_defect_ids"]

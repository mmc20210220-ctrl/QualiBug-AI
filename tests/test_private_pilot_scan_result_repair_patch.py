from pathlib import Path

from ai_test_asset_center.private_pilot_scan_result_repair_patch import _repair_scan_result_if_needed


class DummyScannerModule:
    @staticmethod
    def _persist_execution_evidence(project, root, scan_id, campaign, runtime_contract, execution_status, v12):
        return {
            "status": "persisted",
            "bundle_id": f"bundle_{scan_id}",
        }


def test_repair_restores_only_findings_downgraded_by_scope_failure(tmp_path: Path) -> None:
    result = {
        "success": True,
        "scan_id": "scan_demo_1",
        "execution_status": "completed",
        "campaign": {"campaign_id": "campaign_1"},
        "runtime_contract": {"status": "approved"},
        "v12": {"findings": [{"title": "越权退款"}]},
        "evidence_bundle": {"status": "persistence_failed", "reason": "UnboundLocalError"},
        "findings": [],
        "candidate_findings": [
            {
                "title": "越权退款",
                "confirmation_status": "inconclusive",
                "evidence_persistence_status": "failed",
            },
            {
                "title": "普通候选线索",
                "confirmation_status": "candidate",
            },
        ],
        "layers": {"source_grounded_discovery": {"findings": 0, "candidates": 2}},
    }

    repaired = _repair_scan_result_if_needed(
        result,
        project="demo",
        root=tmp_path,
        scanner_module=DummyScannerModule,
    )

    assert repaired["evidence_bundle"]["status"] == "persisted"
    assert repaired["total_findings"] == 1
    assert repaired["total_candidates"] == 1
    assert repaired["findings"][0]["confirmation_status"] == "confirmed"
    assert repaired["findings"][0]["evidence_persistence_status"] == "persisted"
    assert repaired["candidate_findings"][0]["title"] == "普通候选线索"
    assert repaired["scan_result_repair"]["status"] == "repaired"


def test_repair_does_not_upgrade_non_scope_failure(tmp_path: Path) -> None:
    result = {
        "evidence_bundle": {"status": "persistence_failed", "reason": "OSError"},
        "candidate_findings": [{"title": "线索", "evidence_persistence_status": "failed"}],
    }

    repaired = _repair_scan_result_if_needed(
        result,
        project="demo",
        root=tmp_path,
        scanner_module=DummyScannerModule,
    )

    assert repaired is result
    assert "total_findings" not in repaired

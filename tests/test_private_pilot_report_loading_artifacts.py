"""Artifactized intelligence reports remain usable on the read path."""
from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.artifact_store import default_artifact_store
from ai_test_asset_center.private_pilot_report_loading import ReportLoadingMixin


def test_compact_intelligence_report_hydrates_authority_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("QUALIBUG_ARTIFACT_COMPRESSION", "none")
    store = default_artifact_store(tmp_path)
    ledger = {
        "schema_version": "qualibug.obligation-attempt-ledger.v1",
        "run_id": "run-1",
        "campaign_id": "campaign-1",
        "attempts": [],
    }
    ref = store.put(ledger, "OBLIGATION_ATTEMPT_LEDGER").artifact_id
    report_path = (
        tmp_path / "platform_outputs" / "project" / "intelligence_report.json"
    )
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "project": "project",
                "obligation_attempt_ledger_ref": ref,
                "obligation_attempt_ledger_summary": {
                    "artifactized": True,
                    "ref": ref,
                },
                "report_artifactization": {
                    "artifactized_keys": ["obligation_attempt_ledger"],
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = ReportLoadingMixin._read_scan_report_payload(report_path)

    assert loaded["obligation_attempt_ledger"] == ledger
    assert loaded["obligation_attempt_ledger_ref"] == ref

from __future__ import annotations

import json
import sqlite3

from ai_test_asset_center.agent_discovery_loop import _paths
from ai_test_asset_center.business_finding_registry import register_in_ledger


def _ledger_row_count(project: str, root) -> int:
    ledger_path = _paths(project, root)["ledger"]
    if not ledger_path.exists():
        return 0
    conn = sqlite3.connect(ledger_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM loop_items").fetchone()[0])
    finally:
        conn.close()


def test_internal_defect_intake_candidates_do_not_auto_register_into_ledger(tmp_path) -> None:
    project = "enterprise-project"
    intake_path = tmp_path / "platform_workspace" / project / "defect_discovery" / "internal_defect_intake_candidates.json"
    intake_path.parent.mkdir(parents=True, exist_ok=True)
    intake_path.write_text(
        json.dumps(
            {
                "version": "ui_high_confidence_defect_intake_candidates_v1",
                "project_id": project,
                "items": [
                    {
                        "intake_id": "UIINTAKE_1",
                        "title": "高可信 UI 候选",
                        "severity": "P1",
                        "risk_type": "ui_execution",
                        "method": "POST",
                        "path": "/ui/orders/1/cancel",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    registered = register_in_ledger({"validated_candidates": []}, project_id=project, root=tmp_path)

    assert registered == 0
    assert _ledger_row_count(project, tmp_path) == 0


def test_register_in_ledger_requires_explicit_validated_candidates(tmp_path) -> None:
    project = "enterprise-project"
    finding = {
        "finding_id": "FND_UI_VALIDATED_1",
        "title": "UI 已验证候选",
        "hypothesis_id": "HYP_UI_1",
        "violated_invariant": {"kind": "P1"},
        "root_cause_candidate": "ui_execution",
        "verdict": "VALIDATED_CANDIDATE",
        "adversarial_validation": {"status": "passed"},
    }

    registered = register_in_ledger({"validated_candidates": [finding]}, project_id=project, root=tmp_path)

    assert registered == 1
    ledger_path = _paths(project, tmp_path)["ledger"]
    conn = sqlite3.connect(ledger_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT item_id, item_type, state, human_review_state FROM loop_items WHERE item_id = ?",
            ("FND_UI_VALIDATED_1",),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["item_type"] == "VALIDATED_CANDIDATE"
    assert row["state"] == "VALIDATED_CANDIDATE"
    assert row["human_review_state"] == "PENDING_HUMAN_REVIEW"

from __future__ import annotations

import pytest

from ai_test_asset_center import db_persistence as db
from ai_test_asset_center.finding_collaboration import (
    annotate_command_center_collaboration,
    list_finding_collaboration,
    update_finding_collaboration,
)
from ai_test_asset_center.private_pilot_finding_collaboration_handlers import (
    FindingCollaborationHandlersMixin,
)
from ai_test_asset_center.private_pilot_http_routing import HttpRoutingMixin
from ai_test_asset_center.private_pilot_service import PrivatePilotHandler


def _seed_finding(tmp_path, *, title: str = "库存不得为负", path: str = "/api/inventory") -> tuple[str, str, str]:
    tenant = "tenant_collab"
    project = "project_collab"
    created = db.create_tenant(
        tmp_path,
        tenant,
        "Collaboration Tenant",
        username="collab_admin",
        password="StrongPassword-123",
    )
    assert created["ok"] is True
    assert db.create_project(tmp_path, tenant, project, "Collaboration Project")["ok"] is True
    scan_id = db.save_scan(
        tmp_path,
        tenant,
        project,
        {"grade": "B", "coverage": 0.8, "total_findings": 1},
    )
    merged = db.merge_findings_cumulative(
        tmp_path,
        tenant,
        project,
        scan_id,
        [
            {
                "title": title,
                "severity": "P1",
                "confidence_score": 0.95,
                "_api_method": "GET",
                "_api_path": path,
                "evidence": {
                    "method": "GET",
                    "path": path,
                    "hash": "evidence-collab-001",
                },
            }
        ],
    )
    assert merged["new"] == 1
    rows = db.get_cumulative_findings(
        tmp_path,
        tenant,
        project,
        include_resolved=True,
    )
    assert len(rows) == 1
    return tenant, project, rows[0]["risk_id"]


def test_human_handling_does_not_mutate_verification_truth(tmp_path) -> None:
    tenant, project, finding_id = _seed_finding(tmp_path)

    saved = update_finding_collaboration(
        tmp_path,
        tenant,
        project,
        finding_id,
        {
            "handling_status": "in_progress",
            "assignee": "研发 A",
            "fix_version": "v1.2.3",
            "developer_feedback": "已定位库存扣减路径，准备修复。",
            "disposition": "none",
        },
        actor_name="qa_lead",
    )
    assert saved["verification_status"] == "open"
    assert saved["handling_status"] == "in_progress"
    assert saved["assignee"] == "研发 A"
    assert saved["fix_version"] == "v1.2.3"

    with pytest.raises(ValueError, match="execution-owned"):
        update_finding_collaboration(
            tmp_path,
            tenant,
            project,
            finding_id,
            {"verification_status": "resolved"},
            actor_name="qa_lead",
        )

    assert db.update_finding_status(
        tmp_path,
        finding_id,
        "resolved",
        tenant_id=tenant,
        project_id=project,
    ) is True
    rows = list_finding_collaboration(tmp_path, tenant, project)
    assert rows[0]["verification_status"] == "resolved"
    assert rows[0]["handling_status"] == "in_progress"


def test_display_finding_receives_stable_persistence_crosswalk(tmp_path) -> None:
    tenant, project, finding_id = _seed_finding(tmp_path)
    update_finding_collaboration(
        tmp_path,
        tenant,
        project,
        finding_id,
        {
            "handling_status": "triaged",
            "assignee": "研发 B",
            "disposition": "accepted_risk",
            "disposition_note": "仅本版本临时接受，下一版本修复。",
        },
        actor_name="project_owner",
    )

    payload = {
        "data": {
            "defects": [
                {
                    "id": "GET:/api/inventory:display123",
                    "title": "库存不得为负",
                    "repro_method": "GET",
                    "repro_path": "/api/inventory",
                    "proof": {"hash": "evidence-collab-001"},
                }
            ]
        }
    }
    annotated = annotate_command_center_collaboration(
        payload,
        tmp_path,
        tenant,
        project,
    )
    finding = annotated["data"]["defects"][0]
    assert finding["finding_persistence_id"] == finding_id
    assert finding["verification_status"] == "open"
    assert finding["collaboration"]["handling_status"] == "triaged"
    assert finding["collaboration"]["assignee"] == "研发 B"
    assert finding["collaboration"]["disposition"] == "accepted_risk"
    assert annotated["data"]["risks"][0]["finding_persistence_id"] == finding_id


def test_collaboration_mixin_is_before_http_and_replay_handlers() -> None:
    mro = PrivatePilotHandler.__mro__
    assert mro.index(FindingCollaborationHandlersMixin) < mro.index(HttpRoutingMixin)
    assert PrivatePilotHandler._handle_replay is FindingCollaborationHandlersMixin._handle_replay

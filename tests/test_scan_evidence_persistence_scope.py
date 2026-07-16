from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.__main__ import _persist_execution_evidence
from ai_test_asset_center.canonical_defect_registry import (
    CANONICAL_DEFECT_REGISTRY_SCHEMA,
)


def test_replay_evidence_bundle_uses_canonical_representatives_not_customer_findings(
    tmp_path: Path,
) -> None:
    occurrence = {
        "finding_id": "finding-1",
        "title": "Private evaluator finding",
        "raw_evidence": {
            "request_raw": {"method": "GET", "path": "/resources/1"},
            "has_real_evidence": True,
        },
        "execution_status": "executed",
    }
    representative = {
        **occurrence,
        "canonical_defect_id": "canonical-1",
        "delivery_occurrence_finding_id": "finding-1",
    }
    registry = {
        "schema_version": CANONICAL_DEFECT_REGISTRY_SCHEMA,
        "status": "VERIFIED",
        "authority_scope": "private_evaluator",
        "canonical_defect_count": 1,
        "delivery_occurrence_count": 1,
        "canonical_defect_ids": ["canonical-1"],
        "delivery_occurrence_finding_ids": ["finding-1"],
    }

    bundle = _persist_execution_evidence(
        "enterprise-project",
        tmp_path,
        "scan-1",
        {"campaign_id": "campaign-1"},
        {"source_manifest": {"source_id": "api", "source_hash": "a" * 64}},
        "executed",
        {
            "findings": [],
            "formal_count_projection": {
                "canonical_representative_findings": [representative],
            },
            "candidate_findings": [],
            "external_findings": [],
            "canonical_defect_registry": registry,
            "delivery_occurrences": [occurrence],
        },
    )

    findings_artifact = (
        tmp_path / "platform_workspace" / "enterprise-project"
        / "evidence_bundles" / bundle["bundle_id"] / "findings.json"
    )
    persisted_findings = json.loads(findings_artifact.read_text(encoding="utf-8"))

    assert bundle["status"] == "persisted"
    assert [row["canonical_defect_id"] for row in persisted_findings] == [
        "canonical-1"
    ]

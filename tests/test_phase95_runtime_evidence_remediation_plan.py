from __future__ import annotations

from ai_test_asset_center.grounded_probe_executor import (
    _build_runtime_evidence_remediation_plan,
    _render_runtime_evidence_remediation_plan_markdown,
)


def test_runtime_remediation_plan_prioritizes_exact_blocking_candidates() -> None:
    report = {
        "created_at": "2026-06-27T00:00:00Z",
        "project_id": "remediation-plan-demo",
        "runtime_evidence_scoreboard": {
            "evidence_maturity": {"level": "runtime_evidence_blocked", "customer_ready": False},
            "recommended_next_actions": [
                {"priority": "P0", "gap_type": "runtime_binding_instability"},
            ],
        },
        "runtime_evidence_probe_ledger": {
            "entries": [
                {
                    "candidate_id": "WRITE-BINDING-GAP",
                    "readiness_level": "evidence_gap",
                    "verdict": "validated_candidate",
                    "customer_ready": False,
                    "gap_types": ["runtime_binding_not_fully_bound", "snapshot_not_fully_accepted"],
                },
                {
                    "candidate_id": "WRITE-FIXTURE-GAP",
                    "readiness_level": "evidence_gap",
                    "verdict": "needs_more_evidence",
                    "customer_ready": False,
                    "gap_types": ["fixture_setup_not_fully_accepted"],
                },
                {
                    "candidate_id": "WRITE-READY",
                    "readiness_level": "customer_ready_candidate",
                    "verdict": "validated_candidate",
                    "customer_ready": True,
                    "gap_types": [],
                },
            ]
        },
        "runtime_customer_reproduction_pack": {
            "packages": [
                {
                    "finding_id": "GPF-GAP",
                    "candidate_id": "WRITE-BINDING-GAP",
                    "customer_ready": False,
                    "readiness_level": "validated_but_reproduction_gap",
                    "reproduction_readiness_gate": {
                        "blockers": ["runtime_binding_not_fully_bound", "probe_ledger_has_evidence_gaps"]
                    },
                },
                {
                    "finding_id": "GPF-READY",
                    "candidate_id": "WRITE-READY",
                    "customer_ready": True,
                    "readiness_level": "customer_ready_candidate",
                    "reproduction_readiness_gate": {"blockers": []},
                },
            ]
        },
    }

    plan = _build_runtime_evidence_remediation_plan(report)

    assert plan["status"] == "runtime_remediation_required"
    assert plan["p0_group_count"] == 4
    assert plan["queued_candidate_count"] == 2
    assert set(plan["rerun_manifest"]["candidate_ids"]) == {"WRITE-BINDING-GAP", "WRITE-FIXTURE-GAP"}
    assert plan["rerun_manifest"]["customer_ready_candidate_ids_excluded"] == ["WRITE-READY"]

    groups = {group["gap_type"]: group for group in plan["priority_groups"]}
    assert groups["runtime_binding_not_fully_bound"]["priority"] == "P0"
    assert groups["runtime_binding_not_fully_bound"]["candidate_ids"] == ["WRITE-BINDING-GAP"]
    assert groups["runtime_binding_not_fully_bound"]["finding_ids"] == ["GPF-GAP"]
    assert groups["fixture_setup_not_fully_accepted"]["candidate_ids"] == ["WRITE-FIXTURE-GAP"]
    assert groups["probe_ledger_has_evidence_gaps"]["source_counts"]["reproduction_readiness_gate"] == 1

    markdown = _render_runtime_evidence_remediation_plan_markdown(plan)
    assert "Runtime Evidence Remediation Plan" in markdown
    assert "WRITE-BINDING-GAP" in markdown
    assert "runtime_binding_not_fully_bound" in markdown
    assert "fix P0 groups first" in markdown


def test_runtime_remediation_plan_reports_customer_ready_when_no_runtime_gaps() -> None:
    report = {
        "created_at": "2026-06-27T00:00:00Z",
        "project_id": "remediation-ready-demo",
        "runtime_evidence_scoreboard": {
            "evidence_maturity": {"level": "customer_ready_runtime_evidence", "customer_ready": True}
        },
        "runtime_evidence_probe_ledger": {
            "entries": [
                {
                    "candidate_id": "READY-1",
                    "readiness_level": "customer_ready_candidate",
                    "verdict": "validated_candidate",
                    "customer_ready": True,
                    "gap_types": [],
                },
                {
                    "candidate_id": "PROTECTED-1",
                    "readiness_level": "protected_or_falsified",
                    "verdict": "falsified_or_protected",
                    "customer_ready": False,
                    "gap_types": [],
                },
            ]
        },
        "runtime_customer_reproduction_pack": {
            "packages": [
                {
                    "finding_id": "GPF-READY",
                    "candidate_id": "READY-1",
                    "customer_ready": True,
                    "readiness_level": "customer_ready_candidate",
                    "reproduction_readiness_gate": {"blockers": []},
                }
            ]
        },
    }

    plan = _build_runtime_evidence_remediation_plan(report)

    assert plan["status"] == "customer_ready_no_runtime_remediation_needed"
    assert plan["remediation_group_count"] == 0
    assert plan["queued_candidate_count"] == 0
    assert plan["rerun_manifest"]["candidate_ids"] == []
    assert plan["rerun_manifest"]["customer_ready_candidate_ids_excluded"] == ["READY-1"]

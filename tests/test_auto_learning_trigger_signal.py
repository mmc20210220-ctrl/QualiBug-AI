"""Tests for the auto-learning trigger on the real v12 scan schema.

The pre-fix trigger read legacy fields (``high_value_summary`` /
``benchmark_metrics.coverage_gain`` / ``probe_execution_result``) that no
mainline scan writes, so it never fired. These tests drive it with the
authoritative v12 shapes and verify the decision, the trace-ledger history
reader, and the KB write-back of extracted signals.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center import learning_knowledge_db as _db_mod
from ai_test_asset_center import learning_pattern_bridge as _bridge_mod
from ai_test_asset_center.auto_learning_trigger import (
    AutoLearningTrigger,
    LearningTriggerConfig,
)
from ai_test_asset_center.learning_knowledge_db import LearningKnowledgeDB

PROJECT = "trigger_signal_test"


@pytest.fixture()
def isolated_kb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(_db_mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(_bridge_mod, "_REPO_ROOT", tmp_path)
    return tmp_path


def _v12_scan_result(*, confirmed: int = 0, blocked: int = 0) -> dict:
    return {
        "formal_count_projection": {
            "formal_customer_deliverable_count": confirmed,
            "canonical_defect_count": confirmed,
        },
        "pipeline_health": {"blocked_obligation_count": blocked},
    }


def test_trigger_fires_on_authoritative_confirmed_count(isolated_kb: Path) -> None:
    trigger = AutoLearningTrigger(project=PROJECT, config=LearningTriggerConfig())
    ok, reason = trigger.should_trigger(_v12_scan_result(confirmed=5))
    assert ok
    assert reason.startswith("SIGNALS_MET")
    assert "confirmed_bugs=5" in reason


def test_trigger_fires_on_blocked_obligations(isolated_kb: Path) -> None:
    trigger = AutoLearningTrigger(project=PROJECT, config=LearningTriggerConfig())
    ok, reason = trigger.should_trigger(_v12_scan_result(confirmed=0, blocked=12))
    assert ok
    assert "blocked_obligations=12" in reason


def test_trigger_stays_quiet_on_clean_scan(isolated_kb: Path) -> None:
    trigger = AutoLearningTrigger(project=PROJECT, config=LearningTriggerConfig())
    ok, reason = trigger.should_trigger(_v12_scan_result(confirmed=0, blocked=0))
    assert not ok
    assert reason.startswith("NO_SIGNALS")


def test_trigger_reads_canonical_defect_count_fallback(isolated_kb: Path) -> None:
    """Without formal_customer_deliverable_count, canonical_defect_count works."""
    result = {
        "formal_count_projection": {"canonical_defect_count": 4},
        "pipeline_health": {},
    }
    trigger = AutoLearningTrigger(project=PROJECT, config=LearningTriggerConfig())
    ok, _ = trigger.should_trigger(result)
    assert ok


def test_extract_confirmed_findings_from_v12_registry(isolated_kb: Path) -> None:
    trigger = AutoLearningTrigger(project=PROJECT, config=LearningTriggerConfig())
    scan_result = {
        "canonical_defect_registry": {
            "canonical_defects": [
                {
                    "canonical_defect_id": "cdef_a",
                    "identity": {
                        "operation": {"source_locator": "POST /api/orders"},
                        "property": "state_integrity",
                    },
                    "occurrence_count": 2,
                },
                {
                    "canonical_defect_id": "cdef_b",
                    "identity": {
                        "operation": {"source_locator": "GET /api/users"},
                        "property": "authorization",
                    },
                    "occurrence_count": 1,
                },
            ]
        },
        "formal_count_projection": {
            "canonical_representative_findings": [
                {
                    "title": "[Oracle] duplicate submit",
                    "category": "state_integrity",
                    "severity": "P1",
                    "obligation_id": "obl_1",
                    "experiment_id": "exp_1",
                }
            ]
        },
    }
    findings = trigger._extract_confirmed_findings(scan_result)
    assert len(findings) == 3  # registry 2 + projection 1
    titles = {f["title"] for f in findings}
    assert "POST /api/orders" in titles
    assert "[Oracle] duplicate submit" in titles


def test_execute_persists_signals_to_kb(isolated_kb: Path) -> None:
    trigger = AutoLearningTrigger(
        project=PROJECT,
        root=isolated_kb,
        config=LearningTriggerConfig(dry_run=False),
    )
    scan_result = {
        "canonical_defect_registry": {
            "canonical_defects": [
                {
                    "canonical_defect_id": "cdef_a",
                    "identity": {
                        "operation": {"source_locator": "POST /api/orders"},
                        "property": "state_integrity",
                    },
                    "occurrence_count": 2,
                },
                {
                    "canonical_defect_id": "cdef_b",
                    "identity": {
                        "operation": {"source_locator": "GET /api/users"},
                        "property": "authorization",
                    },
                    "occurrence_count": 1,
                },
            ]
        }
    }
    result = trigger.execute(scan_result=scan_result)
    assert result.success
    assert result.patterns_extracted >= 1
    # Signals must land in the SQLite KB as risk_pattern entries so the next
    # scan's planning boost / reasoner memory block consume them.
    db = LearningKnowledgeDB(project=PROJECT)
    entries = db.get_effective_patterns("risk_pattern", min_usage=0)
    assert entries, "no signals were persisted to the KB"
    assert any("learned:" in e.key for e in entries)
    # No dead learned_probes.json file anymore.
    assert not (isolated_kb / "platform_outputs" / PROJECT / "workspace" / "learned_probes.json").exists()


def test_load_recent_scans_reads_trace_ledgers(isolated_kb: Path) -> None:
    trigger = AutoLearningTrigger(project=PROJECT, root=isolated_kb)
    ledger_dir = (
        isolated_kb / "platform_outputs" / PROJECT
        / "discovery_evolution" / "trace_ledgers" / "target-1"
    )
    ledger_dir.mkdir(parents=True)
    for run_id in ("RUN_1", "RUN_2"):
        (ledger_dir / f"{run_id}.trace-ledger.json").write_text(
            json.dumps({
                "schema_version": "qualibug.discovery-trace-ledger.v3",
                "run_id": run_id,
                "created_at_utc": f"2026-08-0{int(run_id[-1])}T00:00:00Z",
                "campaign_id": "CMP_x",
                "attempts": [],
            }),
            encoding="utf-8",
        )
    scans = trigger._load_recent_scans(limit=2)
    assert len(scans) == 2
    assert {s["run_id"] for s in scans} == {"RUN_1", "RUN_2"}

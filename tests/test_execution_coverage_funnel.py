"""Tests for execution_coverage_funnel.py — SPEC v1.2 §5."""
import pytest
from ai_test_asset_center.execution_coverage_funnel import (
    FUNNEL_STAGES,
    build_execution_coverage_funnel,
)


def _make_obligation(oid="obl_1", family="authorization"):
    return {"obligation_id": oid, "risk_family": family, "source_refs": []}


def _make_experiment(oid="obl_1", status="COMPILED", reason=""):
    return {
        "obligation_id": oid,
        "experiment_id": f"exp_{oid}",
        "compile_receipt": {"status": status, "reason_code": reason},
        "safety_contract": {"governed_write": False},
        "observers": [{"observer_id": "entity_state"}],
        "binding_plan": [],
    }


def _make_result(oid="obl_1", status="EXECUTED"):
    return {
        "obligation_id": oid,
        "status": status,
        "observations": {},
        "oracle_receipt": {"verdict": "ASSERTION_VIOLATION"},
    }


class TestFunnelStages:
    def test_all_stages_defined(self):
        assert len(FUNNEL_STAGES) == 17
        assert FUNNEL_STAGES[0] == "OBLIGATION_CREATED"
        assert FUNNEL_STAGES[-1] == "DEEP_UNIQUE_ROOT_CAUSE"

    def test_empty_input(self):
        result = build_execution_coverage_funnel(
            obligations=[], experiments=[], execution_results=[], findings=[]
        )
        assert result["schema_version"] == "qualibug.execution-coverage-funnel.v1"
        assert result["obligations_total"] == 0

    def test_single_obligation_compiled(self):
        result = build_execution_coverage_funnel(
            obligations=[_make_obligation()],
            experiments=[_make_experiment()],
            execution_results=[],
            findings=[],
        )
        assert result["obligations_total"] == 1
        assert result["stages"]["EXPERIMENT_COMPILED"]["count"] == 1

    def test_single_obligation_blocked(self):
        result = build_execution_coverage_funnel(
            obligations=[_make_obligation()],
            experiments=[_make_experiment(status="BLOCKED", reason="BLOCKED_MISSING_OBSERVER")],
            execution_results=[],
            findings=[],
        )
        assert result["obligations_total"] == 1
        assert result["stages"]["EXPERIMENT_COMPILED"]["count"] == 0
        assert "BLOCKED_MISSING_OBSERVER" in result["terminal_reasons"]

    def test_executed_with_finding(self):
        finding = {
            "obligation_id": "obl_1",
            "title": "Test Bug",
            "customer_deliverable": True,
            "root_cause_id": "rc_1",
        }
        result = build_execution_coverage_funnel(
            obligations=[_make_obligation()],
            experiments=[_make_experiment()],
            execution_results=[_make_result()],
            findings=[finding],
        )
        assert result["stages"]["FORMAL_DELIVERABLE"]["count"] == 1
        assert result["stages"]["UNIQUE_ROOT_CAUSE"]["count"] == 1

    def test_duplicate_obligation_not_counted(self):
        result = build_execution_coverage_funnel(
            obligations=[_make_obligation(), _make_obligation()],
            experiments=[_make_experiment()],
            execution_results=[],
            findings=[],
        )
        assert result["obligations_total"] == 1

    def test_risk_family_breakdown(self):
        result = build_execution_coverage_funnel(
            obligations=[
                _make_obligation("obl_1", "authorization"),
                _make_obligation("obl_2", "conservation"),
            ],
            experiments=[_make_experiment("obl_1"), _make_experiment("obl_2")],
            execution_results=[],
            findings=[],
        )
        assert "authorization" in result["risk_family_breakdown"]
        assert "conservation" in result["risk_family_breakdown"]

    def test_fingerprint_deterministic(self):
        kwargs = dict(
            obligations=[_make_obligation()],
            experiments=[_make_experiment()],
            execution_results=[],
            findings=[],
        )
        r1 = build_execution_coverage_funnel(**kwargs)
        r2 = build_execution_coverage_funnel(**kwargs)
        assert r1["fingerprint"] == r2["fingerprint"]

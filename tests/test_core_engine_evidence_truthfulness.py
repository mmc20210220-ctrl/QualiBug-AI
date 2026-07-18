from __future__ import annotations

from core.engine import MockEngine as Engine


def test_memory_engine_marks_all_generated_results_as_simulated(monkeypatch):
    monkeypatch.setenv("QUALIBUG_API_TOKEN", "test-token")
    engine = Engine()

    result = engine.run("generic-smoke", "test-token")

    assert result["execution_source"] == "memory_simulation"
    assert result["metrics"]["confirmed_bugs"] == 0
    assert result["metrics"]["bugs"] == 0
    assert result["summary"]["simulated"] == result["trace_count"]
    assert all(trace["confirmation_status"] == "simulated" for trace in result["traces"])
    assert all(trace["evidence_level"] == "synthetic" for trace in result["traces"])


def test_confirmed_verdict_requires_complete_runtime_receipt():
    engine = Engine()
    incomplete = {
        "execution_status": "executed",
        "evidence": {"request": {"method": "GET"}, "response": {"status": 500}},
    }
    complete = {
        "execution_status": "executed",
        "evidence": {
            "request": {"method": "GET", "path": "/resource"},
            "response": {"status": 500, "body": "observed"},
            "assertion": {"expected": "non-error", "actual": "500"},
            "timestamp": "2026-07-06T00:00:00Z",
            "target": "configured-target",
            "actor": "configured-test-actor",
            "reproduction_steps": ["send documented request"],
        },
    }

    assert engine.judge(incomplete) == "INCONCLUSIVE"
    assert engine.judge(complete) == "CONFIRMED"


def test_simulation_can_never_be_promoted_to_confirmed():
    engine = Engine()
    simulated = {
        "simulation": True,
        "execution_status": "executed",
        "evidence": {
            "request": {"method": "GET"},
            "response": {"status": 500},
            "assertion": {"expected": "x", "actual": "y"},
            "timestamp": "2026-07-06T00:00:00Z",
            "target": "configured-target",
            "actor": "configured-test-actor",
            "reproduction_steps": ["step"],
        },
    }

    assert engine.judge(simulated) == "SIMULATED"

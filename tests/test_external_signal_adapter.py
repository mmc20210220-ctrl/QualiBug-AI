from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.external_signal_adapter import execute_external_signal_requests


def test_external_signal_adapter_imports_json_report(tmp_path):
    report_path = tmp_path / "signals.json"
    report_path.write_text(json.dumps({
        "findings": [
            {
                "title": "schemathesis found 500",
                "severity": "P1",
                "description": "unexpected HTTP 500 from generated payload",
                "confidence_score": 0.82,
                "trace_id": "trace-123",
                "db_evidence": {"table": "orders"},
                "business_invariant_evaluation": {"verdict": "failed"},
                "before_after_snapshot": {"before": {"path": "/api/orders"}, "after": {"path": "/api/orders"}},
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")

    result = execute_external_signal_requests(
        "demo",
        [{
            "provider": "schemathesis",
            "report_path": str(report_path),
            "report_format": "json",
            "title": "openapi fuzz import",
        }],
        runtime_contract={},
        root=tmp_path,
    )

    assert result["status"] == "completed"
    assert result["requested"] == 1
    assert result["imported"] == 1
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["external_signal_provider"] == "schemathesis"
    assert finding["evidence"]["trace_id"] == "trace-123"
    assert finding["db_evidence"]["table"] == "orders"
    assert finding["business_invariant_evaluation"]["verdict"] == "failed"


def test_external_signal_adapter_blocks_missing_report(tmp_path):
    result = execute_external_signal_requests(
        "demo",
        [{
            "provider": "restler",
            "report_path": str(tmp_path / "missing.json"),
            "report_format": "json",
        }],
        runtime_contract={},
        root=tmp_path,
    )

    assert result["status"] == "blocked"
    assert result["blocked"] == 1
    assert result["findings"] == []


def test_external_signal_adapter_blocks_schemathesis_when_not_installed(tmp_path):
    result = execute_external_signal_requests(
        "demo",
        [{
            "provider": "schemathesis",
            "base_url": "http://127.0.0.1:8080",
            "schema_path": str(tmp_path / "openapi.json"),
        }],
        runtime_contract={"approved_base_url": "http://127.0.0.1:8080"},
        root=tmp_path,
    )

    assert result["status"] == "blocked"
    assert result["blocked"] == 1
    assert result["results"][0]["reason"] == "SCHEMATHESIS_NOT_INSTALLED"


def test_external_signal_adapter_runs_schemathesis_and_imports_junit(monkeypatch, tmp_path):
    schema_path = tmp_path / "openapi.json"
    schema_path.write_text('{"openapi":"3.0.0","paths":{"/api/orders":{"get":{"responses":{"200":{"description":"ok"}}}}}}', encoding="utf-8")

    monkeypatch.setattr("ai_test_asset_center.external_signal_adapter._bool_env_installed", lambda name: True)

    class Completed:
        def __init__(self):
            self.returncode = 1
            self.stdout = "schemathesis stdout"
            self.stderr = "schemathesis stderr"

    def fake_run(command, cwd, capture_output, text, encoding, errors):
        junit_path = Path(command[command.index("--report-junit-path") + 1])
        junit_path.parent.mkdir(parents=True, exist_ok=True)
        junit_path.write_text(
            """<?xml version="1.0" encoding="utf-8"?>
<testsuite tests="1" failures="1">
  <testcase classname="GET /api/orders" name="status code check">
    <failure message="Server error">HTTP 500</failure>
  </testcase>
</testsuite>
""",
            encoding="utf-8",
        )
        return Completed()

    monkeypatch.setattr("ai_test_asset_center.external_signal_adapter.subprocess.run", fake_run)

    result = execute_external_signal_requests(
        "demo",
        [{
            "provider": "schemathesis",
            "base_url": "http://127.0.0.1:8080",
            "schema_path": str(schema_path),
            "max_failures": 5,
            "wait_for_schema": 3,
        }],
        runtime_contract={"approved_base_url": "http://127.0.0.1:8080"},
        root=tmp_path,
        run_id="run_1",
    )

    assert result["status"] == "completed"
    assert result["imported"] == 1
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["external_signal_provider"] == "schemathesis"
    assert "Server error" in finding["description"]
    artifacts = result["results"][0]["artifacts"]
    assert any(item["artifact_type"] == "schemathesis_junit" for item in artifacts)


def test_external_signal_adapter_runtime_replay_attaches_trace_and_snapshot(monkeypatch, tmp_path):
    report_path = tmp_path / "signals.json"
    report_path.write_text(json.dumps({
        "findings": [
            {
                "title": "GET /api/orders returned 500",
                "severity": "P1",
                "method": "GET",
                "path": "/api/orders",
                "description": "unexpected server error",
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")

    class Response:
        status = 500

        def __init__(self):
            self.headers = {"content-type": "application/json"}

        def read(self, *_args, **_kwargs):
            return b'{"error":"boom"}'

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("ai_test_asset_center.external_signal_adapter.urllib.request.urlopen", lambda req, timeout=10: Response())

    result = execute_external_signal_requests(
        "demo",
        [{
            "provider": "schemathesis",
            "report_path": str(report_path),
            "report_format": "json",
            "base_url": "http://127.0.0.1:8080",
            "execution_mode": "runtime_replay",
        }],
        runtime_contract={"approved_base_url": "http://127.0.0.1:8080"},
        root=tmp_path,
    )

    assert result["runtime_replay_summary"]["replayed"] == 1
    finding = result["findings"][0]
    assert finding["runtime_replay"]["status"] == "executed"
    assert finding["runtime_replay"]["http_status"] == 500
    assert finding["before_after_snapshot"]["before"]["path"] == "/api/orders"
    assert finding["evidence"]["runtime_replay"]["http_status"] == 500


def test_external_signal_adapter_imports_data_diff_report_as_db_evidence(tmp_path):
    report_path = tmp_path / "db_diff.json"
    report_path.write_text(json.dumps({
        "business_operation": "POST /api/orders",
        "before_snapshots": [{"row_count": 1, "table": "orders"}],
        "after_snapshots": [{"row_count": 2, "table": "orders"}],
        "diffs": [{"table": "orders", "detail": "orders row_count 1->2", "added_rows": 1}],
    }, ensure_ascii=False), encoding="utf-8")

    result = execute_external_signal_requests(
        "demo",
        [{
            "provider": "data_diff",
            "report_path": str(report_path),
        }],
        runtime_contract={},
        root=tmp_path,
    )

    assert result["status"] == "completed"
    assert result["imported"] == 1
    finding = result["findings"][0]
    assert finding["db_evidence"]["table"] == "orders"
    assert finding["db_evidence"]["business_operation"] == "POST /api/orders"
    assert "1->2" in finding["db_evidence"]["db_assertion"]


def test_external_signal_adapter_attaches_db_diff_report_to_schemathesis_findings(tmp_path):
    signals_path = tmp_path / "signals.json"
    signals_path.write_text(json.dumps({
        "findings": [
            {
                "title": "POST /api/orders accepted invalid payload",
                "severity": "P1",
                "method": "POST",
                "path": "/api/orders",
                "description": "write accepted unexpectedly",
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    db_diff_path = tmp_path / "db_diff.json"
    db_diff_path.write_text(json.dumps({
        "before_snapshots": [{"row_count": 1, "table": "orders"}],
        "after_snapshots": [{"row_count": 2, "table": "orders"}],
        "diffs": [{"table": "orders", "detail": "orders row_count 1->2", "added_rows": 1}],
    }, ensure_ascii=False), encoding="utf-8")

    result = execute_external_signal_requests(
        "demo",
        [{
            "provider": "schemathesis",
            "report_path": str(signals_path),
            "report_format": "json",
            "db_diff_report_path": str(db_diff_path),
        }],
        runtime_contract={},
        root=tmp_path,
    )

    finding = result["findings"][0]
    assert finding["db_evidence"]["table"] == "orders"
    assert finding["db_evidence"]["business_operation"] == "POST /api/orders"
    assert finding["evidence"]["db_diff_report"].endswith("db_diff.json")


def test_external_signal_adapter_imports_invariant_report(tmp_path):
    report_path = tmp_path / "invariant.json"
    report_path.write_text(json.dumps({
        "results": [
            {
                "name": "订单状态守恒",
                "status": "failed",
                "message": "订单状态从 PAID 变成了 CANCELLED",
                "failed_fields": ["status"],
                "expected": "PAID",
                "actual": "CANCELLED",
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")

    result = execute_external_signal_requests(
        "demo",
        [{
            "provider": "soda_core",
            "report_path": str(report_path),
            "title": "order invariant check",
        }],
        runtime_contract={},
        root=tmp_path,
    )

    assert result["status"] == "completed"
    finding = result["findings"][0]
    assert finding["business_invariant_evaluation"]["verdict"] == "failed"
    assert finding["failed_fields"] == ["status"]
    assert finding["business_invariant_evaluation"]["results"][0]["actual"] == "CANCELLED"


def test_external_signal_adapter_attaches_invariant_report_to_schemathesis_findings(tmp_path):
    signals_path = tmp_path / "signals.json"
    signals_path.write_text(json.dumps({
        "findings": [
            {
                "title": "POST /api/orders accepted invalid transition",
                "severity": "P1",
                "method": "POST",
                "path": "/api/orders",
                "description": "write accepted unexpectedly",
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    invariant_path = tmp_path / "invariant.json"
    invariant_path.write_text(json.dumps({
        "results": [
            {
                "name": "订单状态守恒",
                "status": "failed",
                "message": "订单状态从 PAID 变成了 CANCELLED",
                "failed_fields": ["status"],
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")

    result = execute_external_signal_requests(
        "demo",
        [{
            "provider": "schemathesis",
            "report_path": str(signals_path),
            "report_format": "json",
            "invariant_report_path": str(invariant_path),
        }],
        runtime_contract={},
        root=tmp_path,
    )

    finding = result["findings"][0]
    assert finding["business_invariant_evaluation"]["verdict"] == "failed"
    assert finding["failed_fields"] == ["status"]
    assert finding["evidence"]["invariant_report"].endswith("invariant.json")

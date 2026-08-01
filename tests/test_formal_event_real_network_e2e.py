from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from ai_test_asset_center import experiment_cleanup_executor as cleanup_executor
from ai_test_asset_center import experiment_executor as executor
from ai_test_asset_center import experiment_plan_executor as plan_executor
from ai_test_asset_center import experiment_runtime_support as runtime_support
from ai_test_asset_center import formal_event_surface as events
from ai_test_asset_center import sandbox_write_executor as sandbox
from ai_test_asset_center import sandbox_write_executor_base as sandbox_base


def _helper_module():
    path = Path(__file__).with_name("test_formal_event_post_runtime_execution.py")
    spec = importlib.util.spec_from_file_location(
        "qualibug_formal_event_test_helper",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SutState:
    def __init__(self) -> None:
        self.created = False
        self.calls: list[str] = []
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    state: _SutState

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, status: int, body: Any) -> None:
        payload = json.dumps(body).encode("utf-8") if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        with self.state.lock:
            self.state.calls.append(f"GET {path}")
            created = self.state.created
        if path == "/api/orders":
            rows = (
                [{"id": "order-1", "sku": "SKU-1", "quantity": 1}]
                if created
                else []
            )
            self._json(200, {"items": rows})
            return
        if path == "/api/orders/order-1":
            self._json(
                200 if created else 404,
                {"id": "order-1", "sku": "SKU-1", "quantity": 1}
                if created
                else {},
            )
            return
        if path == "/test-observers/events":
            # Intentional business defect: the write succeeds but no event is
            # emitted during the complete observation window.
            self._json(200, {"items": []})
            return
        self._json(404, {})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        with self.state.lock:
            self.state.calls.append(f"POST {path}")
        if path != "/api/orders":
            self._json(404, {})
            return
        assert body == {"sku": "SKU-1", "quantity": 1}
        with self.state.lock:
            self.state.created = True
        self._json(201, {"id": "order-1", **body})

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        with self.state.lock:
            self.state.calls.append(f"DELETE {path}")
        if path != "/api/orders/order-1":
            self._json(404, {})
            return
        with self.state.lock:
            self.state.created = False
        self._json(204, None)


@pytest.fixture
def real_sut() -> tuple[str, _SutState]:
    state = _SutState()
    handler = type("BoundHandler", (_Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_network_event_violation_restores_environment_and_true_completes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    real_sut: tuple[str, _SutState],
) -> None:
    helper = _helper_module()
    model, experiment = helper._compile_event_experiment()
    base_url, state = real_sut

    def allow_sandbox(**_: Any) -> tuple[bool, str]:
        return True, ""

    for module in (
        sandbox,
        sandbox_base,
        plan_executor,
        cleanup_executor,
        runtime_support,
        executor,
    ):
        monkeypatch.setattr(
            module,
            "sandbox_write_allowed",
            allow_sandbox,
            raising=False,
        )

    result = executor.execute_one_experiment(
        experiment,
        behavior_ir=model,
        root=tmp_path,
        project="formal-event-real-network",
        base_url=base_url,
        runtime_contract={
            "status": "approved",
            "environment_type": "test",
            "environment_kind": "test",
            "environment_ref": "event-real-network",
            "execution_mode": "approved_sandbox_write",
            "approved_base_url": base_url,
            "declared_adapters": ["http_api", events.ADAPTER],
            "is_production": False,
        },
        campaign_id="campaign-event-real-network",
        execution_id="execution-event-real-network",
        actor_tokens={
            "secret_ref:test_accounts:admin@example.test": "token-admin",
        },
    )

    assert state.created is False
    assert "POST /api/orders" in state.calls
    assert "GET /test-observers/events" in state.calls
    assert "DELETE /api/orders/order-1" in state.calls
    assert state.calls.index("POST /api/orders") < state.calls.index(
        "GET /test-observers/events"
    ) < state.calls.index("DELETE /api/orders/order-1")

    assert result["status"] == "EXECUTED"
    assert result["oracle_verdict"]["status"] == "VIOLATION"
    assert result["oracle_verdict"]["assertions"][0]["reason_code"] == (
        "EVENT_DELIVERY_COUNT_BELOW_MINIMUM"
    )
    assert result["cleanup_equivalence_receipt"]["equivalence_status"] == (
        "EQUIVALENT"
    )
    assert result["environment_restored"] is True
    assert result["finding"] is not None
    assert result["execution_receipt_bundle"]["complete"] is True
    assert result["execution_receipt_bundle"]["validation_errors"] == []
    assert result["execution_finalization_receipt"]["true_completed"] is True
    assert result["lifecycle_state"] == "TRUE_COMPLETED"

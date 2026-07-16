from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from benchmark_evaluator.http_observation_gateway import (
    EvaluatorHttpObservationGateway,
)
from ai_test_asset_center.discovery_policy_evaluation_runner import (
    PolicyEvaluationRunnerError,
    TrustedObservationStore,
)


_SIGNING_KEY = b"evaluator-owned-test-key-material-32-bytes-minimum"


class _TargetHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"path": self.path}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = b'{"created":true}'
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        return None


@pytest.fixture
def upstream() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(url: str, *, method: str) -> None:
    headers = {
        "X-QualiBug-Run-Id": "RUN-1",
        "X-QualiBug-Campaign-Id": "CMP-1",
        "X-QualiBug-Target-Id": "TARGET-1",
        "X-QualiBug-Obligation-Id": "OBL-1",
        "X-QualiBug-Execution-Id": "EXEC-1",
    }
    body = b"{}" if method == "POST" else None
    request = urllib.request.Request(
        url + "/resource",
        method=method,
        headers=headers,
        data=body,
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status in {200, 201}


def test_gateway_forwards_requests_and_seals_exact_attempt_counts(
    tmp_path: Path,
    upstream: str,
) -> None:
    workspace = tmp_path / "product"
    observation_root = tmp_path / "evaluator-observations"
    workspace.mkdir()
    observation_root.mkdir()
    gateway = EvaluatorHttpObservationGateway(
        observation_root=observation_root,
        signing_key=_SIGNING_KEY,
    )

    with gateway.observe(
        upstream_base_url=upstream,
        campaign_id="CMP-1",
        target_id="TARGET-1",
        environment_type="test",
    ) as proxy_url:
        _request(proxy_url, method="GET")
        _request(proxy_url, method="POST")

    store = TrustedObservationStore(
        observation_root,
        product_workspace_root=workspace,
        verification_key=_SIGNING_KEY,
    )
    observations = store.load(
        run_id="RUN-1",
        campaign_id="CMP-1",
        target_id="TARGET-1",
    )

    assert observations == [{
        "obligation_id": "OBL-1",
        "execution_id": "EXEC-1",
        "source_kind": "evaluator_http_proxy",
        "source_receipt_id": observations[0]["source_receipt_id"],
        "source_fingerprint": observations[0]["source_fingerprint"],
        "target_request_count": 2,
        "write_count": 1,
        "production_request_count": 0,
        "audit_receipt_ids": observations[0]["audit_receipt_ids"],
    }]
    assert len(observations[0]["source_fingerprint"]) == 64
    assert len(observations[0]["audit_receipt_ids"]) == 1


def test_store_rejects_a_tampered_gateway_pack(
    tmp_path: Path,
    upstream: str,
) -> None:
    workspace = tmp_path / "product"
    observation_root = tmp_path / "evaluator-observations"
    workspace.mkdir()
    observation_root.mkdir()
    gateway = EvaluatorHttpObservationGateway(
        observation_root=observation_root,
        signing_key=_SIGNING_KEY,
    )
    with gateway.observe(
        upstream_base_url=upstream,
        campaign_id="CMP-1",
        target_id="TARGET-1",
        environment_type="test",
    ) as proxy_url:
        _request(proxy_url, method="GET")
    pack_path = observation_root / "RUN-1.json"
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    pack["observations"][0]["target_request_count"] = 99
    pack_path.write_text(json.dumps(pack), encoding="utf-8")

    store = TrustedObservationStore(
        observation_root,
        product_workspace_root=workspace,
        verification_key=_SIGNING_KEY,
    )
    with pytest.raises(PolicyEvaluationRunnerError, match="authentication"):
        store.load(
            run_id="RUN-1",
            campaign_id="CMP-1",
            target_id="TARGET-1",
        )


def test_gateway_fails_closed_for_production() -> None:
    gateway = EvaluatorHttpObservationGateway(
        observation_root=Path.cwd(),
        signing_key=_SIGNING_KEY,
    )
    with pytest.raises(ValueError, match="non-production"):
        with gateway.observe(
            upstream_base_url="https://example.com",
            campaign_id="CMP-1",
            target_id="TARGET-1",
            environment_type="production",
        ):
            pass

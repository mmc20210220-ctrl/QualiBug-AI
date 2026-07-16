from __future__ import annotations

"""Evaluator-owned HTTP proxy that attests target request coverage."""

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

from ai_test_asset_center.evaluator_receipt_auth import seal_evaluator_artifact
from ai_test_asset_center.target_policy import (
    is_nonproduction_environment,
    normalize_base_url,
)


TRUSTED_OBSERVATION_PACK_SCHEMA = (
    "qualibug.evaluator-trusted-observation-pack.v1"
)
OBSERVATION_PACK_FINGERPRINT_FIELD = "observation_pack_fingerprint"
OBSERVATION_PACK_AUTHENTICATION_FIELD = "observation_pack_authentication"

_TRACE_HEADERS = {
    "run_id": "X-QualiBug-Run-Id",
    "campaign_id": "X-QualiBug-Campaign-Id",
    "target_id": "X-QualiBug-Target-Id",
    "obligation_id": "X-QualiBug-Obligation-Id",
    "execution_id": "X-QualiBug-Execution-Id",
}
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_HOP_BY_HOP = frozenset({
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
})
_MAX_REQUEST_BYTES = 10 * 1024 * 1024
_MAX_RESPONSE_BYTES = 20 * 1024 * 1024


def _canonical_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _identity(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > 240
        or "\r" in text
        or "\n" in text
    ):
        raise ValueError(f"gateway_trace_identity_invalid:{field}")
    return text


class _ObservationSession:
    def __init__(self, *, campaign_id: str, target_id: str) -> None:
        self.campaign_id = campaign_id
        self.target_id = target_id
        self.run_id = ""
        self.events: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.errors: list[str] = []
        self.lock = threading.Lock()

    def record(
        self,
        *,
        trace: dict[str, str],
        method: str,
        path: str,
        status: int,
        body: bytes,
    ) -> None:
        try:
            normalized = {
                field: _identity(trace.get(field), field)
                for field in _TRACE_HEADERS
            }
            if normalized["campaign_id"] != self.campaign_id:
                raise ValueError("gateway_campaign_id_mismatch")
            if normalized["target_id"] != self.target_id:
                raise ValueError("gateway_target_id_mismatch")
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,240}", normalized["run_id"]):
                raise ValueError("gateway_run_id_invalid")
            event_material = {
                "run_id": normalized["run_id"],
                "campaign_id": normalized["campaign_id"],
                "target_id": normalized["target_id"],
                "obligation_id": normalized["obligation_id"],
                "execution_id": normalized["execution_id"],
                "method": method,
                "path": path,
                "status": int(status),
                "request_body_fingerprint": hashlib.sha256(body).hexdigest(),
                "observed_at_ns": time.time_ns(),
            }
            event_fingerprint = _canonical_fingerprint(event_material)
            event = {
                **event_material,
                "event_fingerprint": event_fingerprint,
                "write": method in _WRITE_METHODS,
            }
            key = (normalized["obligation_id"], normalized["execution_id"])
            with self.lock:
                if self.run_id and self.run_id != normalized["run_id"]:
                    self.errors.append("gateway_run_id_mismatch")
                    return
                self.run_id = normalized["run_id"]
                self.events.setdefault(key, []).append(event)
        except ValueError as exc:
            with self.lock:
                self.errors.append(str(exc))

    def observations(self) -> list[dict[str, Any]]:
        with self.lock:
            if self.errors:
                raise RuntimeError(
                    "evaluator_http_gateway_identity_failure:" + ",".join(self.errors)
                )
            if not self.run_id or not self.events:
                raise RuntimeError("evaluator_http_gateway_observed_no_correlated_requests")
            rows: list[dict[str, Any]] = []
            for (obligation_id, execution_id), events in sorted(self.events.items()):
                fingerprint = _canonical_fingerprint(events)
                write_receipts = [
                    "gateway-write-" + event["event_fingerprint"][:24]
                    for event in events
                    if event["write"]
                ]
                rows.append({
                    "obligation_id": obligation_id,
                    "execution_id": execution_id,
                    "source_kind": "evaluator_http_proxy",
                    "source_receipt_id": "gateway-" + fingerprint[:24],
                    "source_fingerprint": fingerprint,
                    "target_request_count": len(events),
                    "write_count": len(write_receipts),
                    "production_request_count": 0,
                    "audit_receipt_ids": write_receipts,
                })
            return rows


class EvaluatorHttpObservationGateway:
    """Forward one scan to its target and seal independently observed counts."""

    def __init__(
        self,
        *,
        observation_root: Path | str,
        signing_key: str | bytes | bytearray,
        request_timeout_seconds: float = 60.0,
    ) -> None:
        self.observation_root = Path(observation_root).resolve()
        if not self.observation_root.is_dir():
            raise ValueError(
                f"observation_root_not_found:{self.observation_root}"
            )
        self.signing_key = signing_key
        self.request_timeout_seconds = float(request_timeout_seconds)
        if self.request_timeout_seconds <= 0:
            raise ValueError("gateway_request_timeout_invalid")

    @contextmanager
    def observe(
        self,
        *,
        upstream_base_url: str,
        campaign_id: str,
        target_id: str,
        environment_type: str,
    ) -> Iterator[str]:
        upstream = normalize_base_url(upstream_base_url)
        if not upstream:
            raise ValueError("gateway_upstream_url_invalid")
        if not is_nonproduction_environment(environment_type):
            raise ValueError("gateway requires an explicitly declared non-production target")
        session = _ObservationSession(
            campaign_id=_identity(campaign_id, "campaign_id"),
            target_id=_identity(target_id, "target_id"),
        )
        handler = self._handler(upstream, session)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(
            target=server.serve_forever,
            name="qualibug-evaluator-http-gateway",
            daemon=True,
        )
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("evaluator_http_gateway_shutdown_timeout")
            self._persist(session)

    def _handler(
        self,
        upstream: str,
        session: _ObservationSession,
    ) -> type[BaseHTTPRequestHandler]:
        timeout = self.request_timeout_seconds

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self._forward()

            def do_HEAD(self) -> None:  # noqa: N802
                self._forward()

            def do_OPTIONS(self) -> None:  # noqa: N802
                self._forward()

            def do_POST(self) -> None:  # noqa: N802
                self._forward()

            def do_PUT(self) -> None:  # noqa: N802
                self._forward()

            def do_PATCH(self) -> None:  # noqa: N802
                self._forward()

            def do_DELETE(self) -> None:  # noqa: N802
                self._forward()

            def _forward(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if length < 0 or length > _MAX_REQUEST_BYTES:
                    self.send_error(413, "request body too large")
                    return
                body = self.rfile.read(length) if length else b""
                present = {
                    field: str(self.headers.get(header) or "").strip()
                    for field, header in _TRACE_HEADERS.items()
                }
                populated = [field for field, value in present.items() if value]
                if populated and len(populated) != len(_TRACE_HEADERS):
                    with session.lock:
                        session.errors.append("gateway_trace_headers_partial")
                    self.send_error(400, "correlation headers incomplete")
                    return
                target_url = upstream.rstrip("/") + self.path
                forwarded_headers = {
                    key: value
                    for key, value in self.headers.items()
                    if key.lower() not in _HOP_BY_HOP
                    and key.lower() not in {
                        header.lower() for header in _TRACE_HEADERS.values()
                    }
                }
                request = urllib.request.Request(
                    target_url,
                    method=self.command,
                    data=body if body else None,
                    headers=forwarded_headers,
                )
                try:
                    with urllib.request.urlopen(request, timeout=timeout) as response:
                        status = int(response.status)
                        response_body = response.read(_MAX_RESPONSE_BYTES + 1)
                        response_headers = dict(response.headers.items())
                except urllib.error.HTTPError as exc:
                    status = int(exc.code)
                    response_body = exc.read(_MAX_RESPONSE_BYTES + 1)
                    response_headers = dict(exc.headers.items()) if exc.headers else {}
                except Exception as exc:
                    status = 502
                    response_body = json.dumps({
                        "error": f"upstream_transport_failed:{type(exc).__name__}",
                    }).encode("utf-8")
                    response_headers = {"Content-Type": "application/json"}
                if len(response_body) > _MAX_RESPONSE_BYTES:
                    with session.lock:
                        session.errors.append("gateway_upstream_response_too_large")
                    self.send_error(502, "upstream response too large")
                    return
                if populated:
                    session.record(
                        trace=present,
                        method=self.command.upper(),
                        path=self.path,
                        status=status,
                        body=body,
                    )
                self.send_response(status)
                for key, value in response_headers.items():
                    if key.lower() not in _HOP_BY_HOP:
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(response_body)

            def log_message(self, *_: object) -> None:
                return None

        return Handler

    def _persist(self, session: _ObservationSession) -> Path:
        observations = session.observations()
        payload = {
            "schema_version": TRUSTED_OBSERVATION_PACK_SCHEMA,
            "created_at_utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(),
            ),
            "run_id": session.run_id,
            "campaign_id": session.campaign_id,
            "target_id": session.target_id,
            "observations": observations,
        }
        sealed = seal_evaluator_artifact(
            payload,
            signing_key=self.signing_key,
            domain=TRUSTED_OBSERVATION_PACK_SCHEMA,
            fingerprint_field=OBSERVATION_PACK_FINGERPRINT_FIELD,
            authentication_field=OBSERVATION_PACK_AUTHENTICATION_FIELD,
        )
        path = self.observation_root / f"{session.run_id}.json"
        serialized = json.dumps(sealed, ensure_ascii=False, indent=2)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != sealed:
                raise RuntimeError(
                    f"immutable_observation_pack_conflict:{path}"
                )
            return path
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(serialized, encoding="utf-8")
        os.replace(temporary, path)
        return path

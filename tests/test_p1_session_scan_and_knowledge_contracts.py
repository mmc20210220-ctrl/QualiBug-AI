from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_test_asset_center import db_persistence, jwt_auth
from ai_test_asset_center.enterprise_knowledge_center.transaction_lock import (
    KnowledgeTransactionBusy,
    knowledge_transaction,
)
from ai_test_asset_center.private_pilot_ingest_handlers import IngestHandlersMixin
from ai_test_asset_center.private_pilot_scan_coordinator import (
    ScanLeaseBusy,
    project_scan_lease,
)
from ai_test_asset_center.private_pilot_scan_handlers import ScanHandlersMixin
from ai_test_asset_center.private_pilot_tenant_auth import (
    TenantAuthenticationError,
    _principal_from_headers,
)


def _create_account(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tenant: str = "tenant-a",
    username: str = "user-a",
) -> dict:
    monkeypatch.setenv("QUALIBUG_ALLOW_TENANT_PROVISIONING", "1")
    result = db_persistence.create_tenant(
        root,
        tenant,
        tenant,
        username=username,
        password="strong-password",
    )
    assert result["ok"] is True
    return result


def test_password_change_revokes_existing_cookie_and_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUALIBUG_JWT_SECRET", "session-test-secret")
    jwt_auth._cached_secret = None
    account = _create_account(tmp_path, monkeypatch)
    authenticated = db_persistence.authenticate_tenant(
        tmp_path,
        "user-a",
        "strong-password",
    )
    assert authenticated is not None
    token = jwt_auth.create_token(
        authenticated["tenant_id"],
        authenticated["role"],
        username=authenticated["username"],
        session_version=authenticated["session_version"],
    )
    before = _principal_from_headers(
        {"Cookie": f"qualibug_token={token}"},
        root=tmp_path,
    )
    assert before["tenant_id"] == "tenant-a"
    assert db_persistence.verify_api_key(tmp_path, account["api_key"]) == "tenant-a"

    changed = db_persistence.reset_tenant_password(
        tmp_path,
        tenant_id="tenant-a",
        username="user-a",
        current_password="strong-password",
        new_password="new-strong-password",
    )
    assert changed["ok"] is True
    assert changed["api_key_revoked"] is True
    with pytest.raises(TenantAuthenticationError, match="revoked"):
        _principal_from_headers(
            {"Cookie": f"qualibug_token={token}"},
            root=tmp_path,
        )
    assert db_persistence.verify_api_key(tmp_path, account["api_key"]) is None


def test_duplicate_username_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_account(tmp_path, monkeypatch, tenant="tenant-a", username="shared")
    second = db_persistence.create_tenant(
        tmp_path,
        "tenant-b",
        "tenant-b",
        username="shared",
        password="strong-password",
    )
    assert second == {"ok": False, "error": "USERNAME_EXISTS"}


def test_project_scan_lease_is_exclusive_across_threads(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with project_scan_lease(
            tmp_path,
            "project-a",
            mode="holder",
            tenant_id="tenant-a",
        ):
            entered.set()
            assert release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(5)
    with pytest.raises(ScanLeaseBusy):
        with project_scan_lease(
            tmp_path,
            "project-a",
            mode="contender",
            tenant_id="tenant-a",
            wait_seconds=0,
        ):
            pass
    release.set()
    thread.join(5)
    assert not thread.is_alive()


def test_knowledge_transaction_is_exclusive(tmp_path: Path) -> None:
    with knowledge_transaction(
        tmp_path,
        "project-a",
        operation="first",
        wait_seconds=0,
    ):
        with pytest.raises(KnowledgeTransactionBusy):
            with knowledge_transaction(
                tmp_path,
                "project-a",
                operation="second",
                wait_seconds=0,
            ):
                pass


class _RawUploadHarness(IngestHandlersMixin):
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self.rfile = io.BytesIO(body)
        self.headers = headers

    def _body(self) -> dict:  # pragma: no cover - legacy fallback seam
        return {"legacy": True}


def test_raw_upload_reader_preserves_bytes_without_base64() -> None:
    payload = b"\x00PK\x03\x04binary-enterprise-material"
    harness = _RawUploadHarness(
        payload,
        {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(payload)),
            "X-QualiBug-Filename": "package.zip",
            "X-QualiBug-Source-Type": "",
        },
    )
    request = harness._read_ingest_request()
    assert request["_raw_content"] == payload
    assert request["filename"] == "package.zip"
    assert request["transport_encoding"] == "raw_octet_stream"


class _ReportBindingHarness(ScanHandlersMixin):
    pass


class _PreflightHarness(ScanHandlersMixin):
    def _json(self, payload, status=200):
        return payload


def test_scan_preflight_accepts_markdown_api_as_executable_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = "markdown-api-preflight"
    service_dir = tmp_path / "platform_workspace" / project
    service_dir.mkdir(parents=True)
    (service_dir / "multi_service_config.json").write_text(
        json.dumps({"services": [{"name": "gateway"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_source_registry.list_source_assets",
        lambda project, root: [{"source_type": "markdown_api"}],
    )
    monkeypatch.setattr(
        "ai_test_asset_center.private_pilot_scan_handlers.build_target_policy_decision",
        lambda **kwargs: {
            "blocking_codes": [],
            "write_allowed": True,
            "read_allowed": True,
        },
    )

    result = _PreflightHarness()._handle_scan_preflight(
        project,
        tmp_path,
        {
            "base_url": "http://sandbox.example",
            "approved_base_url": "http://sandbox.example",
            "environment_type": "test",
            "environment_ref": "sandbox-1",
        },
    )

    assert result["ready"] is True
    assert "NO_API_SPEC" not in result["blocking_codes"]


def test_report_must_be_bound_to_current_scan(tmp_path: Path) -> None:
    project_dir = tmp_path / "platform_outputs" / "project-a"
    project_dir.mkdir(parents=True)
    report = project_dir / "intelligence_report.json"
    report.write_text(
        json.dumps({"scan_id": "old-scan", "real_findings": [{"title": "stale"}]}),
        encoding="utf-8",
    )
    harness = _ReportBindingHarness()
    bound, receipt = harness._bound_scan_report(
        "project-a",
        tmp_path,
        {"scan_id": "new-scan", "report_path": str(report.relative_to(tmp_path))},
    )
    assert bound == {}
    assert receipt["status"] == "unbound"
    assert receipt["result_scan_id"] == "new-scan"
    assert receipt["report_scan_id"] == "old-scan"

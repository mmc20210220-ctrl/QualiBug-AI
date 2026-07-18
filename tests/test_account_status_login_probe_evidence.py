from __future__ import annotations

import json
from typing import Any

from ai_test_asset_center import supplementary_behavior_slices as slices
from ai_test_asset_center.customer_delivery_gate import is_customer_deliverable_defect


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def read(self, _limit: int) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _wire_probe_inputs(monkeypatch, payload: dict[str, Any]) -> None:
    monkeypatch.setattr(slices, "_api_facts", lambda _text, _state_re: ([], [], []))
    monkeypatch.setattr(
        slices,
        "_discover_login_endpoint",
        lambda _endpoints: ("/api/auth/login", {"email": "", "password": ""}),
    )
    monkeypatch.setattr(
        slices,
        "load_settings_accounts",
        lambda _root, _project: (
            [{"email": "disabled@example.test", "password": "Secret-123", "role": "buyer", "status": "DISABLED"}],
            "",
        ),
    )
    monkeypatch.setattr(slices.urllib.request, "urlopen", lambda _request, timeout=10: _FakeResponse(payload))


def test_disabled_login_probe_stays_diagnostic_without_formal_oracle_chain(tmp_path, monkeypatch) -> None:
    _wire_probe_inputs(monkeypatch, {"token": "runtime-secret-token", "user": {"id": "u-1"}})

    findings = slices.probe_disabled_account_logins(
        tmp_path,
        "generic-project",
        "declared api doc",
        "http://target.example",
        campaign_id="camp-1",
        discovery_round=7,
    )

    assert len(findings) == 1
    finding = findings[0]
    assert not is_customer_deliverable_defect(finding)
    assert finding["raw_evidence"]["has_real_evidence"] is True
    assert finding["evidence_quality"]["level"] == "validated"
    assert finding["evidence_status"]["business_evidence_status"] == "VALIDATED"
    assert finding["reproduction"]["har_evidence"]["status_code"] == 200
    assert finding["raw_evidence"]["request_raw"]["body"]["password"] == "<REDACTED>"
    assert finding["raw_evidence"]["response_raw"]["body"]["token"] == "<REDACTED>"


def test_disabled_login_probe_does_not_promote_bare_200_rejection_envelope(tmp_path, monkeypatch) -> None:
    _wire_probe_inputs(monkeypatch, {"ok": False, "error": "account disabled"})

    findings = slices.probe_disabled_account_logins(
        tmp_path,
        "generic-project",
        "declared api doc",
        "http://target.example",
    )

    assert findings == []

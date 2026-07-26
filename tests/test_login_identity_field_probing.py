"""The login body's identity key must not be hardcoded to "username".

Two credential paths built the login body as ``{"username": ..., "password": ...}``
with no way to change the key, while ``runtime_connectivity_auth_preflight`` had
supported a configurable ``username_field`` all along. Against any system that
authenticates by email -- which is most consumer-facing enterprise systems -- both
paths got 401, produced no token, and every authenticated probe downstream degraded
to unauthenticated. Nothing reported "could not log in"; the run simply found less.

The fix mirrors the multi-path probing the same function already did for login
*paths*: try candidate identity keys, declared shape first.
"""

from __future__ import annotations

import json

import pytest

from ai_test_asset_center.enterprise_credential_manager import (
    ServiceCredential,
    _identity_field_candidates,
)


# ── candidate ordering ──────────────────────────────────────────────────────

def test_declared_field_is_used_alone() -> None:
    """A declared field wins outright.

    Probing past an explicit declaration would let a wrong-but-accepted shape mask
    a misconfiguration, so the declared value is not merely first -- it is the only
    one tried.
    """
    assert _identity_field_candidates("someone@example.com", "loginId") == ["loginId"]
    assert _identity_field_candidates("alice", "username") == ["username"]


def test_username_stays_first_for_a_plain_identity() -> None:
    """The pre-existing default must not regress for non-email systems."""
    assert _identity_field_candidates("alice")[0] == "username"


def test_email_shaped_identity_reorders_email_first() -> None:
    """An identity containing @ cannot succeed as "username" on such a system.

    Without the reorder the probe spends its budget on a shape that is guaranteed
    to 401 before ever reaching the one that works.
    """
    candidates = _identity_field_candidates("buyer01@example.com")
    assert candidates[0] == "email"
    assert "username" in candidates, "the default must remain reachable, not be dropped"


def test_candidates_are_unique_and_bounded() -> None:
    """Path count x field count bounds the request budget; keep the field side small."""
    for identity in ("alice", "buyer01@example.com"):
        candidates = _identity_field_candidates(identity)
        assert len(candidates) == len(set(candidates)), candidates
        assert len(candidates) <= 6, candidates


# ── the credential carries the resolved shape ───────────────────────────────

def test_credential_defaults_to_undeclared_field() -> None:
    """Empty means "probe", not "use empty string as the key"."""
    cred = ServiceCredential("gateway", "buyer")
    assert cred.username_field == ""
    assert cred.resolved_login_shape == {}


def test_username_field_survives_slots() -> None:
    """ServiceCredential uses __slots__; a field missing from it raises on assign."""
    cred = ServiceCredential("gateway", "buyer")
    cred.username_field = "email"
    cred.resolved_login_shape = {"identity_field": "email"}
    assert cred.username_field == "email"


# ── config plumbing ─────────────────────────────────────────────────────────

def test_username_field_is_metadata_not_a_role(tmp_path) -> None:
    """username_field sits beside role keys in the auth dict.

    Without it in METADATA_KEYS the loader reads it as a role named
    "username_field" whose config is a string, producing a junk entry.
    """
    from ai_test_asset_center.enterprise_credential_manager import EnterpriseCredentialManager

    manager = EnterpriseCredentialManager('probe_project', tmp_path)
    manager._load_service_auth(
        "gateway",
        "http://localhost:8080",
        {
            "type": "password_login",
            "login_api": "/api/auth/login",
            "username_field": "email",
            "buyer": {"username": "buyer01@example.com", "password": "Test@123456"},
        },
    )
    assert manager.store.get("gateway", "username_field") is None
    cred = manager.store.get("gateway", "buyer")
    assert cred is not None
    assert cred.username_field == "email"


def test_role_level_username_field_overrides_service_level(tmp_path) -> None:
    """One service can front several auth backends; the role-level value wins."""
    from ai_test_asset_center.enterprise_credential_manager import (
        EnterpriseCredentialManager,
        CredentialStore,
    )

    manager = EnterpriseCredentialManager('probe_project', tmp_path)
    manager._load_service_auth(
        "gateway",
        "http://localhost:8080",
        {
            "type": "password_login",
            "username_field": "email",
            "ops": {"username": "ops1", "password": "p", "username_field": "account"},
        },
    )
    assert manager.store.get("gateway", "ops").username_field == "account"


# ── the body actually built ─────────────────────────────────────────────────

def test_login_body_uses_the_probed_field(monkeypatch, tmp_path) -> None:
    """End-to-end on the body composition: an email identity must send "email".

    Drives the real login routine against a stub transport that accepts only the
    email shape, which is exactly how the benchmark target behaves.
    """
    from ai_test_asset_center import enterprise_credential_manager as ecm

    seen: list[dict] = []

    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._payload = json.dumps(payload).encode()

        def read(self, *_a):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def _fake_urlopen(req, timeout=None, allow_internal=None):
        body = json.loads(req.data.decode())
        seen.append(body)
        if "email" not in body:
            raise RuntimeError("401 invalid credentials")
        return _Resp({"token": "t0ken", "user": {"role": "buyer"}})

    monkeypatch.setattr(ecm, "safe_urlopen", _fake_urlopen)

    manager = ecm.EnterpriseCredentialManager('probe_project', tmp_path)
    cred = ecm.ServiceCredential("gateway", "buyer")
    cred.base_url = "http://localhost:8080"
    cred.login_api = "/api/auth/login"
    cred.username = "buyer01@example.com"
    cred.password = "Test@123456"
    manager.store.set(cred)

    result = manager.login("gateway", "buyer")
    assert result is not None, "email-authenticating target must produce a token"
    assert result.token == "t0ken"
    assert result.username_field == "email"
    assert result.resolved_login_shape["identity_field"] == "email"
    assert result.resolved_login_shape["declared"] == "probed"
    assert seen and "email" in seen[0], "email must be tried first for an @ identity"


def test_plain_username_target_still_works(monkeypatch, tmp_path) -> None:
    """The fix must not break the case that already worked."""
    from ai_test_asset_center import enterprise_credential_manager as ecm

    class _Resp:
        def read(self, *_a):
            return json.dumps({"token": "t0ken"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def _fake_urlopen(req, timeout=None, allow_internal=None):
        body = json.loads(req.data.decode())
        if "username" not in body:
            raise RuntimeError("401")
        return _Resp()

    monkeypatch.setattr(ecm, "safe_urlopen", _fake_urlopen)

    manager = ecm.EnterpriseCredentialManager('probe_project', tmp_path)
    cred = ecm.ServiceCredential("svc", "admin")
    cred.base_url = "http://localhost:9999"
    cred.login_api = "/auth/login"
    cred.username = "alice"
    cred.password = "pw"
    manager.store.set(cred)

    result = manager.login("svc", "admin")
    assert result is not None
    assert result.username_field == "username"

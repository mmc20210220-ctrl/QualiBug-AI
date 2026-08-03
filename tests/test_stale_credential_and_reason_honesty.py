"""A stale credential must not be presented as a live one, and a deferral must keep its reason.

Measured against a live 11-service benchmark target, three defects compounded into
the same outcome: 47 of 49 execution steps died on HTTP 401, and four of the five
resulting P1 "authorization" findings were the harness's own auth failures rather
than defects in the target -- all four endpoints answer 200 with a fresh token.

The release gate refused to publish them, which is the system working. But a
fabricated defect that only a downstream gate catches is still a defect factory,
and the reason codes attached to the blocked obligations were themselves false.

1. ``load_actor_tokens`` returned stored bearer tokens without checking ``exp``.
   The project's test_accounts.json held tokens four days expired.
2. The TEST_ACCOUNTS.md login fallback was dead: it required
   QUALIBUG_TARGET_BASE_URL, which the HTTP scan entrypoint never sets.
3. ``load_project_test_credentials`` could not read ``{"accounts": [...]}`` -- the
   shape the ingest API writes -- so the test-data bootstrap saw zero credentials
   and aborted at ``control_actor_login_failed``.

And in the reporting path, an obligation the compiler had deferred with a specific
reason was relabelled ``OBLIGATION_NOT_IN_PLAN`` / ``BUDGET_EXHAUSTED`` on a branch
that is only reached when the budget was NOT the constraint.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from ai_test_asset_center.experiment_runtime_credentials import _jwt_expired, load_actor_tokens


def _jwt(exp_offset_seconds: float) -> str:
    """A structurally valid JWT whose exp sits at now + offset. Signature is junk."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    claims = json.dumps({"sub": "u1", "exp": int(time.time() + exp_offset_seconds)}).encode()
    body = base64.urlsafe_b64encode(claims).decode().rstrip("=")
    return f"{header}.{body}.c2lnbmF0dXJl"


# ── expiry detection ────────────────────────────────────────────────────────

def test_expired_token_is_detected() -> None:
    assert _jwt_expired(_jwt(-3600)) is True


def test_live_token_is_not_discarded() -> None:
    assert _jwt_expired(_jwt(+3600)) is False


def test_token_expiring_inside_the_skew_counts_as_expired() -> None:
    """A token with seconds left will die mid-run; treat it as already gone."""
    assert _jwt_expired(_jwt(+5)) is True


def test_opaque_non_jwt_token_is_kept() -> None:
    """API keys and opaque bearers carry no exp claim and must not be dropped.

    Treating "cannot parse an expiry" as "expired" would discard every
    non-JWT credential in the product.
    """
    assert _jwt_expired("opaque-api-key-abc123") is False
    assert _jwt_expired("") is False
    assert _jwt_expired("a.b") is False


def test_jwt_without_exp_claim_is_kept() -> None:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(b'{"sub":"u1"}').decode().rstrip("=")
    assert _jwt_expired(f"{header}.{body}.sig") is False


def test_unparseable_payload_is_kept() -> None:
    assert _jwt_expired("aaa.!!!not-base64!!!.sig") is False


# ── the stored catalog no longer hands over dead tokens ─────────────────────

def _write_accounts(root: Path, project: str, token: str) -> None:
    path = root / "platform_inputs" / project / "test_accounts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"accounts": [
            {"name": "buyer01", "role": "buyer", "email": "b@example.com",
             "token": token, "status": "active", "account_ref": "buyer01"},
        ]}),
        encoding="utf-8",
    )


def test_live_stored_token_is_returned(tmp_path: Path) -> None:
    """The path that already worked must keep working."""
    _write_accounts(tmp_path, "p1", _jwt(+3600))
    tokens = load_actor_tokens(tmp_path, "p1")
    assert tokens.get("buyer")


def test_expired_stored_token_is_not_returned(tmp_path: Path) -> None:
    """The defect: a dead token was handed to the executor, which sent it, got 401,
    and recorded the 401 as the endpoint rejecting that actor."""
    _write_accounts(tmp_path, "p2", _jwt(-86400))
    tokens = load_actor_tokens(tmp_path, "p2")
    assert "buyer" not in tokens
    assert tokens == {} or not any(tokens.values())


def test_expired_token_is_reported_not_silently_dropped(tmp_path: Path, capsys) -> None:
    """Silence here is indistinguishable from "this project has no accounts"."""
    _write_accounts(tmp_path, "p3", _jwt(-86400))
    load_actor_tokens(tmp_path, "p3")
    out = capsys.readouterr().out
    assert "STALE" in out
    assert "buyer" in out


def test_base_url_is_accepted_from_the_caller(tmp_path: Path) -> None:
    """The MD-login fallback was dead under the HTTP scan entrypoint because it read
    only QUALIBUG_TARGET_BASE_URL. The parameter must exist and be honoured."""
    import inspect

    signature = inspect.signature(load_actor_tokens)
    assert "base_url" in signature.parameters
    assert signature.parameters["base_url"].kind is inspect.Parameter.KEYWORD_ONLY

    # With no accounts file at all the call must still be well-formed, not raise.
    assert load_actor_tokens(tmp_path, "absent", base_url="http://localhost:8080") == {}


def test_login_transport_failure_is_observable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from ai_test_asset_center import experiment_runtime_credentials as runtime_credentials

    path = tmp_path / "platform_inputs" / "p-login" / "TEST_ACCOUNTS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "| role | email | password |\n"
        "| --- | --- | --- |\n"
        "| buyer | buyer@example.test | secret |\n",
        encoding="utf-8",
    )

    def fail_login(*_args, **_kwargs):
        raise TimeoutError("login timed out")

    monkeypatch.setattr(runtime_credentials, "_http_request", fail_login)
    with caplog.at_level("WARNING"):
        tokens = load_actor_tokens(
            tmp_path,
            "p-login",
            base_url="http://target.invalid",
        )

    assert tokens == {}
    assert "actor_login_transport_failed" in caplog.text
    assert "role=buyer" in caplog.text
    assert "TimeoutError" in caplog.text


def test_executors_thread_the_approved_base_url() -> None:
    """A parameter nobody passes is the same as no parameter."""
    root = Path(__file__).resolve().parents[1] / "ai_test_asset_center"
    # The token-loading call site moved into the extracted executor core during
    # the architecture split; the batch executor still threads base_url into it.
    core = (root / "experiment_executor_core.py").read_text(encoding="utf-8")
    assert "load_actor_tokens(" in core
    assert "root, project, base_url=base_url" in core
    batch = (root / "experiment_batch_executor.py").read_text(encoding="utf-8")
    assert "base_url=base_url" in batch


def test_password_login_preferred_over_unexpired_stored_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpired JWT can still be orphaned after a target DB reset.

    Read probes may return empty 200 with the orphan id, while writes fail with a
    user-identity foreign key. When the catalog still declares a password and the
    caller supplies an approved base_url, login must win over the snapshot.
    """
    from ai_test_asset_center import experiment_runtime_credentials as runtime_credentials

    orphan = _jwt(+3600)
    path = tmp_path / "platform_inputs" / "p-refresh" / "test_accounts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "buyer01@example.com": {
                    "role": "buyer",
                    "email": "buyer01@example.com",
                    "password": "Test@123456",
                    "token": orphan,
                    "authenticated_role": "buyer",
                    "authenticated_status": "ACTIVE",
                    "status": "ACTIVE",
                }
            }
        ),
        encoding="utf-8",
    )
    live = _jwt(+7200)

    def fake_login(*, base_url, login_path, email, password):
        assert base_url == "http://target.example"
        assert email == "buyer01@example.com"
        assert password == "Test@123456"
        assert login_path.endswith("/api/auth/login")
        return live, 200

    monkeypatch.setattr(runtime_credentials, "_login_declared_account", fake_login)
    tokens = load_actor_tokens(
        tmp_path,
        "p-refresh",
        base_url="http://target.example",
    )
    assert tokens.get("buyer") == live
    assert tokens.get("buyer01@example.com") == live
    assert orphan not in tokens.values()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["buyer01@example.com"]["token"] == live


def test_password_login_failure_does_not_return_orphan_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_test_asset_center import experiment_runtime_credentials as runtime_credentials

    orphan = _jwt(+3600)
    path = tmp_path / "platform_inputs" / "p-orphan" / "test_accounts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "name": "buyer01",
                        "role": "buyer",
                        "email": "buyer01@example.com",
                        "password": "Test@123456",
                        "token": orphan,
                        "status": "ACTIVE",
                        "account_ref": "buyer01",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        runtime_credentials,
        "_login_declared_account",
        lambda **_kwargs: ("", 401),
    )
    tokens = load_actor_tokens(
        tmp_path,
        "p-orphan",
        base_url="http://target.example",
    )
    assert tokens == {}
    assert orphan not in tokens.values()


# ── the credential catalog reads the shape the product writes ───────────────

def test_accounts_container_shape_is_unwrapped(tmp_path: Path) -> None:
    """{"accounts": [...]} is what the ingest API writes.

    The dict-of-dicts comprehension skipped it because the value is a list, so a
    file holding eight accounts loaded as zero credentials.
    """
    from ai_test_asset_center.enterprise_pilot_runtime import load_project_test_credentials

    path = tmp_path / "platform_inputs" / "shape" / "test_accounts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"accounts": [
            {"role": "buyer", "email": "b@example.com", "password": "pw1"},
            {"role": "admin", "email": "a@example.com", "password": "pw2"},
        ]}),
        encoding="utf-8",
    )
    rows = load_project_test_credentials("shape", root=tmp_path)
    emails = {r.get("email") for r in rows}
    assert {"b@example.com", "a@example.com"} <= emails, rows


def test_legacy_dict_of_dicts_shape_still_loads(tmp_path: Path) -> None:
    """The pre-existing shape must not regress."""
    from ai_test_asset_center.enterprise_pilot_runtime import load_project_test_credentials

    path = tmp_path / "platform_inputs" / "legacy" / "test_accounts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"normal_user": {"username": "u1", "password": "pw"}}),
        encoding="utf-8",
    )
    rows = load_project_test_credentials("legacy", root=tmp_path)
    assert any(r.get("username") == "u1" for r in rows), rows


# ── a deferral keeps the reason the compiler gave it ────────────────────────

def _terminal_receipt_surface() -> str:
    """Manual terminal accounting now lives in the extracted terminal module.

    The support module keeps a compatibility re-export; the guard follows the
    implementation instead of a frozen file location.
    """
    root = Path(__file__).resolve().parents[1] / "ai_test_asset_center"
    return (
        (root / "discovery_runtime_execution_support.py").read_text(encoding="utf-8")
        + (root / "discovery_runtime_execution_terminal.py").read_text(encoding="utf-8")
    )


def test_deferred_compile_receipt_keeps_its_own_reason_code() -> None:
    """MISSING_PRIMARY_OPERATION must not be rewritten as BUDGET_EXHAUSTED.

    The relabel was reached only when the obligation was NOT pending for budget,
    so 'budget exhausted' was the one explanation that could not be true. A wrong
    reason code is worse than a missing one: it sends the next reader looking for
    capacity they already have.
    """
    source = _terminal_receipt_surface()

    assert 'compile_status == "DEFERRED"' in source
    assert '_text(compile_receipt.get("reason_code"))' in source
    branch_at = source.index('compile_status == "DEFERRED"')
    pending_at = source.index("elif obligation_id in pending_ids:")
    assert branch_at < pending_at, "the receipt's own reason must be honoured before any fallback"


def test_unattributed_fallback_does_not_claim_budget_exhaustion() -> None:
    """The final fallback runs precisely when the budget is not the cause."""
    source = _terminal_receipt_surface()

    fallback_at = source.index('"reason_code": "OBLIGATION_NOT_IN_PLAN"')
    tail = source[fallback_at: fallback_at + 400]
    assert "NOT_IN_PLAN_REASON_UNATTRIBUTED" in tail
    assert '"BUDGET_EXHAUSTED"' not in tail

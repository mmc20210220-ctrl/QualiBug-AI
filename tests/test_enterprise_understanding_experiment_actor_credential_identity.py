"""The executor must never substitute another same-role account token."""
from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center import experiment_executor as executor
from ai_test_asset_center import experiment_executor_governance as governance


def _write_accounts(root: Path, project: str, accounts: list[dict]) -> None:
    target = root / "platform_inputs" / project
    target.mkdir(parents=True, exist_ok=True)
    (target / "test_accounts.json").write_text(
        json.dumps({"accounts": accounts}, ensure_ascii=False),
        encoding="utf-8",
    )


def _account(
    account_ref: str,
    token: str,
    *,
    role: str = "仓库员",
    status: str = "ACTIVE",
) -> dict:
    return {
        "account_ref": account_ref,
        "role": role,
        "token": token,
        "status": status,
    }


def test_same_role_accounts_keep_exact_tokens_but_remove_role_aliases(
    tmp_path: Path,
) -> None:
    _write_accounts(
        tmp_path,
        "demo",
        [
            _account("warehouse-a", "token-a"),
            _account("warehouse-b", "token-b"),
        ],
    )

    tokens = governance._identity_safe_load_actor_tokens(tmp_path, "demo")

    assert tokens["warehouse-a"] == "token-a"
    assert tokens["secret_ref:test_accounts:warehouse-a"] == "token-a"
    assert tokens["warehouse-b"] == "token-b"
    assert tokens["secret_ref:test_accounts:warehouse-b"] == "token-b"
    assert "仓库员" not in tokens
    assert "secret_ref:test_accounts:仓库员" not in tokens
    assert "secret_ref:context:仓库员" not in tokens
    assert "secret_ref:actor:仓库员" not in tokens


def test_single_active_account_keeps_role_compatibility_alias(tmp_path: Path) -> None:
    _write_accounts(
        tmp_path,
        "demo",
        [_account("warehouse-a", "token-a")],
    )

    tokens = governance._identity_safe_load_actor_tokens(tmp_path, "demo")

    assert tokens["仓库员"] == "token-a"
    assert tokens["secret_ref:test_accounts:仓库员"] == "token-a"


def test_disabled_second_account_does_not_make_role_ambiguous(tmp_path: Path) -> None:
    _write_accounts(
        tmp_path,
        "demo",
        [
            _account("warehouse-a", "token-a"),
            _account("warehouse-disabled", "token-disabled", status="DISABLED"),
        ],
    )

    tokens = governance._identity_safe_load_actor_tokens(tmp_path, "demo")

    assert tokens["仓库员"] == "token-a"
    assert "warehouse-disabled" in tokens
    assert "secret_ref:test_accounts:warehouse-disabled" in tokens


def test_authenticated_role_and_email_local_part_preserve_exact_actor_identity(
    tmp_path: Path,
) -> None:
    _write_accounts(
        tmp_path,
        "demo",
        [
            {
                "account_ref": "buyer01@example.com",
                "email": "buyer01@example.com",
                "role": "普通买家",
                "authenticated_role": "buyer",
                "authenticated_status": "ACTIVE",
                "token": "token-a",
            },
            {
                "account_ref": "buyer02@example.com",
                "email": "buyer02@example.com",
                "role": "普通买家",
                "authenticated_role": "buyer",
                "authenticated_status": "ACTIVE",
                "token": "token-b",
            },
        ],
    )

    tokens = governance._identity_safe_load_actor_tokens(tmp_path, "demo")

    assert tokens["secret_ref:test_accounts:buyer01@example.com"] == "token-a"
    assert tokens["secret_ref:test_accounts:buyer01"] == "token-a"
    assert tokens["secret_ref:test_accounts:buyer02@example.com"] == "token-b"
    assert tokens["secret_ref:test_accounts:buyer02"] == "token-b"
    assert "secret_ref:actor:buyer" not in tokens


def test_account_qualified_actor_never_falls_back_to_role_token() -> None:
    actor = {
        "actor_id": "actor:warehouse-a",
        "role": "仓库员",
        "account_ref": "warehouse-a",
        "credential_secret_ref": "secret_ref:test_accounts:warehouse-a",
        "identity_match_status": "EXACT",
    }
    wrong_only = {
        "仓库员": "token-b",
        "secret_ref:test_accounts:warehouse-b": "token-b",
    }

    assert governance._resolve_token(actor, wrong_only) == ""

    exact = {
        **wrong_only,
        "secret_ref:test_accounts:warehouse-a": "token-a",
    }
    assert governance._resolve_token(actor, exact) == "token-a"


def test_role_only_actor_keeps_single_account_compatibility() -> None:
    actor = {
        "actor_id": "actor:warehouse-role",
        "role": "仓库员",
        "credential_secret_ref": "secret_ref:actor:仓库员",
    }

    assert governance._resolve_token(actor, {"仓库员": "token-a"}) == "token-a"


def test_exact_account_preflight_blocks_when_only_role_token_exists() -> None:
    experiment = {
        "control_plan": [
            {
                "actor_ref": "actor:warehouse-a",
                "operation_ref": "op:register",
            }
        ],
        "treatment_plan": [],
        "binding_plan": [],
        "fixture_dag": {"nodes": []},
    }
    behavior_ir = {
        "actors": [
            {
                "actor_id": "actor:warehouse-a",
                "role": "仓库员",
                "account_ref": "warehouse-a",
                "credential_secret_ref": "secret_ref:test_accounts:warehouse-a",
                "identity_match_status": "EXACT",
            }
        ]
    }

    ok, reason, detail = governance._exact_secret_preflight(
        experiment,
        behavior_ir=behavior_ir,
        actor_tokens={"仓库员": "token-b"},
        deferred_actor_refs=set(),
    )

    assert ok is False
    assert reason == "BLOCKED_MISSING_ACTOR"
    assert detail == "exact_credential_unresolved:actor:warehouse-a"


def test_exact_account_preflight_accepts_declared_secret() -> None:
    experiment = {
        "control_plan": [],
        "treatment_plan": [
            {
                "actor_ref": "actor:warehouse-a",
                "operation_ref": "op:register",
            }
        ],
        "binding_plan": [],
        "fixture_dag": {"nodes": []},
    }
    behavior_ir = {
        "actors": [
            {
                "actor_id": "actor:warehouse-a",
                "role": "仓库员",
                "account_ref": "warehouse-a",
                "credential_secret_ref": "secret_ref:test_accounts:warehouse-a",
                "identity_match_status": "EXACT",
            }
        ]
    }

    assert governance._exact_secret_preflight(
        experiment,
        behavior_ir=behavior_ir,
        actor_tokens={"secret_ref:test_accounts:warehouse-a": "token-a"},
        deferred_actor_refs=set(),
    ) == (True, "", "")


def test_graph_only_actor_remains_owned_by_graph_target_authority() -> None:
    experiment = {
        "control_plan": [],
        "treatment_plan": [
            {
                "actor_ref": "actor:warehouse-a",
                "operation_ref": "op:register",
            }
        ],
        "binding_plan": [],
        "fixture_dag": {"nodes": []},
    }
    behavior_ir = {
        "actors": [
            {
                "actor_id": "actor:warehouse-a",
                "role": "仓库员",
                "account_ref": "warehouse-a",
                "credential_secret_ref": "secret_ref:test_accounts:warehouse-a",
                "identity_match_status": "EXACT",
            }
        ]
    }

    assert governance._exact_secret_preflight(
        experiment,
        behavior_ir=behavior_ir,
        actor_tokens={},
        deferred_actor_refs={"actor:warehouse-a"},
    ) == (True, "", "")


def test_governed_core_hooks_use_same_identity_safe_authorities() -> None:
    governance._sync_core_hooks()

    assert governance._core.load_actor_tokens is governance.load_actor_tokens
    assert governance._core._resolve_token is governance._resolve_token


def test_public_facade_preserves_historical_loader_identity() -> None:
    assert executor.load_actor_tokens is executor._runtime_load_actor_tokens
    assert governance.load_actor_tokens is governance._identity_safe_load_actor_tokens

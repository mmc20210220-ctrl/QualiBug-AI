"""The executor must never substitute another same-role account token."""
from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center import experiment_executor as executor


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

    tokens = executor.load_actor_tokens(tmp_path, "demo")

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

    tokens = executor.load_actor_tokens(tmp_path, "demo")

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

    tokens = executor.load_actor_tokens(tmp_path, "demo")

    assert tokens["仓库员"] == "token-a"
    assert "warehouse-disabled" in tokens
    assert "secret_ref:test_accounts:warehouse-disabled" in tokens


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

    assert executor._resolve_token(actor, wrong_only) == ""

    exact = {
        **wrong_only,
        "secret_ref:test_accounts:warehouse-a": "token-a",
    }
    assert executor._resolve_token(actor, exact) == "token-a"


def test_role_only_actor_keeps_single_account_compatibility() -> None:
    actor = {
        "actor_id": "actor:warehouse-role",
        "role": "仓库员",
        "credential_secret_ref": "secret_ref:actor:仓库员",
    }

    assert executor._resolve_token(actor, {"仓库员": "token-a"}) == "token-a"


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

    ok, reason, detail = executor._exact_secret_preflight(
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

    assert executor._exact_secret_preflight(
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

    assert executor._exact_secret_preflight(
        experiment,
        behavior_ir=behavior_ir,
        actor_tokens={},
        deferred_actor_refs={"actor:warehouse-a"},
    ) == (True, "", "")


def test_core_hooks_use_same_identity_safe_authorities() -> None:
    executor._sync_core_hooks()

    assert executor._core.load_actor_tokens is executor.load_actor_tokens
    assert executor._core._resolve_token is executor._resolve_token

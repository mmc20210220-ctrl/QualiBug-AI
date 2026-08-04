"""Which adapters may be observed through — declared or product-owned.

Two properties are pinned:

1. A target adapter becomes available because the CUSTOMER declared the thing it
   observes. Never from a hostname, a port, a URL scheme or the presence of a
   driver.
2. Product-owned evidence adapters may be available because the governed
   executor creates their evidence itself. They never imply access to another
   customer surface or relax target declaration requirements.
"""
from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.adapter_capability import (
    BASELINE_ADAPTERS,
    DECLARATION_REQUIRED,
    PRODUCT_OWNED_ADAPTERS,
    missing_declaration_reason,
    operator_db_dsn_declared,
    resolve_available_adapters,
)


EXPECTED_DEFAULT_ADAPTERS = BASELINE_ADAPTERS | PRODUCT_OWNED_ADAPTERS


def _write_config(root: Path, project: str, config: dict) -> Path:
    directory = root / "platform_workspace" / project
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "multi_service_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _db(name: str = "app_db") -> dict:
    return {
        "host": "db.internal",
        "port": 5432,
        "name": name,
        "user": "reader",
        "password": "p",
    }


def test_target_and_product_owned_baselines_are_distinct(tmp_path: Path) -> None:
    assert BASELINE_ADAPTERS == frozenset({"http_api"})
    assert PRODUCT_OWNED_ADAPTERS == frozenset({"process_ledger"})
    assert resolve_available_adapters(
        tmp_path,
        "absent_project",
    ) == EXPECTED_DEFAULT_ADAPTERS


def test_service_without_a_db_block_gets_no_db_adapter(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "proj",
        {"services": [{"name": "api", "base_url": "http://x"}]},
    )
    assert resolve_available_adapters(tmp_path, "proj") == EXPECTED_DEFAULT_ADAPTERS


def test_declared_db_block_makes_db_sql_available(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "proj",
        {"services": [{"name": "api", "db": _db()}]},
    )
    assert resolve_available_adapters(tmp_path, "proj") == (
        EXPECTED_DEFAULT_ADAPTERS | {"db_sql"}
    )


def test_multi_database_topology_declares_db_sql_once(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "proj",
        {"services": [
            {"name": "order", "db": _db("order_db")},
            {"name": "payment", "db": _db("payment_db")},
        ]},
    )
    assert resolve_available_adapters(tmp_path, "proj") == (
        EXPECTED_DEFAULT_ADAPTERS | {"db_sql"}
    )


def test_malformed_config_falls_back_to_known_baselines(tmp_path: Path) -> None:
    directory = tmp_path / "platform_workspace" / "proj"
    directory.mkdir(parents=True)
    (directory / "multi_service_config.json").write_text(
        "{not json",
        encoding="utf-8",
    )

    assert resolve_available_adapters(tmp_path, "proj") == EXPECTED_DEFAULT_ADAPTERS


def test_empty_db_block_is_not_a_declaration(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "proj",
        {"services": [{"name": "api", "db": {}}]},
    )
    assert resolve_available_adapters(tmp_path, "proj") == EXPECTED_DEFAULT_ADAPTERS


def test_runtime_contract_may_declare_target_adapter(tmp_path: Path) -> None:
    _write_config(tmp_path, "proj", {"services": [{"name": "api"}]})
    resolved = resolve_available_adapters(
        tmp_path,
        "proj",
        {"declared_adapters": ["db_sql"]},
    )
    assert resolved == EXPECTED_DEFAULT_ADAPTERS | {"db_sql"}


def test_unknown_adapter_name_is_ignored_not_trusted(tmp_path: Path) -> None:
    _write_config(tmp_path, "proj", {"services": [{"name": "api"}]})
    resolved = resolve_available_adapters(
        tmp_path,
        "proj",
        {"declared_adapters": ["telepathy", "db_sql"]},
    )
    assert "telepathy" not in resolved
    assert "db_sql" in resolved
    assert "process_ledger" in resolved


def test_nothing_is_inferred_from_a_base_url(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "proj",
        {"services": [{
            "name": "api",
            "base_url": "postgresql://db.internal:5432/app",
        }]},
    )
    assert resolve_available_adapters(tmp_path, "proj") == EXPECTED_DEFAULT_ADAPTERS


def test_operator_dsn_is_a_data_layer_declaration(tmp_path: Path) -> None:
    # The governed cleanup executor resolves its credential with QUALIBUG_DB_DSN
    # as the first authority (operator override). Compile must answer with the
    # same authority, or writes block as non-reversible while the executor
    # already holds the reversal key.
    env = {"QUALIBUG_DB_DSN": "postgresql://u:p@localhost:5432/app"}
    assert operator_db_dsn_declared(env)
    assert resolve_available_adapters(tmp_path, "absent_project", env=env) == (
        EXPECTED_DEFAULT_ADAPTERS | {"db_sql"}
    )


def test_blank_or_missing_dsn_is_no_declaration(tmp_path: Path) -> None:
    for env in ({}, {"QUALIBUG_DB_DSN": ""}, {"QUALIBUG_DB_DSN": "   "}):
        assert not operator_db_dsn_declared(env)
        assert resolve_available_adapters(
            tmp_path, "absent_project", env=env
        ) == EXPECTED_DEFAULT_ADAPTERS


def test_refusal_reason_names_authority_source() -> None:
    reason = missing_declaration_reason("db_sql")
    assert "db_sql" in reason
    assert DECLARATION_REQUIRED["db_sql"] in reason
    assert "product_owned" in missing_declaration_reason("process_ledger")
    assert "unknown" in missing_declaration_reason("telepathy")


def test_compiler_facade_forwards_the_adapter_set() -> None:
    import inspect

    from ai_test_asset_center.experiment_compiler import compile_experiments
    from ai_test_asset_center.experiment_compiler_base import (
        compile_experiments as base_compile_experiments,
    )

    assert "available_adapters" in inspect.signature(compile_experiments).parameters
    assert "available_adapters" in inspect.signature(
        base_compile_experiments
    ).parameters


def test_planning_reads_runtime_contract_from_campaign_context() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center"
        / "discovery_runtime_planning.py"
    ).read_text(encoding="utf-8")
    assert "resolve_available_adapters(" in source
    assert 'inputs.campaign_context.get("_runtime_contract")' in source
    assert 'getattr(inputs, "runtime_contract"' not in source

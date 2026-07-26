"""Which adapters a target may be observed through — declared, never inferred.

``compile_observer_requirements`` correctly refuses an observer whose adapter is not
available. But the available set was hardcoded ``{"http_api"}`` at every site, so
registering a non-http observer could never make it usable on the main chain: the observer
existed, compiled only in a test that passed the wider set by hand, and was refused by the
production compiler.

Two properties are pinned:

1. An adapter becomes available because the CUSTOMER declared the thing it observes, in the
   same multi_service_config.json the environment gate reads. Never from a hostname, a port,
   a URL scheme or the presence of a driver — target_policy states the same rule for write
   safety, and the reason generalizes: inferring a database from an answering port 5432 means
   reading a store the customer never pointed the product at.
2. Every failure direction is FEWER adapters. A malformed config, a missing config and an
   unknown adapter name all fall back to the baseline rather than widening it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.adapter_capability import (
    BASELINE_ADAPTERS,
    DECLARATION_REQUIRED,
    missing_declaration_reason,
    resolve_available_adapters,
)


def _write_config(root: Path, project: str, config: dict) -> Path:
    directory = root / "platform_workspace" / project
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "multi_service_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _db(name: str = "app_db") -> dict:
    return {"host": "db.internal", "port": 5432, "name": name, "user": "reader", "password": "p"}


def test_http_api_is_always_available(tmp_path: Path) -> None:
    """The product is defined around an HTTP target."""
    assert BASELINE_ADAPTERS == frozenset({"http_api"})
    assert resolve_available_adapters(tmp_path, "absent_project") == BASELINE_ADAPTERS


def test_service_without_a_db_block_gets_no_db_adapter(tmp_path: Path) -> None:
    _write_config(tmp_path, "proj", {"services": [{"name": "api", "base_url": "http://x"}]})
    assert resolve_available_adapters(tmp_path, "proj") == frozenset({"http_api"})


def test_declared_db_block_makes_db_sql_available(tmp_path: Path) -> None:
    _write_config(tmp_path, "proj", {"services": [{"name": "api", "db": _db()}]})
    assert resolve_available_adapters(tmp_path, "proj") == frozenset({"http_api", "db_sql"})


def test_multi_database_topology_declares_db_sql_once(tmp_path: Path) -> None:
    _write_config(tmp_path, "proj", {"services": [
        {"name": "order", "db": _db("order_db")},
        {"name": "payment", "db": _db("payment_db")},
    ]})
    assert resolve_available_adapters(tmp_path, "proj") == frozenset({"http_api", "db_sql"})


def test_malformed_config_falls_back_to_the_baseline(tmp_path: Path) -> None:
    """A broken config must never widen the set. Fail-closed direction is fewer adapters."""
    directory = tmp_path / "platform_workspace" / "proj"
    directory.mkdir(parents=True)
    (directory / "multi_service_config.json").write_text("{not json", encoding="utf-8")

    assert resolve_available_adapters(tmp_path, "proj") == BASELINE_ADAPTERS


def test_empty_db_block_is_not_a_declaration(tmp_path: Path) -> None:
    _write_config(tmp_path, "proj", {"services": [{"name": "api", "db": {}}]})
    assert resolve_available_adapters(tmp_path, "proj") == BASELINE_ADAPTERS


def test_runtime_contract_may_declare_an_adapter(tmp_path: Path) -> None:
    """A deployment can configure capability outside the project file.

    Still a declaration, not an inference.
    """
    _write_config(tmp_path, "proj", {"services": [{"name": "api"}]})
    resolved = resolve_available_adapters(
        tmp_path, "proj", {"declared_adapters": ["db_sql"]}
    )
    assert resolved == frozenset({"http_api", "db_sql"})


def test_unknown_adapter_name_is_ignored_not_trusted(tmp_path: Path) -> None:
    _write_config(tmp_path, "proj", {"services": [{"name": "api"}]})
    resolved = resolve_available_adapters(
        tmp_path, "proj", {"declared_adapters": ["telepathy", "db_sql"]}
    )
    assert "telepathy" not in resolved
    assert "db_sql" in resolved


def test_nothing_is_inferred_from_a_base_url(tmp_path: Path) -> None:
    """A postgres-looking URL in a base_url must not make db_sql available."""
    _write_config(tmp_path, "proj", {"services": [
        {"name": "api", "base_url": "postgresql://db.internal:5432/app"},
    ]})
    assert resolve_available_adapters(tmp_path, "proj") == BASELINE_ADAPTERS


def test_refusal_reason_names_what_would_have_to_be_declared() -> None:
    """An operator needs to know what to declare, not just that it was refused."""
    reason = missing_declaration_reason("db_sql")
    assert "db_sql" in reason
    assert DECLARATION_REQUIRED["db_sql"] in reason
    assert "unknown" in missing_declaration_reason("telepathy")


def test_compiler_facade_forwards_the_adapter_set() -> None:
    """The threading is the point: without it a registered observer stays unusable."""
    import inspect

    from ai_test_asset_center.experiment_compiler import compile_experiments
    from ai_test_asset_center.experiment_compiler_base import (
        compile_experiments as base_compile_experiments,
    )

    assert "available_adapters" in inspect.signature(compile_experiments).parameters
    assert "available_adapters" in inspect.signature(base_compile_experiments).parameters


def test_planning_reads_the_runtime_contract_from_campaign_context() -> None:
    """It is campaign_context["_runtime_contract"], not an attribute on inputs.

    Reading it as an attribute silently yielded None, which made the contract-declared
    adapter path dead code.
    """
    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center" / "discovery_runtime_planning.py"
    ).read_text(encoding="utf-8")
    assert 'resolve_available_adapters(' in source
    assert 'inputs.campaign_context.get("_runtime_contract")' in source
    assert 'getattr(inputs, "runtime_contract"' not in source

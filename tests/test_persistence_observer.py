"""The first non-http observer: persistence surface, adapter db_sql.

Database-level defects — dangling references, aggregate-vs-detail divergence, state values
outside a declared enumeration, cross-store inconsistency — were structurally unreachable
because every built-in observer declared adapter ``http_api``.

These tests exercise the whole governance boundary WITHOUT a live database, because every
refusal path must be provable independently of whether a driver or a server happens to be
available:

* customer-declared sources only, never the evaluator-owned BENCHMARK_MANIFEST
* declared non-production only, for reads as well as writes
* identifiers validated against the introspected schema before interpolation
* every refusal carries a named reason code, so "not observed" can never be mistaken for
  "observed and clean"
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center import observer_contracts_base as ocb
from ai_test_asset_center import persistence_observer as po
from ai_test_asset_center.observer_contracts_base import OBSERVER_REGISTRY
from ai_test_asset_center.persistence_observer import (
    ADAPTER,
    EVIDENCE_KEY,
    OBSERVER_ID,
    PersistenceObserverError,
    _validated_identifier,
    install_persistence_observer,
    observe_persistence_state,
    persistence_read_allowed,
    resolve_declared_data_sources,
)


def _write_config(root: Path, project: str, config: dict) -> Path:
    directory = root / "platform_workspace" / project
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "multi_service_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _service(name: str, *, environment: str = "test", db: dict | None = None) -> dict:
    service: dict = {"name": name, "base_url": f"http://{name}.local", "environment": environment}
    if db is not None:
        service["db"] = db
    return service


def _full_db(name: str = "orders_db") -> dict:
    return {"host": "db.internal", "port": 5432, "name": name, "user": "reader", "password": "p"}


# ── declared source resolution ──────────────────────────────────────────────

def test_multi_service_declares_multiple_databases(tmp_path: Path) -> None:
    """Multi-service / multi-database topology falls out of the declared config."""
    _write_config(tmp_path, "proj", {"environment": "test", "services": [
        _service("order", db=_full_db("order_db")),
        _service("payment", db=_full_db("payment_db")),
        _service("gateway"),  # no db block -- not a database source
    ]})
    sources = resolve_declared_data_sources(tmp_path, "proj")

    assert [item["module"] for item in sources] == ["order", "payment"]
    assert "order_db" in sources[0]["dsn"]
    assert "payment_db" in sources[1]["dsn"]


def test_no_declared_database_returns_empty_not_an_error(tmp_path: Path) -> None:
    _write_config(tmp_path, "proj", {"services": [_service("gateway")]})
    assert resolve_declared_data_sources(tmp_path, "proj") == []


@pytest.mark.parametrize("missing", ["host", "name", "user"])
def test_partially_declared_database_is_refused(tmp_path: Path, missing: str) -> None:
    """Partially declared is not declared -- refuse rather than default a value.

    Guessing a host, port or database name would make the observer read something the
    customer never pointed it at.
    """
    db = _full_db()
    db.pop(missing)
    _write_config(tmp_path, "proj", {"services": [_service("order", db=db)]})

    with pytest.raises(PersistenceObserverError) as excinfo:
        resolve_declared_data_sources(tmp_path, "proj")
    assert "incomplete" in str(excinfo.value)


def test_unreadable_config_raises_rather_than_looking_empty(tmp_path: Path) -> None:
    directory = tmp_path / "platform_workspace" / "proj"
    directory.mkdir(parents=True)
    (directory / "multi_service_config.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(PersistenceObserverError) as excinfo:
        resolve_declared_data_sources(tmp_path, "proj")
    assert "unreadable" in str(excinfo.value)


def test_benchmark_manifest_is_never_consulted(tmp_path: Path) -> None:
    """BENCHMARK_MANIFEST.json is evaluator-owned and must not reach runtime context.

    The pre-existing _discover_dsn reads it FIRST, which is why that path is unfit for the
    product chain.
    """
    (tmp_path / "BENCHMARK_MANIFEST.json").write_text(
        json.dumps({"databases": {"evaluator": "postgresql://u:p@evaluator:5432/hidden"}}),
        encoding="utf-8",
    )
    _write_config(tmp_path, "proj", {"services": [_service("gateway")]})

    sources = resolve_declared_data_sources(tmp_path, "proj")
    assert sources == []
    assert "evaluator" not in json.dumps(sources)


# ── governance gate ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "environment,expected_reason",
    [
        ("production", "production_environment_blocked"),
        ("prod", "production_environment_blocked"),
        ("live", "production_environment_blocked"),
        ("customer-qa", "environment_not_recognized_nonprod"),
    ],
)
def test_read_is_refused_outside_declared_non_production(
    tmp_path: Path, environment: str, expected_reason: str
) -> None:
    """Reads are gated like writes: a production read exposes real customer data."""
    _write_config(tmp_path, "proj", {"environment": environment,
                                     "services": [_service("order", environment=environment,
                                                           db=_full_db())]})
    allowed, reason = persistence_read_allowed(tmp_path, "proj", {})

    assert allowed is False
    assert expected_reason in reason


def test_read_is_refused_when_no_environment_is_declared_anywhere(tmp_path: Path) -> None:
    """Undeclared is not "probably safe".

    load_project_environment_kind also reads services[].environment, so a project-level
    blank alone is not undeclared — this pins the case where nothing declares it at all.
    """
    directory = tmp_path / "platform_workspace" / "proj"
    directory.mkdir(parents=True)
    (directory / "multi_service_config.json").write_text(
        json.dumps({"services": [{"name": "order", "db": _full_db()}]}), encoding="utf-8"
    )
    allowed, reason = persistence_read_allowed(tmp_path, "proj", {})

    assert allowed is False
    assert "environment_kind_undeclared" in reason


@pytest.mark.parametrize("environment", ["test", "staging", "uat", "sit", "dev", "sandbox"])
def test_read_is_permitted_on_declared_non_production(tmp_path: Path, environment: str) -> None:
    _write_config(tmp_path, "proj", {"environment": environment,
                                     "services": [_service("order", db=_full_db())]})
    allowed, reason = persistence_read_allowed(tmp_path, "proj", {})

    assert allowed is True, reason


# ── identifier safety ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "identifier",
    ["orders; DROP TABLE users", "orders--", "1orders", "", "a" * 80, "order s", "ord'ers"],
)
def test_unsafe_identifier_shapes_are_refused(identifier: str) -> None:
    with pytest.raises(PersistenceObserverError) as excinfo:
        _validated_identifier(identifier, {"orders"})
    assert "identifier_shape_refused" in str(excinfo.value)


def test_identifier_absent_from_introspected_schema_is_refused() -> None:
    """A well-shaped name the database never reported is still refused.

    No driver parameterizes identifiers, so the introspected schema is the only thing that
    makes interpolation safe.
    """
    with pytest.raises(PersistenceObserverError) as excinfo:
        _validated_identifier("secret_table", {"orders", "payments"})
    assert "not_in_introspected_schema" in str(excinfo.value)


def test_introspected_identifier_is_accepted() -> None:
    assert _validated_identifier("orders", {"orders", "payments"}) == "orders"


# ── handler refusals ────────────────────────────────────────────────────────

def _envelope(**spec: object) -> dict:
    return {
        "observer_id": OBSERVER_ID,
        "experiment": {},
        "observations": {},
        "assertion": {"property": dict(spec)},
        "property": dict(spec),
    }


def test_missing_source_declaration_is_refused_with_a_named_reason() -> None:
    """The observer never infers a table from an entity name.

    An inferred table name is an inferred business fact, which the evidence rules forbid.
    """
    receipt = observe_persistence_state(_envelope())

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PERSISTENCE_TARGET_NOT_SOURCE_DECLARED"
    assert set(receipt["evidence"]["missing"]) == {
        "persistence_root", "project", "persistence_table", "persistence_fields",
    }


def test_production_environment_refusal_is_reported(tmp_path: Path) -> None:
    _write_config(tmp_path, "proj", {"environment": "production",
                                     "services": [_service("order", db=_full_db())]})
    receipt = observe_persistence_state(_envelope(
        persistence_root=str(tmp_path), project="proj",
        persistence_table="orders", persistence_fields=["status"],
    ))

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PERSISTENCE_READ_NOT_PERMITTED"
    assert "production_environment_blocked" in receipt["evidence"]["detail"]


def test_no_declared_source_is_reported_separately_from_a_failed_read(tmp_path: Path) -> None:
    """"No database declared" and "the read failed" are different facts."""
    _write_config(tmp_path, "proj", {"environment": "test", "services": [_service("gateway")]})
    receipt = observe_persistence_state(_envelope(
        persistence_root=str(tmp_path), project="proj",
        persistence_table="orders", persistence_fields=["status"],
    ))

    assert receipt["reason_code"] == "PERSISTENCE_SOURCE_NOT_DECLARED"


def test_unreachable_database_is_not_observed_never_clean(tmp_path: Path) -> None:
    """A connection that cannot be made must not look like a clean reading.

    No driver or server is needed for this: an unroutable host fails, and the point is
    that the refusal is explicit and names every module it attempted.
    """
    _write_config(tmp_path, "proj", {"environment": "test", "services": [
        _service("order", db={"host": "127.0.0.1", "port": 1,
                              "name": "nope", "user": "u", "password": "p"}),
    ]})
    receipt = observe_persistence_state(_envelope(
        persistence_root=str(tmp_path), project="proj",
        persistence_table="orders", persistence_fields=["status"],
    ))

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PERSISTENCE_NOT_OBSERVED"
    assert receipt["evidence"]["attempted_modules"] == ["order"]
    assert receipt["evidence"]["failures"]


def test_invalid_config_refusal_is_distinct(tmp_path: Path) -> None:
    _write_config(tmp_path, "proj", {"environment": "test", "services": [
        _service("order", db={"host": "h", "port": 5432, "name": "", "user": "u"}),
    ]})
    receipt = observe_persistence_state(_envelope(
        persistence_root=str(tmp_path), project="proj",
        persistence_table="orders", persistence_fields=["status"],
    ))

    assert receipt["reason_code"] == "PERSISTENCE_CONFIG_INVALID"


# ── registration ────────────────────────────────────────────────────────────

def test_registration_is_opt_in_and_declares_the_db_sql_adapter() -> None:
    """Registering at import time would let any import open a customer DB connection."""
    assert OBSERVER_ID not in OBSERVER_REGISTRY
    try:
        registered = install_persistence_observer()
        assert registered == OBSERVER_ID
        contract = OBSERVER_REGISTRY[OBSERVER_ID]
        assert contract["adapter"] == ADAPTER == "db_sql"
        assert contract["surface"] == "persistence_state"
        assert contract["implemented"] is True
        assert EVIDENCE_KEY in contract["evidence_keys"]
    finally:
        OBSERVER_REGISTRY.pop(OBSERVER_ID, None)
        ocb._REGISTERED_OBSERVER_HANDLERS.pop(OBSERVER_ID, None)


def test_importing_the_module_registers_nothing() -> None:
    """AGENTS.md requires importing ai_test_asset_center to be side-effect free."""
    assert OBSERVER_ID not in OBSERVER_REGISTRY
    assert OBSERVER_ID not in ocb._REGISTERED_OBSERVER_HANDLERS
    assert po.OBSERVER_ID == OBSERVER_ID

"""The adapter cleanup branch must actually be reached, and must actually delete.

The ladder resolved, the compiler emitted 784 db_sql cleanup steps, the reversibility
gate accepted them, and BLOCKED_NON_REVERSIBLE_WRITE fell from 668 to 151 -- and the
cleanup produced zero receipts, so there was no evidence it ever ran. A compensator that
has never been observed to execute is not a compensator, which is why
``declared_adapter_cleanup`` is still withheld from CLEANUP_AUTHORITIES.

These tests close that gap deterministically. Driving the whole pipeline to reach the
branch depends on an accepted governed write against a live target, which is not a test;
driving ``execute_experiment_cleanup_compensation`` directly is.

What is proven here:
  * the branch is reachable -- a db_sql step in a cleanup plan produces a receipt;
  * it deletes through the guarded executor, with the value bound not interpolated;
  * a row this run did not create is refused before any connection opens;
  * a failure is recorded as a cleanup failure rather than passing silently.
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

from ai_test_asset_center import experiment_cleanup_executor as cleanup_mod
from ai_test_asset_center import experiment_cleanup_executor_core as cleanup_core

# An approved non-production target-policy contract, injected so these tests
# exercise the real ownership/DSN guards without also having to stand up a
# project config on disk just to clear the (separately tested) target-policy
# gate added to ``execute_declared_adapter_cleanup``.
_APPROVED_RUNTIME_CONTRACT = {
    "approved_base_url": "http://qb-test.local",
    "requested_base_url": "http://qb-test.local",
    "environment_ref": "qb-test-env",
    "environment_type": "test",
    "execution_mode": "approved_sandbox_write",
    "status": "approved",
}


def test_the_adapter_branch_precedes_the_http_path_logic() -> None:
    """A db_sql step carries no path or method.

    If the HTTP branch sees it first it records cleanup_compensation_unresolved and the
    row is left behind, which is exactly what happened.
    """
    source = inspect.getsource(cleanup_core.execute_experiment_cleanup_compensation)
    loop_at = source.index("for cleanup_index in range(len(cleanup_plan))")
    adapter_at = source.index('adapter")) == "db_sql"', loop_at)
    # The HTTP fallback INSIDE the same loop, not an earlier unrelated occurrence.
    http_at = source.index("cleanup_compensation_unresolved", adapter_at)
    assert loop_at < adapter_at < http_at


def test_the_branch_is_inside_the_cleanup_plan_loop() -> None:
    """It must run per step, not once per experiment."""
    source = inspect.getsource(cleanup_core.execute_experiment_cleanup_compensation)
    loop_at = source.index("for cleanup_index in range(len(cleanup_plan))")
    adapter_at = source.index('adapter")) == "db_sql"')
    assert loop_at < adapter_at


def test_a_db_step_produces_a_receipt_and_deletes(monkeypatch) -> None:
    """The whole point: reach the branch, run the guarded executor, get a receipt."""
    executed: list[dict] = []

    def _fake_execute(
        step,
        *,
        identity_value,
        dsn="",
        creation_receipts=None,
        connect=None,
        root=None,
        project="",
        runtime_contract=None,
        policy_decision=None,
        **_kwargs,
    ):
        executed.append({"step": dict(step), "identity_value": identity_value, "dsn": dsn})
        return {
            "schema_version": "qualibug.cleanup-adapter-execution.v1",
            "adapter": "db_sql",
            "table": step.get("table"),
            "identity_value": identity_value,
            "status": "CLEANED",
            "reason_code": "",
            "rows_deleted": 1,
        }

    monkeypatch.setattr(
        "ai_test_asset_center.cleanup_adapter_ladder.execute_declared_adapter_cleanup",
        _fake_execute,
    )
    monkeypatch.setattr(
        cleanup_core, "_project_database_dsn", lambda root, project: ("postgresql://x/y", "")
    )

    step = {
        "action": "declared_adapter_cleanup",
        "adapter": "db_sql",
        "mode": "row_delete",
        "table": "products",
        "identity_column": "sku",
        "scope": "run_created_only",
        "requires_ownership_proof": True,
    }
    receipt = cleanup_mod._execute_adapter_cleanup_step(
        step,
        root=".",
        project="p",
        runtime_bindings={"sku": "qb_auto_sku_1"},
        steps_out=[],
        runtime_contract=_APPROVED_RUNTIME_CONTRACT,
    )
    assert receipt["status"] == "CLEANED"
    assert receipt["rows_deleted"] == 1
    assert executed and executed[0]["identity_value"] == "qb_auto_sku_1"
    assert executed[0]["dsn"] == "postgresql://x/y"


def test_the_identity_comes_from_what_the_run_observed() -> None:
    """Prefer a value the compensated write returned over a runtime binding."""
    identity = cleanup_mod._adapter_cleanup_identity(
        {"identity_column": "sku"},
        runtime_bindings={"sku": "from_binding"},
        steps_out=[
            {
                "phase": "treatment",
                "body": {"sku": "from_write_response"},
                "governance_receipt": {
                    "accepted": True,
                    "after": {"body": {"sku": "from_write_response"}},
                },
            }
        ],
    )
    assert identity == "from_write_response"


def test_identity_ignores_fixture_and_domain_field_names() -> None:
    """Never bind cleanup identity from fixture bodies or hardcoded orderId keys."""
    identity = cleanup_mod._adapter_cleanup_identity(
        {"identity_column": "id"},
        runtime_bindings={},
        steps_out=[
            {"phase": "fixture", "body": {"id": "fixture-row", "orderId": "ord-wrong"}},
            {
                "phase": "treatment",
                "body": {"orderId": "ord-from-payment", "status": "PAID"},
                "governance_receipt": {
                    "accepted": True,
                    "after": {"body": {"orderId": "ord-from-payment", "status": "PAID"}},
                },
            },
        ],
    )
    # identity_column=id and no generic id present → unbound (not orderId guess)
    assert identity == ""


def test_identity_ignores_rejected_governed_write_bodies() -> None:
    identity = cleanup_mod._adapter_cleanup_identity(
        {"identity_column": "id"},
        runtime_bindings={},
        steps_out=[
            {
                "phase": "treatment",
                "body": {"id": "qb_auto_rejected"},
                "governance_receipt": {
                    "accepted": False,
                    "after": {"body": {"id": "qb_auto_rejected"}},
                },
            }
        ],
    )

    assert identity == ""


def test_no_observed_identity_yields_nothing_rather_than_a_guess() -> None:
    """The executor then refuses; deleting by guess is the failure this prevents."""
    assert cleanup_mod._adapter_cleanup_identity(
        {"identity_column": "sku"}, runtime_bindings={}, steps_out=[]
    ) == ""


def test_a_customer_row_is_refused_before_any_connection(monkeypatch) -> None:
    """End to end through the executor's own guard, not a stub."""
    monkeypatch.setattr(
        cleanup_mod, "_project_database_dsn", lambda root, project: ("postgresql://x/y", "")
    )
    receipt = cleanup_mod._execute_adapter_cleanup_step(
        {
            "adapter": "db_sql", "table": "orders", "identity_column": "id",
            "scope": "run_created_only", "requires_ownership_proof": True,
        },
        root=".",
        project="p",
        runtime_bindings={"id": "982ab14f-a-real-customer-order"},
        steps_out=[],
        runtime_contract=_APPROVED_RUNTIME_CONTRACT,
    )
    assert receipt["status"] == "REFUSED"
    assert receipt["reason_code"] == "CLEANUP_ROW_NOT_CREATED_BY_THIS_RUN"


def test_an_undeclared_database_is_refused_not_skipped() -> None:
    """No DSN means no cleanup, and that must be a receipt rather than silence."""
    receipt = cleanup_mod._execute_adapter_cleanup_step(
        {
            "adapter": "db_sql", "table": "orders", "identity_column": "id",
            "scope": "run_created_only", "requires_ownership_proof": True,
        },
        root="/nonexistent",
        project="absent",
        runtime_bindings={"id": "qb_auto_1"},
        steps_out=[],
        runtime_contract=_APPROVED_RUNTIME_CONTRACT,
    )
    assert receipt["status"] in ("REFUSED", "FAILED")
    assert receipt["reason_code"] == "CLEANUP_DB_CONNECTION_NOT_CONFIGURED"


def test_a_credential_decrypt_failure_is_not_collapsed_into_not_configured(monkeypatch) -> None:
    """A declared-but-broken credential is a different fault than "not configured"."""
    monkeypatch.setattr(
        cleanup_core,
        "_project_database_dsn",
        lambda root, project: ("", "CREDENTIAL_DECRYPT_FAILED:ValueError"),
    )
    receipt = cleanup_mod._execute_adapter_cleanup_step(
        {
            "adapter": "db_sql", "table": "orders", "identity_column": "id",
            "scope": "run_created_only", "requires_ownership_proof": True,
        },
        root=".",
        project="p",
        runtime_bindings={"id": "qb_auto_1"},
        steps_out=[],
        runtime_contract=_APPROVED_RUNTIME_CONTRACT,
    )
    assert receipt["status"] == "REFUSED"
    assert receipt["reason_code"].startswith("CREDENTIAL_DECRYPT_FAILED")


def test_malformed_cleanup_database_config_is_reported(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("QUALIBUG_DB_DSN", raising=False)
    config = tmp_path / "platform_workspace" / "project" / "multi_service_config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{not-json", encoding="utf-8")

    dsn, error = cleanup_mod._project_database_dsn(tmp_path, "project")

    assert dsn == ""
    assert error.startswith("CLEANUP_DB_CONFIG_INVALID:")


def test_cleanup_key_initialization_failure_is_observable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from ai_test_asset_center import private_pilot_credentials_patch

    monkeypatch.delenv("QUALIBUG_DB_DSN", raising=False)
    key_path = (
        tmp_path
        / "platform_workspace"
        / ".secrets"
        / "credential_encryption.key"
    )
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text("existing-key", encoding="utf-8")
    monkeypatch.setattr(
        private_pilot_credentials_patch,
        "ensure_local_credential_encryption_key",
        lambda _root: (_ for _ in ()).throw(ValueError("invalid key")),
    )

    with caplog.at_level("WARNING"):
        dsn, error = cleanup_mod._project_database_dsn(tmp_path, "project")

    assert dsn == ""
    assert error == ""
    assert "cleanup_credential_key_load_failed" in caplog.text
    assert "ValueError" in caplog.text


def test_a_write_without_target_policy_context_is_refused(monkeypatch) -> None:
    """No policy_decision and no root/project means the gate cannot evaluate -- refuse."""
    monkeypatch.setattr(
        cleanup_mod, "_project_database_dsn", lambda root, project: ("postgresql://x/y", "")
    )
    from ai_test_asset_center.cleanup_adapter_ladder import execute_declared_adapter_cleanup

    receipt = execute_declared_adapter_cleanup(
        {
            "adapter": "db_sql", "table": "orders", "identity_column": "id",
            "requires_ownership_proof": True,
        },
        identity_value="qb_auto_1",
        dsn="postgresql://x/y",
        connect=lambda: (_ for _ in ()).throw(AssertionError("must not connect")),
    )
    assert receipt["status"] == "REFUSED"
    assert receipt["reason_code"] == "CLEANUP_TARGET_POLICY_UNAVAILABLE"


def test_row_delete_rolls_back_when_declared_identity_matches_multiple_rows() -> None:
    from ai_test_asset_center.cleanup_adapter_ladder import (
        execute_declared_adapter_cleanup,
    )

    class _Cursor:
        rowcount = 2

        def execute(self, sql, params):
            return None

    class _Connection:
        committed = False
        rolled_back = False

        def cursor(self):
            return _Cursor()

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            return None

    connection = _Connection()
    receipt = execute_declared_adapter_cleanup(
        {
            "adapter": "db_sql",
            "table": "orders",
            "identity_column": "id",
            "requires_ownership_proof": True,
        },
        identity_value="qb_auto_1",
        dsn="postgresql://x/y",
        connect=lambda: connection,
        policy_decision={"write_allowed": True},
    )

    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "CLEANUP_DB_DELETE_CARDINALITY_MISMATCH"
    assert receipt["rows_deleted"] == 2
    assert connection.rolled_back is True
    assert connection.committed is False


def test_row_delete_surfaces_rollback_failure() -> None:
    from ai_test_asset_center.cleanup_adapter_ladder import (
        execute_declared_adapter_cleanup,
    )

    class _Cursor:
        def execute(self, sql, params):
            raise ValueError("delete failed")

    class _Connection:
        def cursor(self):
            return _Cursor()

        def rollback(self):
            raise RuntimeError("rollback failed")

        def close(self):
            return None

    receipt = execute_declared_adapter_cleanup(
        {
            "adapter": "db_sql",
            "table": "orders",
            "identity_column": "id",
            "requires_ownership_proof": True,
        },
        identity_value="qb_auto_1",
        dsn="postgresql://x/y",
        connect=lambda: _Connection(),
        policy_decision={"write_allowed": True},
    )

    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "CLEANUP_DB_ROLLBACK_FAILED:RuntimeError"
    assert "delete failed" in receipt["detail"]
    assert "rollback failed" in receipt["detail"]


def test_a_failed_adapter_cleanup_counts_as_a_cleanup_failure() -> None:
    """Pinned in source: a db_sql cleanup that RUNS but is not CLEANED must increment
    cleanup_failures (not continue quietly).

    The earlier ``if _scoped_n == 0`` guard is a separate NOT_REQUIRED path: when no
    governed write was accepted there is nothing to roll back, so it is intentionally
    not a failure. Anchor on the adapter-run failure path, not that guard's ``continue``.
    """
    source = inspect.getsource(cleanup_core.execute_experiment_cleanup_compensation)
    start = source.index('_adapter_cleaned = _text(_adapter_receipt.get("status")) == "CLEANED"')
    end = (
        source.index('observations["cleanup_status"] = "failed"', start)
        + len('observations["cleanup_status"] = "failed"')
    )
    block = source[start:end]
    assert "cleanup_failures += 1" in block
    assert 'observations["cleanup_status"] = "failed"' in block


def test_the_dsn_comes_from_the_operators_declared_config() -> None:
    """Never a hardcoded connection; absence yields empty and the executor refuses."""
    assert cleanup_mod._project_database_dsn("/nonexistent", "absent") == ("", "")


def test_dsn_loads_existing_local_encryption_key(tmp_path, monkeypatch) -> None:
    """Cleanup must decrypt with the same local key the credential-save path uses."""
    from ai_test_asset_center.credential_crypto import encrypt

    monkeypatch.delenv("QUALIBUG_CRED_ENC_KEY", raising=False)
    monkeypatch.delenv("QUALIBUG_DB_DSN", raising=False)
    key_dir = tmp_path / "platform_workspace" / ".secrets"
    key_dir.mkdir(parents=True)
    key = "unit-test-cleanup-credential-key-value"
    (key_dir / "credential_encryption.key").write_text(key, encoding="utf-8")
    monkeypatch.setenv("QUALIBUG_CRED_ENC_KEY", key)
    encrypted = encrypt("db-secret-pass")
    monkeypatch.delenv("QUALIBUG_CRED_ENC_KEY", raising=False)

    project = "proj"
    cfg_dir = tmp_path / "platform_workspace" / project
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "multi_service_config.json").write_text(
        json.dumps(
            {
                "services": [
                    {
                        "name": "gateway",
                        "db": {
                            "host": "localhost",
                            "port": 5432,
                            "name": "app",
                            "user": "u",
                            "password": encrypted,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    dsn, err = cleanup_mod._project_database_dsn(tmp_path, project)
    assert err == ""
    assert dsn == "postgresql://u:db-secret-pass@localhost:5432/app"
    assert os.environ.get("QUALIBUG_CRED_ENC_KEY") == key


def test_mutation_restore_fields_from_governed_before_after() -> None:
    """Action POSTs expose status diffs that field-restore must reverse."""
    fields = cleanup_mod._mutation_restore_fields_from_steps(
        [
            {
                "phase": "treatment",
                "governance_receipt": {
                    "accepted": True,
                    "before": {
                        "body": {
                            "id": "ord-1",
                            "status": "SHIPPED",
                            "updated_at": "t0",
                        }
                    },
                    "after": {
                        "body": {
                            "id": "ord-1",
                            "status": "COMPLETED",
                            "updated_at": "t1",
                        }
                    },
                },
            }
        ],
        identity_value="ord-1",
    )
    assert fields == {"status": "SHIPPED"}


def test_mutation_restore_fields_rejects_cross_entity_before_snapshot() -> None:
    fields = cleanup_mod._mutation_restore_fields_from_steps(
        [
            {
                "phase": "treatment",
                "governance_receipt": {
                    "accepted": True,
                    "before": {
                        "body": {"id": "other-order", "status": "SHIPPED"}
                    },
                    "after": {
                        "body": {"id": "target-order", "status": "COMPLETED"}
                    },
                },
            }
        ],
        identity_value="target-order",
    )

    assert fields == {}


def test_mutation_attestation_binds_same_step_as_restore_fields() -> None:
    """Control/treatment arms of the same identity must not cross-wire restore."""
    steps = [
        {
            "phase": "control",
            "governance_receipt": {
                "accepted": True,
                "audit_path": "audit-control",
                "before": {"body": {"id": "ord-1", "status": "SHIPPED", "qty": 1}},
                "after": {"body": {"id": "ord-1", "status": "SHIPPED", "qty": 2}},
            },
        },
        {
            "phase": "treatment",
            "governance_receipt": {
                "accepted": True,
                "audit_path": "audit-treatment",
                "before": {"body": {"id": "ord-1", "status": "SHIPPED", "qty": 1}},
                "after": {"body": {"id": "ord-1", "status": "COMPLETED", "qty": 1}},
            },
        },
    ]
    restore = cleanup_mod._mutation_restore_fields_from_steps(
        steps, identity_value="ord-1", identity_column="id"
    )
    # Newest arm first: treatment mutated status, not qty.
    assert restore == {"status": "SHIPPED"}
    attestation = cleanup_mod._mutation_attestation_from_steps(
        steps,
        identity_value="ord-1",
        identity_column="id",
        restore_fields=restore,
    )
    assert attestation["write_receipt_ref"] == "audit-treatment"
    assert attestation["before_body"]["status"] == "SHIPPED"
    assert attestation["after_body"]["status"] == "COMPLETED"
    assert attestation["restore_fields"] == restore


def test_mutation_restore_fields_require_nonempty_identity() -> None:
    """Unscoped diffs must not invent a field-restore map.

    Empty identity + scalar diffs used to enter field-restore while attestation
    refused, yielding false CLEANUP_MUTATION_NOT_ATTESTED and blocking DELETE.
    """
    fields = cleanup_mod._mutation_restore_fields_from_steps(
        [
            {
                "phase": "treatment",
                "governance_receipt": {
                    "accepted": True,
                    "before": {"body": {"id": "ord-1", "status": "SHIPPED"}},
                    "after": {"body": {"id": "ord-1", "status": "COMPLETED"}},
                },
            }
        ],
        identity_value="",
        identity_column="id",
    )
    assert fields == {}


def test_adapter_cleanup_without_identity_does_not_false_mutation_refuse(
    monkeypatch,
) -> None:
    """Missing cleanup identity must not dead-end as MUTATION_NOT_ATTESTED."""
    monkeypatch.setattr(
        cleanup_mod, "_project_database_dsn", lambda root, project: ("postgresql://x/y", "")
    )
    monkeypatch.setattr(
        "ai_test_asset_center.cleanup_adapter_ladder.execute_declared_adapter_field_restore",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not field-restore")),
    )
    deleted: list[dict] = []

    def _fake_delete(step, *, identity_value, **kwargs):
        deleted.append({"identity_value": identity_value, "table": step.get("table")})
        return {
            "schema_version": "qualibug.cleanup-adapter-execution.v1",
            "adapter": "db_sql",
            "table": step.get("table"),
            "identity_value": identity_value,
            "status": "REFUSED",
            "reason_code": "CLEANUP_ROW_IDENTITY_NOT_RESOLVABLE",
            "rows_deleted": 0,
        }

    monkeypatch.setattr(
        "ai_test_asset_center.cleanup_adapter_ladder.execute_declared_adapter_cleanup",
        _fake_delete,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.cleanup_adapter_ladder.build_ordered_delete_plan",
        lambda **kwargs: [
            {
                "adapter": "db_sql",
                "table": kwargs.get("table") or "orders",
                "identity_column": kwargs.get("identity_column") or "id",
            }
        ],
    )

    receipt = cleanup_mod._execute_adapter_cleanup_step(
        {"adapter": "db_sql", "table": "orders", "identity_column": "id"},
        root=Path("."),
        project="proj",
        runtime_bindings={},
        steps_out=[
            {
                "phase": "treatment",
                "governance_receipt": {
                    "accepted": True,
                    # Scalar diffs without a resolvable primary identity — the
                    # pre-fix path built an unscoped restore map and then
                    # refused attestation as CLEANUP_MUTATION_NOT_ATTESTED.
                    "before": {"body": {"status": "SHIPPED"}},
                    "after": {"body": {"status": "COMPLETED"}},
                },
            }
        ],
        runtime_contract={"status": "approved", "approved_base_url": "http://localhost:8080"},
    )

    assert receipt["reason_code"] != "CLEANUP_MUTATION_NOT_ATTESTED"
    assert deleted, "expected owned-row DELETE path after empty restore map"


def test_adapter_prefers_field_restore_over_row_delete_for_mutations(monkeypatch) -> None:
    """Existing-entity mutations must not attempt run-owned row delete."""
    restored: list[dict] = []

    def _fake_restore(step, *, identity_value, restore_fields, dsn="", **kwargs):
        restored.append(
            {
                "identity_value": identity_value,
                "restore_fields": dict(restore_fields),
                "dsn": dsn,
            }
        )
        return {
            "schema_version": "qualibug.cleanup-adapter-execution.v1",
            "adapter": "db_sql",
            "table": "orders",
            "identity_value": identity_value,
            "status": "CLEANED",
            "reason_code": "",
            "rows_updated": 1,
            "mode": "field_restore",
            "restored_fields": sorted(restore_fields),
        }

    monkeypatch.setattr(
        cleanup_mod, "_project_database_dsn", lambda root, project: ("postgresql://x/y", "")
    )
    monkeypatch.setattr(
        "ai_test_asset_center.cleanup_adapter_ladder.execute_declared_adapter_field_restore",
        _fake_restore,
    )
    monkeypatch.setattr(
        "ai_test_asset_center.cleanup_adapter_ladder.execute_declared_adapter_cleanup",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not row-delete")),
    )

    receipt = cleanup_mod._execute_adapter_cleanup_step(
        {
            "adapter": "db_sql",
            "table": "orders",
            "identity_column": "id",
            "requires_ownership_proof": True,
        },
        root=".",
        project="p",
        runtime_bindings={},
        steps_out=[
            {
                "phase": "treatment",
                "body": {"id": "ord-1", "status": "COMPLETED"},
                "governance_receipt": {
                    "accepted": True,
                    "audit_path": "audit-ord-1-treatment",
                    "before": {"body": {"id": "ord-1", "status": "SHIPPED"}},
                    "after": {"body": {"id": "ord-1", "status": "COMPLETED"}},
                },
            }
        ],
        runtime_contract=_APPROVED_RUNTIME_CONTRACT,
    )
    assert receipt["status"] == "CLEANED"
    assert receipt["mode"] == "field_restore"
    assert restored and restored[0]["restore_fields"] == {"status": "SHIPPED"}


def test_field_restore_updates_observed_scalars(monkeypatch) -> None:
    """Field restore issues UPDATE, not DELETE, under the same target-policy gate."""
    from ai_test_asset_center.cleanup_adapter_ladder import (
        execute_declared_adapter_field_restore,
    )

    executed: list[tuple] = []

    class _Cursor:
        rowcount = 1

        def execute(self, sql, params):
            executed.append((sql, params))

    class _Conn:
        def cursor(self):
            return _Cursor()

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

    attestation = {
        "identity_value": "ord-1",
        "accepted_write": True,
        "write_receipt_ref": "audit-write-1",
        "before_body": {"id": "ord-1", "status": "SHIPPED"},
        "after_body": {"id": "ord-1", "status": "COMPLETED"},
        "restore_fields": {"status": "SHIPPED"},
    }
    receipt = execute_declared_adapter_field_restore(
        {"adapter": "db_sql", "table": "orders", "identity_column": "id"},
        identity_value="ord-1",
        restore_fields={"status": "SHIPPED"},
        dsn="postgresql://x/y",
        connect=lambda: _Conn(),
        policy_decision={"write_allowed": True},
        mutation_attestation=attestation,
    )
    assert receipt["status"] == "CLEANED"
    assert receipt["receipt_id"].startswith("cleanup_adapter_")
    assert receipt["rows_updated"] == 1
    assert receipt["rows_deleted"] == 0
    assert executed
    sql, params = executed[0]
    assert "UPDATE" in sql.upper()
    assert "DELETE" not in sql.upper()
    assert params == ["SHIPPED", "ord-1"]


def test_field_restore_refuses_without_mutation_attestation() -> None:
    """UPDATE must never run from unbound identity + restore map alone."""
    from ai_test_asset_center.cleanup_adapter_ladder import (
        REASON_MUTATION_NOT_ATTESTED,
        execute_declared_adapter_field_restore,
    )

    receipt = execute_declared_adapter_field_restore(
        {"adapter": "db_sql", "table": "orders", "identity_column": "id"},
        identity_value="ord-1",
        restore_fields={"status": "SHIPPED"},
        dsn="postgresql://x/y",
        connect=lambda: (_ for _ in ()).throw(AssertionError("must not connect")),
        policy_decision={"write_allowed": True},
    )
    assert receipt["status"] == "REFUSED"
    assert receipt["reason_code"] == REASON_MUTATION_NOT_ATTESTED
    assert receipt["rows_updated"] == 0


def test_field_restore_rolls_back_when_identity_updates_multiple_rows() -> None:
    from ai_test_asset_center.cleanup_adapter_ladder import (
        execute_declared_adapter_field_restore,
    )

    class _Cursor:
        rowcount = 2

        def execute(self, sql, params):
            return None

    class _Conn:
        committed = False
        rolled_back = False

        def cursor(self):
            return _Cursor()

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            return None

    connection = _Conn()
    receipt = execute_declared_adapter_field_restore(
        {"adapter": "db_sql", "table": "orders", "identity_column": "id"},
        identity_value="ord-1",
        restore_fields={"status": "SHIPPED"},
        dsn="postgresql://x/y",
        connect=lambda: connection,
        policy_decision={"write_allowed": True},
        mutation_attestation={
            "identity_value": "ord-1",
            "accepted_write": True,
            "write_receipt_ref": "audit-write-1",
            "before_body": {"id": "ord-1", "status": "SHIPPED"},
            "after_body": {"id": "ord-1", "status": "COMPLETED"},
            "restore_fields": {"status": "SHIPPED"},
        },
    )

    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "CLEANUP_DB_RESTORE_CARDINALITY_MISMATCH"
    assert receipt["rows_updated"] == 2
    assert connection.rolled_back is True
    assert connection.committed is False

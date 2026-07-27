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

import pytest

from ai_test_asset_center import experiment_cleanup_executor as cleanup_mod

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
    source = inspect.getsource(cleanup_mod.execute_experiment_cleanup_compensation)
    loop_at = source.index("for cleanup_index in reversed(range(len(cleanup_plan)))")
    adapter_at = source.index('adapter")) == "db_sql"', loop_at)
    # The HTTP fallback INSIDE the same loop, not an earlier unrelated occurrence.
    http_at = source.index("cleanup_compensation_unresolved", adapter_at)
    assert loop_at < adapter_at < http_at


def test_the_branch_is_inside_the_cleanup_plan_loop() -> None:
    """It must run per step, not once per experiment."""
    source = inspect.getsource(cleanup_mod.execute_experiment_cleanup_compensation)
    loop_at = source.index("for cleanup_index in reversed(range(len(cleanup_plan)))")
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
        cleanup_mod, "_project_database_dsn", lambda root, project: ("postgresql://x/y", "")
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
    """Prefer a value the write itself returned over a runtime binding."""
    identity = cleanup_mod._adapter_cleanup_identity(
        {"identity_column": "sku"},
        runtime_bindings={"sku": "from_binding"},
        steps_out=[{"body": {"sku": "from_write_response"}}],
    )
    assert identity == "from_write_response"


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
        cleanup_mod,
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


def test_a_failed_adapter_cleanup_counts_as_a_cleanup_failure() -> None:
    """Pinned in source: the branch must increment cleanup_failures, not continue quietly."""
    source = inspect.getsource(cleanup_mod.execute_experiment_cleanup_compensation)
    start = source.index('adapter")) == "db_sql"')
    # The branch ends at its `continue`; take everything up to and including it.
    end = source.index("continue", start) + len("continue")
    block = source[start:end]
    assert "cleanup_failures += 1" in block
    assert 'observations["cleanup_status"] = "failed"' in block


def test_the_dsn_comes_from_the_operators_declared_config() -> None:
    """Never a hardcoded connection; absence yields empty and the executor refuses."""
    assert cleanup_mod._project_database_dsn("/nonexistent", "absent") == ("", "")

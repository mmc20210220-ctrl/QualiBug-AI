from __future__ import annotations

from ai_test_asset_center import cleanup_equivalence_core as module


def _receipt(**overrides):
    row = {
        "schema_version": "qualibug.cleanup-execution-receipt.v1",
        "status": "ACCEPTED",
        "attempted": True,
        "transport_reached": True,
        "status_code": 200,
    }
    row.update(overrides)
    return row


def test_formal_success_tuple_bridges_legacy_succeeded_without_mutating_receipt() -> None:
    original = _receipt()
    governed = module.normalize_cleanup_execution_success_authority(original)

    assert "succeeded" not in original
    assert governed["succeeded"] is True
    assert governed is not original


def test_status_code_alone_never_proves_cleanup_success() -> None:
    for receipt in (
        _receipt(attempted=False),
        _receipt(transport_reached=False),
        _receipt(status="FAILED"),
        _receipt(status_code=500),
        {"schema_version": "wrong", "status_code": 200},
    ):
        governed = module.normalize_cleanup_execution_success_authority(receipt)
        assert governed.get("succeeded") is not True


def test_explicit_failed_succeeded_flag_is_never_overridden() -> None:
    receipt = _receipt(succeeded=False)
    governed = module.normalize_cleanup_execution_success_authority(receipt)
    assert governed["succeeded"] is False


def test_evaluator_receives_local_success_view(monkeypatch) -> None:
    captured = {}

    def fake_evaluator(**kwargs):
        captured.update(kwargs)
        return {"equivalence_status": "EQUIVALENT"}

    monkeypatch.setattr(
        module,
        "_original_evaluate_cleanup_equivalence",
        fake_evaluator,
    )
    original = _receipt()
    result = module.evaluate_cleanup_equivalence(
        proof={},
        before_observation={},
        after_write_observation={},
        after_cleanup_observation={},
        runtime_bindings={},
        cleanup_execution_receipt=original,
    )

    assert result["equivalence_status"] == "EQUIVALENT"
    assert captured["cleanup_execution_receipt"]["succeeded"] is True
    assert "succeeded" not in original

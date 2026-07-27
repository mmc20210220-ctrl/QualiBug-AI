"""Splice repaired Finalizer V1.6.2-R1 block into experiment_outcome_finalizer.py."""
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "ai_test_asset_center" / "experiment_outcome_finalizer.py"

NEW = r'''    # ── V1.5.0 diagnostic formula (not authority for TRUE_COMPLETED) ──
    _v150_true_completion: dict[str, Any] = {}
    _process_ledger = observations.get("process_step_ledger")
    # V1.6.2-R1: hydrate step id sets from live ledger when observation keys empty.
    # Required ids stay compile/plan authority — never forged from executed responses.
    _ledger_id = _text(
        observations.get("process_step_ledger_id")
        or getattr(_process_ledger, "ledger_id", "")
    )
    _ledger_hash = _text(
        observations.get("process_step_ledger_hash")
        or (
            _process_ledger.compute_hash()
            if _process_ledger is not None and hasattr(_process_ledger, "compute_hash")
            else ""
        )
    )
    if _process_ledger is not None and hasattr(_process_ledger, "executed_step_ids"):
        if not _list(observations.get("executed_step_ids")):
            observations["executed_step_ids"] = list(_process_ledger.executed_step_ids())
        _req = list(getattr(_process_ledger, "required_step_ids", []) or [])
        if not _list(observations.get("required_step_ids")):
            observations["required_step_ids"] = list(_req)
        if not _list(observations.get("planned_step_ids")):
            observations["planned_step_ids"] = list(
                _list(observations.get("required_step_ids")) or _req
            )
        live_id = _text(getattr(_process_ledger, "ledger_id", ""))
        if not _ledger_id and live_id:
            _ledger_id = live_id
            observations["process_step_ledger_id"] = _ledger_id
        if hasattr(_process_ledger, "compute_hash"):
            live_hash = _text(_process_ledger.compute_hash())
            if _ledger_hash and live_hash and _ledger_hash != live_hash:
                observations["finalizer_block_reason"] = (
                    "PROCESS_STEP_LEDGER_HASH_MISMATCH"
                )
            elif not _ledger_hash and live_hash:
                _ledger_hash = live_hash
                observations["process_step_ledger_hash"] = _ledger_hash
        obs_id = _text(observations.get("process_step_ledger_id"))
        if obs_id and live_id and obs_id != live_id:
            observations["finalizer_block_reason"] = (
                "PROCESS_STEP_LEDGER_IDENTITY_MISMATCH"
            )
    _planned_steps = _list(observations.get("planned_step_ids"))
    _executed_steps = _list(observations.get("executed_step_ids"))
    _required_steps = _list(observations.get("required_step_ids"))
    _evidence_receipt = _dict(observations.get("per_step_evidence_completeness"))
    if (
        not _evidence_receipt
        and _process_ledger is not None
        and hasattr(_process_ledger, "executed_step_ids")
        and (_required_steps or _executed_steps)
    ):
        from .process_step_execution import (
            evaluate_per_step_evidence_completeness as _eval_step_evidence,
        )
        _evidence_receipt = _eval_step_evidence(
            planned_step_ids=list(_required_steps or _planned_steps),
            ledger=_process_ledger,
            observed_step_ids=list(_executed_steps),
        )
        observations["per_step_evidence_completeness"] = _evidence_receipt
    _oracle_evaluated = bool(verdict and verdict.get("verdict"))
    _cleanup_executed_ok = cleanup_failures == 0 and bool(steps_out)
    _cleanup_ver = cleanup_gate not in ("FAILED", "BLOCKED")
    if _process_ledger is not None:
        from .process_step_execution import evaluate_true_completed as _eval_true_completed
        _v150_true_completion = _eval_true_completed(
            fixture_materialized=bool(fixture_receipts),
            state_precondition_established=observations.get(
                "state_precondition_established", True
            ),
            all_required_steps_executed=(
                bool(_required_steps)
                and set(_required_steps) <= set(_executed_steps)
            ),
            per_step_evidence_complete=bool(_evidence_receipt.get("complete", False)),
            minimal_oracle_evaluated=_oracle_evaluated,
            cleanup_executed=_cleanup_executed_ok,
            cleanup_verified=_cleanup_ver,
            environment_restored=bool(_env_restored),
        )
        # Diagnostic terminal only — TRUE_COMPLETED requires receipt bundle below.
        if (
            _v150_true_completion.get("terminal_state")
            and _v150_true_completion.get("terminal_state") != "TRUE_COMPLETED"
        ):
            _lifecycle_state = _v150_true_completion["terminal_state"]

    # ── V1.6.2 §8: TRUE_COMPLETED only from Execution Receipt Bundle ──
    # Only this Finalizer may derive TRUE_COMPLETED, and only via finalization
    # receipt from a validated receipt bundle (never direct status assignment).
    _execution_receipt_bundle: dict[str, Any] = {}
    _finalization_receipt: dict[str, Any] = {}
    _finalizer_block_reason = _text(observations.get("finalizer_block_reason"))
    if _process_ledger is None and not observations.get("force_receipt_bundle"):
        from .process_step_execution import (
            FORMAL_MAINLINE_PROCESS_STEP_LEDGER_NOT_PROPAGATED as _LEDGER_NOT_PROP,
        )
        _finalizer_block_reason = _LEDGER_NOT_PROP
        observations["finalizer_block_reason"] = _LEDGER_NOT_PROP
    elif _process_ledger is not None or observations.get("force_receipt_bundle"):
        from .operational_receipts import (
            NOT_APPLICABLE as _NA,
            assemble_bundle_from_finalizer_observations as _assemble_bundle,
            build_execution_finalization_receipt as _build_finalization,
        )
        from .process_step_execution import (
            FINALIZER_PROCESS_STEP_LEDGER_MISSING as _LEDGER_MISSING,
            FINALIZER_RECEIPT_BUNDLE_NOT_ACTIVATED as _BUNDLE_NOT_ACTIVATED,
            PROCESS_STEP_LEDGER_HASH_MISMATCH as _HASH_MISMATCH,
            PROCESS_STEP_LEDGER_IDENTITY_MISMATCH as _ID_MISMATCH,
            PROCESS_STEP_REQUIRED_SET_MISMATCH as _STEP_MISMATCH,
            attach_ledger_refs_to_observations as _attach_ledger_refs,
            step_ids_with_cleanup_evidence as _steps_cleanup,
            step_ids_with_observation_evidence as _steps_observed,
            step_ids_with_oracle_evidence as _steps_oracle,
            validate_required_actual_step_balance as _step_balance,
        )

        if _finalizer_block_reason in {_HASH_MISMATCH, _ID_MISMATCH}:
            observations["finalizer_block_reason"] = _finalizer_block_reason
        elif not _ledger_id and not observations.get("force_receipt_bundle"):
            # SPEC §12: missing process_step_ledger_id must not skip to COMPLETED.
            _finalizer_block_reason = _LEDGER_MISSING
            observations["finalizer_block_reason"] = _LEDGER_MISSING
        else:
            _protocol_id = _text(
                exp.get("protocol_id")
                or _dict(exp.get("protocol")).get("protocol_id")
                or observations.get("protocol_id")
                or _NA
            )
            _fixture_id = _text(
                observations.get("fixture_id")
                or (_dict(fixture_receipts[0]).get("fixture_id") if fixture_receipts else "")
                or _NA
            )
            _run_id = _text(
                resolved_execution_id or observations.get("run_id") or _NA
            )
            _commit = _text(observations.get("code_commit_sha") or _NA)
            _tree = _text(observations.get("tree_hash") or _NA)

            def _id_receipt(rid: str, kind: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
                body = {"receipt_id": rid, "kind": kind}
                if extra:
                    body.update(extra)
                return body

            # Compile receipt: real materials only — never synthesize from executed steps.
            _compile_raw = _dict(observations.get("compile_receipt") or exp.get("compile_receipt"))

            _fixture_prov = list(_list(observations.get("fixture_provenance_receipts")))
            if not _fixture_prov and fixture_receipts:
                for idx, fr in enumerate(fixture_receipts):
                    frd = _dict(fr)
                    fr_rid = _text(frd.get("receipt_id"))
                    if not fr_rid:
                        continue
                    _fixture_prov.append(
                        _id_receipt(fr_rid, "fixture_provenance", frd)
                    )

            # Process-step receipts from live ledger rows (never forge missing steps).
            _step_receipts = list(_list(observations.get("process_step_receipts")))
            if not _step_receipts and _process_ledger is not None and hasattr(
                _process_ledger, "all_rows"
            ):
                for row in list(_process_ledger.all_rows()):
                    if isinstance(row, dict) and _text(row.get("step_id")):
                        _step_receipts.append(dict(row))

            _transport_receipts = list(_list(observations.get("transport_receipts")))
            if not _transport_receipts:
                for idx, step in enumerate(steps_out):
                    sd = _dict(step)
                    gov = _dict(sd.get("governance_receipt"))
                    gov_rid = _text(gov.get("receipt_id"))
                    if gov_rid:
                        _transport_receipts.append(
                            _id_receipt(
                                gov_rid,
                                "transport",
                                {"step_index": idx, "status_code": sd.get("status_code")},
                            )
                        )
            for _tid in _list(observations.get("transport_receipt_ids")):
                if _text(_tid) and not any(
                    _text(_dict(r).get("receipt_id")) == _text(_tid)
                    for r in _transport_receipts
                ):
                    _transport_receipts.append(_id_receipt(_text(_tid), "transport"))

            _obs_receipts = [
                dict(r) for r in observer_receipts if isinstance(r, dict)
            ] or list(_list(observations.get("observation_receipts")))
            if not _obs_receipts and _evidence_receipt and _text(
                _evidence_receipt.get("receipt_id")
            ):
                _obs_receipts.append(
                    _id_receipt(
                        _text(_evidence_receipt.get("receipt_id")),
                        "observation",
                        _evidence_receipt,
                    )
                )

            _oracle_inv = list(_list(observations.get("oracle_invocation_receipts")))
            _oracle_rid = _text(verdict.get("receipt_id")) if isinstance(verdict, dict) else ""
            if not _oracle_inv and _oracle_evaluated and _oracle_rid:
                _oracle_inv.append(
                    _id_receipt(
                        _oracle_rid,
                        "oracle_invocation",
                        {"verdict": _dict(verdict)},
                    )
                )
            _oracle_tr = list(_list(observations.get("oracle_trace_receipts")))
            if not _oracle_tr and _list(observations.get("oracle_trace")):
                for idx, tr in enumerate(_list(observations.get("oracle_trace"))):
                    trd = _dict(tr)
                    tr_rid = _text(trd.get("receipt_id"))
                    if not tr_rid:
                        continue
                    _oracle_tr.append(_id_receipt(tr_rid, "oracle_trace", trd))

            _cleanup_exec_receipts = list(_list(observations.get("cleanup_execution_receipts")))
            if not _cleanup_exec_receipts:
                _singular_cleanup = _dict(observations.get("cleanup_execution_receipt"))
                if _singular_cleanup and _text(_singular_cleanup.get("receipt_id")):
                    _cleanup_exec_receipts = [_singular_cleanup]
            _cleanup_ver_receipts = list(
                _list(observations.get("cleanup_verification_receipts"))
            )
            if not _cleanup_ver_receipts:
                _singular_ver = _dict(observations.get("cleanup_verification"))
                if _singular_ver and _text(
                    _singular_ver.get("receipt_id") or _singular_ver.get("verification_id")
                ):
                    _cleanup_ver_receipts = [_singular_ver]
            if not _cleanup_ver_receipts and cleanup_equivalence_receipt:
                _ver_rid = _text(cleanup_equivalence_receipt.get("receipt_id"))
                if _ver_rid:
                    _cleanup_ver_receipts = [
                        _id_receipt(
                            _ver_rid,
                            "cleanup_verification",
                            dict(cleanup_equivalence_receipt),
                        )
                    ]

            _env_receipt = _dict(observations.get("environment_restoration_receipt"))
            if _env_receipt and _text(_env_receipt.get("receipt_id")):
                observations["restoration_receipt_id"] = _text(_env_receipt.get("receipt_id"))
            elif not _env_receipt:
                _env_receipt = {}

            # Bind real oracle/cleanup receipt ids onto ledger steps before balance.
            if _process_ledger is not None and hasattr(_process_ledger, "append_receipt_ref"):
                if _oracle_rid:
                    for _sid in _executed_steps:
                        _process_ledger.append_receipt_ref(
                            _text(_sid), "oracle_receipt_ids", _oracle_rid
                        )
                _cleanup_bind_ids = [
                    _text(_dict(r).get("receipt_id"))
                    for r in _cleanup_exec_receipts
                    if _text(_dict(r).get("receipt_id"))
                ]
                if _cleanup_bind_ids and _cleanup_ver:
                    _write_steps = (
                        list(_process_ledger.successful_write_step_ids())
                        if hasattr(_process_ledger, "successful_write_step_ids")
                        else list(_executed_steps)
                    )
                    _targets = _write_steps or list(_executed_steps)
                    for _sid in _targets:
                        for _crid in _cleanup_bind_ids:
                            _process_ledger.append_receipt_ref(
                                _text(_sid), "cleanup_receipt_ids", _crid
                            )
                _attach_ledger_refs(observations, _process_ledger)
                _ledger_hash = _text(observations.get("process_step_ledger_hash"))

            _observed_steps = (
                _steps_observed(_process_ledger)
                if _process_ledger is not None and hasattr(_process_ledger, "all_rows")
                else []
            )
            _oracle_steps = (
                _steps_oracle(_process_ledger)
                if _process_ledger is not None and hasattr(_process_ledger, "all_rows")
                else []
            )
            _cleanup_steps = (
                _steps_cleanup(_process_ledger)
                if (
                    _process_ledger is not None
                    and hasattr(_process_ledger, "all_rows")
                    and _cleanup_exec_receipts
                    and _cleanup_ver
                )
                else []
            )
            _balance = _step_balance(
                required_step_ids=list(_required_steps),
                executed_step_ids=list(_executed_steps),
                observed_step_ids=list(_observed_steps),
                oracle_step_ids=list(_oracle_steps),
                cleanup_step_ids=list(_cleanup_steps),
            )
            observations["process_step_balance"] = _balance

            # Attempt bundle/finalization whenever ledger id + structural materials
            # exist. Cleanup/env flags are passed honestly into derivation so
            # missing cleanup yields CLEANUP_FAILED / ENVIRONMENT_DIRTY rather than
            # skipping Finalization Receipt entirely (SPEC §15).
            _attempt_finalization = bool(
                observations.get("force_receipt_bundle")
                or (
                    bool(_ledger_id)
                    and bool(_required_steps)
                    and bool(_executed_steps)
                    and bool(fixture_receipts or _fixture_prov)
                    and bool(_compile_raw)
                    and _oracle_evaluated
                    and not _finalizer_block_reason
                )
            )
            # TRUE_COMPLETED seek still requires cleanup + restoration + balance.
            _seek_true_completed = bool(
                _attempt_finalization
                and _balance.get("balanced", False)
                and (
                    observations.get("force_receipt_bundle")
                    or (
                        _v150_true_completion.get("true_completed")
                        or (
                            _cleanup_ver
                            and bool(_env_restored)
                            and bool(_env_receipt)
                        )
                    )
                )
            )
            if _attempt_finalization and not _balance.get("balanced", False):
                _finalizer_block_reason = _text(_balance.get("reason_code")) or _STEP_MISMATCH
                observations["finalizer_block_reason"] = _finalizer_block_reason
                _seek_true_completed = False
            if _attempt_finalization:
                _execution_receipt_bundle = _assemble_bundle(
                    bundle_id=f"erb_{eid}",
                    campaign_id=_text(resolved_campaign_id or campaign_id or _NA),
                    run_id=_run_id,
                    obligation_id=_text(oid or _NA),
                    experiment_id=_text(eid or _NA),
                    fixture_id=_fixture_id,
                    protocol_id=_protocol_id,
                    code_commit_sha=_commit,
                    tree_hash=_tree,
                    compile_receipt=_compile_raw or None,
                    fixture_provenance_receipts=_fixture_prov,
                    process_step_receipts=_step_receipts,
                    transport_receipts=_transport_receipts,
                    observation_receipts=_obs_receipts,
                    oracle_invocation_receipts=_oracle_inv,
                    oracle_trace_receipts=_oracle_tr,
                    cleanup_execution_receipts=_cleanup_exec_receipts,
                    cleanup_verification_receipts=_cleanup_ver_receipts,
                    environment_restoration_receipt=_env_receipt or None,
                )
                _finalization_receipt = _build_finalization(
                    finalization_receipt_id=f"final_{eid}",
                    bundle=_execution_receipt_bundle,
                    oracle_evaluated=_oracle_evaluated,
                    cleanup_verified=_cleanup_ver,
                    environment_restored=bool(_env_restored),
                    code_commit_sha=_commit,
                    tree_hash=_tree,
                )
                _derived = _text(
                    _finalization_receipt.get("derived_terminal_status")
                    or _finalization_receipt.get("lifecycle_state")
                )
                if _derived:
                    _lifecycle_state = _derived
                if (
                    _derived == "TRUE_COMPLETED"
                    and not (
                        _seek_true_completed
                        and _cleanup_ver
                        and bool(_env_restored)
                        and _balance.get("balanced", False)
                    )
                ):
                    # Fail closed: never accept TRUE_COMPLETED without cleanup/env/balance.
                    _lifecycle_state = "RECEIPT_INCOMPLETE"
                    _finalization_receipt = dict(_finalization_receipt)
                    _finalization_receipt["derived_terminal_status"] = "RECEIPT_INCOMPLETE"
                    _finalization_receipt["true_completed"] = False
                    _finalization_receipt["lifecycle_state"] = "RECEIPT_INCOMPLETE"
            elif not _finalizer_block_reason:
                _finalizer_block_reason = _BUNDLE_NOT_ACTIVATED
                observations["finalizer_block_reason"] = _BUNDLE_NOT_ACTIVATED
'''

def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    start = text.index("    # ── V1.5.0 diagnostic formula (not authority for TRUE_COMPLETED) ──")
    end = text.index("    # Environment restoration is a hard gate for EXPERIMENT_COMPLETED.")
    PATH.write_text(text[:start] + NEW + text[end:], encoding="utf-8")
    print("patched", PATH)


if __name__ == "__main__":
    main()

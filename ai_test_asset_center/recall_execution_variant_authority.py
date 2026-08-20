"""Exact execution-face identity authority for coverage-unit input variants.

Coverage-unit planning selects one semantic unit while the validation compiler may
expand its representative into several independently executable ``__v_`` input
variants.  Actor-arm derivation can then create several compiled experiments for
one actor obligation.  The execution boundary, however, is intentionally keyed by
one selected obligation id -> one compiled experiment.  Reusing the same selected
id for several execution faces either trips ``compiled_experiment_mismatch`` or
silently shadows all but one face in ``experiments_by_obligation``.

This module preserves the optimization (budget still counts coverage units) while
making every already-compiled execution face explicit at the execution boundary:

* duplicate selected rows for one actor obligation receive deterministic alias ids;
* compiler-expanded input variants that belong to a selected unit are selected too;
* aliases point at the original frozen compiled experiment -- no compile receipt,
  experiment payload, Oracle, evidence or delivery gate is rewritten;
* the attempt ledger preserves a variant receipt when that exact variant id was
  independently selected, and only folds legacy *unselected* variants to a selected
  base compatibility face.

The executor already records selected and executed obligation identities separately,
so an alias never invents a new compiled contract; it only prevents a lossy dict key
collision before transport.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from typing import Any, Mapping


_INSTALL_MARKER = "_qualibug_exact_execution_variant_authority_installed"
_LEDGER_PROJECTION_MARKER = (
    "_qualibug_exact_execution_variant_stage_projection_installed"
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _compile_status(experiment: Any) -> str:
    row = _dict(experiment)
    return _text(_dict(row.get("compile_receipt")).get("status")).upper()


def _is_derived_arm(experiment: Any) -> bool:
    row = _dict(experiment)
    return bool(
        _dict(row.get("compile_receipt")).get("arm_derived")
        or _text(row.get("arm_of"))
    )


def _source_experiment_belongs_to_base(
    experiment: dict[str, Any],
    base_obligation_id: str,
) -> bool:
    """Whether a non-arm compiled experiment is one input face of ``base``."""

    if _is_derived_arm(experiment):
        return False
    oid = _text(experiment.get("obligation_id"))
    expanded_from = _text(experiment.get("expanded_from_obligation_id"))
    return bool(
        oid == base_obligation_id
        or expanded_from == base_obligation_id
        or oid.startswith(f"{base_obligation_id}__v_")
    )


def _alias_obligation_id(
    base_obligation_id: str,
    *,
    experiment_id: str,
    occupied: Mapping[str, Any],
) -> str:
    """Return a deterministic variant-shaped alias not already claimed."""

    for salt in range(32):
        material = json.dumps(
            [base_obligation_id, experiment_id, salt],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
        candidate = f"{base_obligation_id}__v_{digest}"
        existing = _dict(occupied.get(candidate))
        if not existing or _text(existing.get("experiment_id")) == experiment_id:
            return candidate
    raise ValueError(f"execution_variant_alias_space_exhausted:{base_obligation_id}")


def _row_for_execution_face(
    template: dict[str, Any],
    *,
    obligation_id: str,
    experiment: dict[str, Any],
    coverage_unit_id: str,
    origin: str,
    base_obligation_id: str,
) -> dict[str, Any]:
    row = dict(_dict(template))
    row.update(
        {
            "obligation_id": obligation_id,
            "experiment_id": _text(experiment.get("experiment_id")),
            "coverage_unit_id": coverage_unit_id,
            "execution_face_origin": origin,
            "execution_face_base_obligation_id": base_obligation_id,
        }
    )
    if not _text(row.get("risk_family")):
        row["risk_family"] = _text(experiment.get("risk_family"))
    return row


def repair_selected_execution_variant_identities(
    *,
    obligation_plan: dict[str, Any],
    units: list[dict[str, Any]],
    experiment_pack: dict[str, Any],
    by_obligation: dict[str, dict[str, Any]],
    receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make every selected execution face one-to-one with a compiled experiment.

    The function is deliberately post-derivation: it consumes only artifacts the
    existing compiler/arm authority already produced.  It never creates an
    experiment and never changes an experiment payload.  Extra selected rows are
    therefore execution aliases over source-attested compiled variants, not new
    semantic obligations.
    """

    result_receipt = dict(_dict(receipt))
    selected = [dict(_dict(row)) for row in _list(obligation_plan.get("selected"))]
    experiments = [
        row
        for row in _list(experiment_pack.get("experiments"))
        if isinstance(row, dict) and _compile_status(row) == "COMPILED"
    ]
    experiment_by_id = {
        _text(row.get("experiment_id")): row
        for row in experiments
        if _text(row.get("experiment_id"))
    }

    # 1) Repair already-selected actor-arm collisions.  Keep the face currently
    # registered under the base id for compatibility; alias every other face.
    counts = Counter(_text(row.get("obligation_id")) for row in selected)
    repaired_selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    selected_experiment_ids: set[str] = set()
    alias_count = 0

    for row in selected:
        oid = _text(row.get("obligation_id"))
        eid = _text(row.get("experiment_id"))
        if not oid or not eid:
            repaired_selected.append(row)
            continue
        target_oid = oid
        if counts.get(oid, 0) > 1:
            compatibility_eid = _text(_dict(by_obligation.get(oid)).get("experiment_id"))
            compatibility_free = oid not in selected_ids
            if not (compatibility_free and eid == compatibility_eid):
                target_oid = _alias_obligation_id(
                    oid,
                    experiment_id=eid,
                    occupied=by_obligation,
                )
                alias_count += 1
        experiment = experiment_by_id.get(eid) or _dict(by_obligation.get(oid))
        if target_oid != oid:
            if not experiment or _text(experiment.get("experiment_id")) != eid:
                raise ValueError(f"execution_variant_experiment_missing:{oid}:{eid}")
            by_obligation[target_oid] = experiment
            row = _row_for_execution_face(
                row,
                obligation_id=target_oid,
                experiment=experiment,
                coverage_unit_id=_text(row.get("coverage_unit_id")),
                origin="actor_input_variant_alias",
                base_obligation_id=oid,
            )
        if target_oid in selected_ids:
            raise ValueError(f"selected_execution_variant_identity_duplicate:{target_oid}")
        selected_ids.add(target_oid)
        selected_experiment_ids.add(eid)
        repaired_selected.append(row)

    # 2) Compiler input variants of every source obligation in a selected unit
    # are executable faces too.  The coverage-unit budget still counted once;
    # this only expands the already-selected unit into the experiments the
    # compiler actually produced.  The compatibility face already represented
    # by a base selected row is skipped by experiment_id, preventing duplicate
    # transport of the same experiment.
    unit_by_id = {
        _text(unit.get("coverage_unit_id")): unit
        for unit in units
        if isinstance(unit, dict) and _text(unit.get("coverage_unit_id"))
    }
    selected_units = [
        _dict(row) for row in _list(obligation_plan.get("selected_units"))
        if isinstance(row, dict)
    ]
    added_input_variants = 0

    for selected_unit in selected_units:
        unit_id = _text(selected_unit.get("coverage_unit_id"))
        unit = _dict(unit_by_id.get(unit_id))
        if not unit:
            continue
        base_ids = [
            _text(value)
            for value in _list(unit.get("obligation_ids"))
            if _text(value)
        ]
        for base_oid in base_ids:
            template = next(
                (
                    row
                    for row in repaired_selected
                    if _text(row.get("obligation_id")) == base_oid
                    or _text(row.get("execution_face_base_obligation_id")) == base_oid
                ),
                {},
            )
            for experiment in experiments:
                if not _source_experiment_belongs_to_base(experiment, base_oid):
                    continue
                eid = _text(experiment.get("experiment_id"))
                if not eid or eid in selected_experiment_ids:
                    continue
                exact_oid = _text(experiment.get("obligation_id"))
                if not exact_oid:
                    continue
                target_oid = exact_oid
                existing = _dict(by_obligation.get(target_oid))
                if (
                    target_oid in selected_ids
                    or (
                        existing
                        and _text(existing.get("experiment_id"))
                        and _text(existing.get("experiment_id")) != eid
                    )
                ):
                    target_oid = _alias_obligation_id(
                        base_oid,
                        experiment_id=eid,
                        occupied=by_obligation,
                    )
                by_obligation[target_oid] = experiment
                repaired_selected.append(
                    _row_for_execution_face(
                        template,
                        obligation_id=target_oid,
                        experiment=experiment,
                        coverage_unit_id=unit_id,
                        origin="compiled_input_variant",
                        base_obligation_id=base_oid,
                    )
                )
                selected_ids.add(target_oid)
                selected_experiment_ids.add(eid)
                added_input_variants += 1

    # Stable execution ordering: preserve existing planner/arm order, append
    # newly exposed compiler variants deterministically by the order already
    # present in experiment_pack.  Never re-rank a selected semantic unit.
    obligation_plan["selected"] = repaired_selected
    obligation_plan["selected_execution_face_count"] = len(repaired_selected)
    result_receipt.update(
        {
            "execution_variant_identity_authority": "exact_selected_execution_face",
            "execution_variant_alias_count": alias_count,
            "compiled_input_variants_selected": added_input_variants,
            "selected_execution_face_count": len(repaired_selected),
        }
    )
    return result_receipt


def collapse_variant_receipts_preserving_selected(
    by_id: Mapping[str, Any],
    *,
    selected_ids: set[str],
) -> dict[str, dict[str, Any]]:
    """Legacy base folding, except an explicitly selected variant stays exact.

    More than one unselected variant for a selected base is no longer resolved by
    insertion order.  That state has no one-to-one terminal attempt authority and
    therefore fails closed instead of silently discarding evidence.
    """

    from . import _obligation_attempt_ledger_single_occurrence_mechanics as _core

    collapsed: dict[str, dict[str, Any]] = {}
    legacy_variants: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for raw_oid, raw_receipt in by_id.items():
        oid = _text(raw_oid)
        receipt = dict(_dict(raw_receipt))
        base = _core._base_obligation_id(oid)
        if oid in selected_ids or base == oid:
            collapsed[oid] = receipt
        elif base in selected_ids:
            legacy_variants.setdefault(base, []).append((oid, receipt))
        else:
            collapsed[oid] = receipt

    for base, rows in legacy_variants.items():
        if base in collapsed:
            # A real base-keyed receipt is already authoritative.  Preserve the
            # historical compatibility behavior for its unselected aliases.
            continue
        if len(rows) != 1:
            ids = ",".join(sorted(oid for oid, _ in rows))
            raise _core.ObligationAttemptLedgerError(
                f"multiple_unselected_variant_receipts:{base}:{ids}"
            )
        collapsed[base] = dict(rows[0][1])
    return collapsed


def _install_public_ledger_variant_projection_guard() -> None:
    """Keep exact selected faces intact before the public ledger binds stages.

    The multi-occurrence ledger facade has a compatibility projection that maps a
    legacy compiler-local variant receipt onto its selected base.  Exact execution
    authority now also selects sibling variant ids independently.  Those exact
    selected ids must be removed from the compatibility projection and restored
    byte-for-byte afterward, otherwise the facade can choose one variant by dict
    order and pop the other independently selected faces before core validation.
    """

    from . import _obligation_attempt_ledger_single_occurrence_mechanics as _core
    from . import obligation_attempt_ledger as _ledger

    if getattr(_ledger, _LEDGER_PROJECTION_MARKER, False):
        return

    original_projection = _ledger._project_variant_stage_receipts

    def _project_preserving_exact_selected(
        *,
        selected: list[dict[str, Any]],
        compile_results: Mapping[str, Any],
        execution_results: Mapping[str, Any],
        gate_results: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        selected_ids = {
            _text(row.get("obligation_id"))
            for row in selected
            if isinstance(row, dict) and _text(row.get("obligation_id"))
        }
        selected_variant_ids = {
            oid for oid in selected_ids
            if _core._base_obligation_id(oid) != oid
        }

        original_maps = (
            dict(compile_results) if isinstance(compile_results, Mapping) else compile_results,
            dict(execution_results) if isinstance(execution_results, Mapping) else execution_results,
            dict(gate_results) if isinstance(gate_results, Mapping) else gate_results,
        )
        if not all(isinstance(mapping, dict) for mapping in original_maps):
            return original_projection(
                selected=selected,
                compile_results=compile_results,
                execution_results=execution_results,
                gate_results=gate_results,
            )

        compile_in, execution_in, gate_in = (
            dict(original_maps[0]),
            dict(original_maps[1]),
            dict(original_maps[2]),
        )

        exact_stage_rows: tuple[dict[str, Any], dict[str, Any], dict[str, Any]] = (
            {oid: compile_in.pop(oid) for oid in selected_variant_ids if oid in compile_in},
            {oid: execution_in.pop(oid) for oid in selected_variant_ids if oid in execution_in},
            {oid: gate_in.pop(oid) for oid in selected_variant_ids if oid in gate_in},
        )

        # With exact selected variants removed, only legacy compatibility faces
        # remain eligible to project onto a selected base.  More than one such
        # execution face is ambiguous: fail closed rather than reintroducing
        # insertion-order first-wins semantics.
        legacy_execution_variants: dict[str, list[str]] = {}
        for raw_oid, raw_receipt in execution_in.items():
            oid = _text(raw_oid)
            base = _core._base_obligation_id(oid)
            if (
                not oid
                or base == oid
                or base not in selected_ids
                or not isinstance(raw_receipt, dict)
            ):
                continue
            direct = execution_in.get(base)
            if (
                isinstance(direct, dict)
                and direct
                and not _ledger._mechanical_execution_gap(direct)
            ):
                continue
            legacy_execution_variants.setdefault(base, []).append(oid)

        for base, variant_ids in legacy_execution_variants.items():
            if len(variant_ids) > 1:
                ids = ",".join(sorted(variant_ids))
                raise _core.ObligationAttemptLedgerError(
                    f"multiple_unselected_variant_receipts:{base}:{ids}"
                )

        projected = original_projection(
            selected=selected,
            compile_results=compile_in,
            execution_results=execution_in,
            gate_results=gate_in,
        )
        output = [dict(mapping) for mapping in projected]
        for index, exact_rows in enumerate(exact_stage_rows):
            output[index].update(exact_rows)
        return output[0], output[1], output[2]

    _ledger._project_variant_stage_receipts = _project_preserving_exact_selected
    setattr(_ledger, _LEDGER_PROJECTION_MARKER, True)


def install_exact_execution_variant_authority() -> None:
    """Install planning and ledger guards on the single product mainline."""

    from . import discovery_runtime_planning as _planning
    from . import _obligation_attempt_ledger_single_occurrence_mechanics as _ledger_core

    if not getattr(_planning, _INSTALL_MARKER, False):
        original_derive = _planning.derive_unit_execution_arms

        def _derive_with_exact_execution_faces(*args: Any, **kwargs: Any) -> dict[str, Any]:
            receipt = original_derive(*args, **kwargs)
            return repair_selected_execution_variant_identities(
                obligation_plan=kwargs["obligation_plan"],
                units=kwargs["units"],
                experiment_pack=kwargs["experiment_pack"],
                by_obligation=kwargs["by_obligation"],
                receipt=receipt,
            )

        _planning.derive_unit_execution_arms = _derive_with_exact_execution_faces
        _ledger_core._collapse_variant_receipts = collapse_variant_receipts_preserving_selected
        setattr(_planning, _INSTALL_MARKER, True)

    # The public multi-occurrence ledger owns an additional compatibility
    # projection outside the patched core.  Guard it independently so a later
    # facade import cannot erase exact selected variant identities.
    _install_public_ledger_variant_projection_guard()

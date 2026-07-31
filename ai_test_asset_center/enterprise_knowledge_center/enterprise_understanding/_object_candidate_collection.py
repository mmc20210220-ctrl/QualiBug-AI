"""Collect business-object candidates without resolving cross-source identity."""
from __future__ import annotations

from typing import Any, Iterable

from .identity_edges import identity_evidence_class
from .identity_types import HARD_IDENTITY_CLASSES, asset_evidence
from ._object_role_evidence import accepted_facts, comparison_key, positive_fact_mentions
from .schema import (
    as_list,
    dedupe_evidence,
    evidence_from_fact,
    is_source_backed_evidence,
    stable_id,
    text,
    unique_text,
)

_DERIVED_MARKERS = frozenset(
    {
        "industry_inference",
        "semantic_inference",
        "heuristic_inference",
        "llm_candidate",
        "derived_candidate",
        "inferred",
    }
)


def _source_backed(rows: Iterable[dict[str, Any]]) -> bool:
    return any(is_source_backed_evidence(row) for row in rows if isinstance(row, dict))


def _derived_object_asset(row: dict[str, Any]) -> bool:
    markers = {
        text(row.get(field)).casefold()
        for field in (
            "source",
            "source_kind",
            "source_type",
            "derivation",
            "recognition_status",
            "status",
        )
        if text(row.get(field))
    }
    return bool(markers & _DERIVED_MARKERS) or any(
        marker.startswith("industry_") or marker.endswith("_candidate")
        for marker in markers
    )


def collect_object_candidates(asset: dict[str, Any]) -> dict[str, Any]:
    candidates: dict[str, dict[str, Any]] = {}
    alias_edges: list[dict[str, str]] = []

    def ensure(label: Any) -> dict[str, Any] | None:
        raw = text(label)
        key = comparison_key(raw)
        if not key:
            return None
        row = candidates.setdefault(
            key,
            {
                "candidate_id": stable_id("business_object_candidate", key),
                "comparison_key": key,
                "labels": [],
                "positive_roles": [],
                "technical_roles": [],
                "evidence": [],
                "source_ids": [],
                "explicit_object_authority": False,
                "source_backed_business_authority": False,
                "derived_only": False,
            },
        )
        row["labels"] = unique_text([*as_list(row.get("labels")), raw])
        return row

    def add_business(
        label: Any,
        role: str,
        evidence: Iterable[dict[str, Any]] = (),
        *,
        explicit: bool = False,
        source_backed: bool = False,
        derived: bool = False,
    ) -> None:
        row = ensure(label)
        if row is None:
            return
        evidence_rows = dedupe_evidence(evidence)
        row["positive_roles"] = unique_text([*as_list(row.get("positive_roles")), role])
        row["evidence"] = dedupe_evidence([*as_list(row.get("evidence")), *evidence_rows])
        row["source_ids"] = unique_text(
            [*as_list(row.get("source_ids")), *[e.get("source_id") for e in evidence_rows]]
        )
        row["explicit_object_authority"] = bool(row.get("explicit_object_authority") or explicit)
        row["source_backed_business_authority"] = bool(
            row.get("source_backed_business_authority") or source_backed
        )
        row["derived_only"] = bool(
            derived
            and not row.get("explicit_object_authority")
            and not row.get("source_backed_business_authority")
        )

    def add_technical(label: Any, role: str) -> None:
        row = ensure(label)
        if row is not None:
            row["technical_roles"] = unique_text([*as_list(row.get("technical_roles")), role])

    for index, raw in enumerate(as_list(asset.get("business_objects"))):
        if not isinstance(raw, dict):
            continue
        label = raw.get("object") or raw.get("name")
        ref = text(raw.get("object_id")) or f"business_objects[{index}]"
        evidence = asset_evidence(raw, ref, "existing_business_object")
        derived = _derived_object_asset(raw)
        add_business(
            label,
            "DERIVED_OBJECT_ASSET" if derived else "EXPLICIT_OBJECT_ASSET",
            evidence,
            explicit=not derived,
            source_backed=not derived,
            derived=derived,
        )
        for alias in as_list(raw.get("aliases")):
            add_business(
                alias,
                "DECLARED_OBJECT_ALIAS",
                evidence,
                explicit=not derived,
                source_backed=not derived,
                derived=derived,
            )

    for fact in accepted_facts(asset):
        evidence = evidence_from_fact(fact)
        source_backed = _source_backed(evidence)
        kind = text(fact.get("kind"))
        if kind in {"RULE", "STATE_TRANSITION"}:
            for label, role in positive_fact_mentions(fact):
                add_business(label, role, evidence, source_backed=source_backed)
        elif kind == "TERM_ALIAS":
            canonical, alias = fact.get("canonical_term"), fact.get("alias")
            add_business(canonical, "TERM_ALIAS_ENDPOINT", evidence)
            add_business(alias, "TERM_ALIAS_ENDPOINT", evidence)
            if (
                text(canonical)
                and text(alias)
                and source_backed
                and identity_evidence_class(fact) in HARD_IDENTITY_CLASSES
            ):
                alias_edges.append(
                    {
                        "left": comparison_key(canonical),
                        "right": comparison_key(alias),
                        "fact_id": text(fact.get("fact_id")),
                    }
                )

    for index, machine in enumerate(as_list(asset.get("state_machines"))):
        if not isinstance(machine, dict):
            continue
        label = machine.get("object_ref") or machine.get("business_object") or machine.get("entity")
        if not text(label):
            continue
        ref = text(machine.get("state_machine_id")) or f"state_machines[{index}]"
        evidence = asset_evidence(machine, ref, "source_backed_state_machine_object")
        add_business(label, "LIFECYCLE_OBJECT", evidence, source_backed=_source_backed(evidence))

    for row in as_list(asset.get("data_tables")):
        if isinstance(row, dict):
            add_technical(row.get("name") or row.get("table"), "DATA_TABLE")
    for row in as_list(asset.get("field_dictionary")):
        if isinstance(row, dict):
            add_technical(row.get("table"), "FIELD_DICTIONARY_PARENT")

    return {"candidates": candidates, "alias_edges": alias_edges}


__all__ = ["collect_object_candidates"]

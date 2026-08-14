"""Collect business-object candidates without resolving cross-source identity."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

from .identity_edges import identity_evidence_class
from .identity_types import HARD_IDENTITY_CLASSES, asset_evidence
from ._object_role_evidence import (
    accepted_facts,
    comparison_key,
    fact_can_seed_object_type,
    object_slot_rejection_reason,
    positive_fact_mentions,
)
from .schema import (
    as_list,
    dedupe_evidence,
    evidence_from_fact,
    is_source_backed_evidence,
    source_evidence,
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
_TECHNICAL_OBJECT_SOURCES = frozenset(
    {
        "database_schema",
        "data_table",
        "field_dictionary",
        "technical_projection",
    }
)
_SOURCE_ENTITY_DERIVATIONS = frozenset({"entity_inventory_table"})
_DESCRIPTION_DELIMITER_RE = re.compile(r"[，,；;。]", re.UNICODE)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_CJK_SURFACE_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9_.-]+$")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


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
    evidence_rows = [value for value in as_list(row.get("evidence")) if isinstance(value, dict)]
    vocabulary_projection = bool(as_list(row.get("industries"))) or any(
        text(value.get("matched_term")) and text(value.get("reference"))
        for value in evidence_rows
    )
    return vocabulary_projection or bool(markers & _DERIVED_MARKERS) or any(
        marker.startswith("industry_") or marker.endswith("_candidate")
        for marker in markers
    )


def _technical_object_asset(row: dict[str, Any]) -> bool:
    source = text(
        row.get("source") or row.get("source_kind") or row.get("source_type")
    ).casefold()
    return source in _TECHNICAL_OBJECT_SOURCES


def _entity_inventory_row(row: dict[str, Any]) -> bool:
    derivations = {
        text(row.get("derivation")),
        *[text(value) for value in as_list(row.get("derivations"))],
    }
    return bool({value for value in derivations if value} & _SOURCE_ENTITY_DERIVATIONS)


def _entity_inventory_labels(row: dict[str, Any]) -> list[str]:
    """Return exact declaration labels from an entity-inventory row.

    The inventory table format declares the first column as entity identity and
    the second as its human description.  A qualifier after punctuation is not
    part of the display label (e.g. ``工单，核心业务实体`` -> ``工单``).  This is
    format normalization only; no vocabulary lookup or semantic similarity is
    used.
    """

    labels = [row.get("name") or row.get("table"), *as_list(row.get("aliases"))]
    description = text(row.get("description"))
    if description:
        display = _DESCRIPTION_DELIMITER_RE.split(description, maxsplit=1)[0].strip()
        if display:
            labels.append(display)
    return unique_text(labels)


def _proper_surface_forms(label: Any) -> list[str]:
    """Return formatting-exact proper suffixes of a declared object label.

    This is not identity evidence.  It only enumerates candidate source spellings
    that may later be admitted when the exact spelling appears in an independent
    source span and maps to exactly one declared object label.  Chinese enterprise
    materials commonly omit a namespace qualifier (``工单附件`` -> ``附件``); for
    token-delimited labels the same contract applies to complete trailing tokens.
    """

    raw = text(label)
    if not raw:
        return []
    values: list[str] = []
    if _CJK_RE.search(raw):
        for start in range(1, max(1, len(raw) - 1)):
            candidate = raw[start:]
            if (
                len(_CJK_RE.findall(candidate)) >= 2
                and _CJK_SURFACE_RE.fullmatch(candidate)
            ):
                values.append(candidate)
    else:
        tokens = list(_TOKEN_RE.finditer(raw))
        for index in range(1, len(tokens)):
            candidate = raw[tokens[index].start() :].strip()
            if len(candidate) >= 3:
                values.append(candidate)
    return unique_text(values)


def _source_text_units(
    asset: dict[str, Any], facts: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Collect exact source prose already admitted by existing authorities."""

    units: list[dict[str, Any]] = []
    for fact in facts:
        evidence = evidence_from_fact(fact)
        for row in evidence:
            quote = text(row.get("quote"))
            if quote:
                units.append({"text": quote, "evidence": [row]})

    for interface in as_list(asset.get("interfaces")):
        if not isinstance(interface, dict):
            continue
        interface_id = text(
            interface.get("interface_id") or interface.get("operation_id")
        )
        source_id = text(interface.get("source_id"))
        source_locator = text(interface.get("source_locator")) or interface_id
        if not source_id or not source_locator:
            continue
        prose_rows: list[tuple[str, str]] = []
        summary = text(
            interface.get("openapi_summary")
            if "openapi_summary" in interface
            else interface.get("summary")
        )
        description = text(
            interface.get("openapi_description")
            if "openapi_description" in interface
            else interface.get("description")
        )
        if summary:
            prose_rows.append(("OPENAPI_OPERATION_SUMMARY", summary))
        if description and description != summary:
            prose_rows.append(("OPENAPI_OPERATION_DESCRIPTION", description))
        for response in as_list(interface.get("response_contracts")):
            if not isinstance(response, dict):
                continue
            response_description = text(response.get("description"))
            if response_description:
                prose_rows.append(("OPENAPI_RESPONSE_DESCRIPTION", response_description))

        for derivation, quote in prose_rows:
            units.append(
                {
                    "text": quote,
                    "evidence": [
                        source_evidence(
                            source_id=source_id,
                            source_locator=source_locator,
                            quote=quote,
                            quote_hash=hashlib.sha256(
                                quote.encode("utf-8")
                            ).hexdigest(),
                            asset_ref=interface_id,
                            derivation=derivation,
                        )
                    ],
                }
            )

    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for unit in units:
        evidence = as_list(unit.get("evidence"))
        first = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
        key = (
            text(first.get("source_id")),
            text(first.get("source_locator")),
            text(unit.get("text")),
        )
        by_key.setdefault(key, unit)
    return list(by_key.values())


def _source_attested_surface_rows(
    declared_labels: dict[str, str], source_units: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Find conservative object-type surface forms without creating aliases.

    A surface is admitted only when all of the following hold:
    - it is a proper lexical suffix of an explicitly declared object label;
    - that suffix belongs to exactly one declared label in the current project;
    - the exact suffix occurs in a separate source span where the full declaration
      does not occur; and
    - among attested suffixes for that declaration, only the longest survives.

    The result proves the *type* of the source mention.  It deliberately does not
    authorize identity union with the longer declaration.
    """

    parents_by_surface: dict[str, set[str]] = {}
    label_by_surface: dict[str, str] = {}
    for parent_key, label in declared_labels.items():
        for surface in _proper_surface_forms(label):
            surface_key = comparison_key(surface)
            if not surface_key or surface_key in declared_labels:
                continue
            parents_by_surface.setdefault(surface_key, set()).add(parent_key)
            label_by_surface.setdefault(surface_key, surface)

    unique_parent = {
        surface_key: next(iter(parent_keys))
        for surface_key, parent_keys in parents_by_surface.items()
        if len(parent_keys) == 1
    }
    matched: dict[str, list[dict[str, Any]]] = {}
    for unit in source_units:
        source_text = text(unit.get("text"))
        source_key = comparison_key(source_text)
        if not source_key:
            continue
        matches_by_parent: dict[str, list[str]] = {}
        for surface_key, parent_key in unique_parent.items():
            parent_label = declared_labels.get(parent_key, "")
            parent_label_key = comparison_key(parent_label)
            if not parent_label_key or parent_label_key in source_key:
                continue
            if surface_key in source_key:
                matches_by_parent.setdefault(parent_key, []).append(surface_key)
        for parent_key, surface_keys in matches_by_parent.items():
            longest = max(surface_keys, key=lambda value: (len(value), value))
            matched.setdefault(parent_key, []).append(
                {
                    "surface_key": longest,
                    "label": label_by_surface[longest],
                    "parent_key": parent_key,
                    "parent_label": declared_labels[parent_key],
                    "evidence": dedupe_evidence(as_list(unit.get("evidence"))),
                }
            )

    rows: list[dict[str, Any]] = []
    for parent_key, candidates in matched.items():
        maximum = max(
            len(text(row.get("surface_key"))) for row in candidates
        )
        longest_rows = [
            row
            for row in candidates
            if len(text(row.get("surface_key"))) == maximum
        ]
        by_surface: dict[str, dict[str, Any]] = {}
        for row in longest_rows:
            surface_key = text(row.get("surface_key"))
            prior = by_surface.get(surface_key)
            if prior is None:
                by_surface[surface_key] = dict(row)
                continue
            prior["evidence"] = dedupe_evidence(
                [*as_list(prior.get("evidence")), *as_list(row.get("evidence"))]
            )
        rows.extend(by_surface.values())
    return sorted(
        rows,
        key=lambda row: (
            text(row.get("parent_key")),
            text(row.get("surface_key")),
        ),
    )


def collect_object_candidates(asset: dict[str, Any]) -> dict[str, Any]:
    facts = accepted_facts(asset)
    candidates: dict[str, dict[str, Any]] = {}
    alias_edges: list[dict[str, Any]] = []
    ignored_inputs: list[dict[str, Any]] = []
    rejected_fact_mentions: list[dict[str, Any]] = []
    declared_object_keys: set[str] = set()
    declared_object_labels: dict[str, str] = {}

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
                "identity_resolution_eligible": False,
                "requires_identity_review": False,
                "source_surface_origin": False,
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
        declares_type: bool = False,
        identity_resolution_eligible: bool = True,
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
        if identity_resolution_eligible and not row.get("source_surface_origin"):
            row["identity_resolution_eligible"] = True
        if explicit:
            row["identity_resolution_eligible"] = True
            row["requires_identity_review"] = False
        if declares_type:
            declared_object_keys.add(row["comparison_key"])
            declared_object_labels.setdefault(row["comparison_key"], text(label))

    def add_technical(label: Any, role: str) -> None:
        row = ensure(label)
        if row is not None:
            row["technical_roles"] = unique_text([*as_list(row.get("technical_roles")), role])

    def add_alias_edge(
        left: Any,
        right: Any,
        evidence: Iterable[dict[str, Any]],
        *,
        authority: str,
        fact_id: str = "",
    ) -> None:
        left_label, right_label = text(left), text(right)
        left_key, right_key = comparison_key(left_label), comparison_key(right_label)
        if not left_key or not right_key or left_key == right_key:
            return
        alias_edges.append(
            {
                "edge_id": stable_id(
                    "business_object_alias_edge", authority, fact_id, left_key, right_key
                ),
                "left": left_key,
                "right": right_key,
                "left_label": left_label,
                "right_label": right_label,
                "fact_id": fact_id,
                "authority": authority,
                "evidence": dedupe_evidence(evidence),
            }
        )

    # Existing business-object assets are inputs, not self-validating authority.
    # Bare/operator-provided rows remain explicit; vocabulary-generated and
    # technical projections are classified separately and cannot self-promote.
    for index, raw in enumerate(as_list(asset.get("business_objects"))):
        if not isinstance(raw, dict):
            continue
        label = raw.get("object") or raw.get("name")
        ref = text(raw.get("object_id")) or f"business_objects[{index}]"
        if _derived_object_asset(raw):
            ignored_inputs.append(
                {
                    "input_ref": ref,
                    "label": text(label),
                    "reason_code": "DERIVED_OBJECT_ASSET_IS_NOT_TYPE_AUTHORITY",
                    "source": text(raw.get("source") or raw.get("derivation")),
                }
            )
            continue
        if _technical_object_asset(raw):
            add_technical(label, "TECHNICAL_OBJECT_ASSET")
            for alias in as_list(raw.get("aliases")):
                add_technical(alias, "TECHNICAL_OBJECT_ALIAS")
            continue
        evidence = asset_evidence(raw, ref, "explicit_business_object_asset")
        add_business(
            label,
            "EXPLICIT_OBJECT_ASSET",
            evidence,
            explicit=True,
            source_backed=_source_backed(evidence),
            declares_type=True,
        )
        for alias in as_list(raw.get("aliases")):
            add_business(
                alias,
                "DECLARED_OBJECT_ALIAS",
                evidence,
                explicit=True,
                source_backed=_source_backed(evidence),
                declares_type=True,
            )
            add_alias_edge(
                label,
                alias,
                evidence,
                authority="EXPLICIT_OBJECT_ASSET_ALIAS",
            )

    # An entity-inventory table is an explicit source declaration, unlike a
    # generic database table.  Reuse the existing parser's derivation receipt.
    for row in as_list(asset.get("data_tables")):
        if not isinstance(row, dict):
            continue
        name = row.get("name") or row.get("table")
        add_technical(name, "DATA_TABLE")
        if not _entity_inventory_row(row):
            continue
        ref = text(row.get("table_id")) or f"data_tables[{text(name)}]"
        evidence = asset_evidence(row, ref, "source_entity_inventory")
        labels = _entity_inventory_labels(row)
        if not labels:
            continue
        canonical = text(name) or labels[0]
        for label in labels:
            add_business(
                label,
                "SOURCE_ENTITY_INVENTORY",
                evidence,
                explicit=True,
                source_backed=_source_backed(evidence),
                declares_type=True,
            )
            if comparison_key(label) != comparison_key(canonical):
                add_alias_edge(
                    canonical,
                    label,
                    evidence,
                    authority="SOURCE_ENTITY_INVENTORY_LABEL",
                )

    for row in as_list(asset.get("field_dictionary")):
        if isinstance(row, dict):
            add_technical(row.get("table"), "FIELD_DICTIONARY_PARENT")

    # Lifecycle ownership is a source-backed object declaration and must be
    # known before rule-slot quality is judged.
    for index, machine in enumerate(as_list(asset.get("state_machines"))):
        if not isinstance(machine, dict):
            continue
        label = machine.get("object_ref") or machine.get("business_object") or machine.get("entity")
        if not text(label):
            continue
        ref = text(machine.get("state_machine_id")) or f"state_machines[{index}]"
        evidence = asset_evidence(machine, ref, "source_backed_state_machine_object")
        add_business(
            label,
            "LIFECYCLE_OBJECT",
            evidence,
            source_backed=_source_backed(evidence),
            declares_type=True,
        )

    # Source prose may use a shorter surface of an explicitly declared compound
    # object.  Admit that exact surface as an object *type* only when it uniquely
    # belongs to one declaration.  Do not create an alias edge: identity remains
    # under the existing source-backed identity / operator-review authority.
    direct_facts = [fact for fact in facts if fact_can_seed_object_type(fact)]
    source_surface_rows = _source_attested_surface_rows(
        declared_object_labels,
        _source_text_units(asset, direct_facts),
    )
    for surface in source_surface_rows:
        row = ensure(surface.get("label"))
        if row is None:
            continue
        row["surface_parent_keys"] = unique_text(
            [*as_list(row.get("surface_parent_keys")), surface.get("parent_key")]
        )
        row["surface_parent_labels"] = unique_text(
            [*as_list(row.get("surface_parent_labels")), surface.get("parent_label")]
        )
        row["source_surface_origin"] = True
        row["identity_resolution_eligible"] = False
        row["automatic_identity_union_allowed"] = False
        row["requires_identity_review"] = True
        add_business(
            surface.get("label"),
            "SOURCE_ATTESTED_OBJECT_SURFACE",
            as_list(surface.get("evidence")),
            source_backed=True,
            declares_type=True,
            identity_resolution_eligible=False,
        )

    for fact in facts:
        evidence = evidence_from_fact(fact)
        source_backed = _source_backed(evidence)
        kind = text(fact.get("kind"))
        if kind in {"RULE", "STATE_TRANSITION"}:
            for label, role in positive_fact_mentions(fact):
                reason = object_slot_rejection_reason(
                    fact, label, declared_object_labels
                )
                if reason:
                    rejected_fact_mentions.append(
                        {
                            "fact_id": text(fact.get("fact_id")),
                            "label": text(label),
                            "comparison_key": comparison_key(label),
                            "role": role,
                            "reason_code": reason,
                            "evidence": evidence,
                        }
                    )
                    continue
                add_business(
                    label,
                    role,
                    evidence,
                    source_backed=source_backed,
                    declares_type=True,
                )
        elif kind == "TERM_ALIAS" and identity_evidence_class(fact) in HARD_IDENTITY_CLASSES:
            canonical, alias = fact.get("canonical_term"), fact.get("alias")
            add_business(canonical, "TERM_ALIAS_ENDPOINT", evidence)
            add_business(alias, "TERM_ALIAS_ENDPOINT", evidence)
            if text(canonical) and text(alias) and source_backed:
                add_alias_edge(
                    canonical,
                    alias,
                    evidence,
                    authority="SOURCE_DECLARED_HARD_ALIAS",
                    fact_id=text(fact.get("fact_id")),
                )

    rejected_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rejected_fact_mentions:
        rejection_key = (
            text(row.get("fact_id")),
            text(row.get("comparison_key")),
            text(row.get("reason_code")),
        )
        prior = rejected_by_key.get(rejection_key)
        if prior is None:
            prior = dict(row)
            prior["roles"] = unique_text([row.get("role")])
            prior.pop("role", None)
            rejected_by_key[rejection_key] = prior
            continue
        prior["roles"] = unique_text(
            [*as_list(prior.get("roles")), row.get("role")]
        )
        prior["evidence"] = dedupe_evidence(
            [*as_list(prior.get("evidence")), *as_list(row.get("evidence"))]
        )

    return {
        "candidates": candidates,
        "alias_edges": list(
            {text(row.get("edge_id")): row for row in alias_edges}.values()
        ),
        "ignored_inputs": ignored_inputs,
        "rejected_fact_mentions": list(rejected_by_key.values()),
        "declared_object_keys": sorted(declared_object_keys),
        "source_attested_surface_rows": source_surface_rows,
    }


__all__ = ["collect_object_candidates"]

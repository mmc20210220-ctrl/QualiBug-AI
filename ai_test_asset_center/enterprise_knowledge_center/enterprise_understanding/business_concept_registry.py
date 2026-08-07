"""Chinese business concept registry — explicit-evidence concept layer.

SPEC: QUALIBUG-CHINESE-SEMANTIC-ROOT-FIX-V1 (P0-D: 业务概念层, §11).

Contract:
- The concept layer normalizes mentions into business concepts using ONLY
  explicit source evidence: declared aliases/labels from the identity
  registry, understanding-model names/aliases, permission-matrix roles,
  data-table business labels and field-dictionary descriptions. A synonym
  merge requires a declared equivalence (same canonical identity or an
  exactly equal declared label) — similarity never merges.
- `concept_lookup(kind, mention)` returns the canonical concept plus its
  declared aliases; a mention that matches several distinct canonical
  concepts is AMBIGUOUS, a mention with no declared match is UNKNOWN.
  Containment-style near-matches are reported as candidate-only evidence and
  never upgrade the status.
- The registry is a pure projection of existing asset data (built fresh per
  call, deterministic, no industry vocabulary added).
"""

from __future__ import annotations

from typing import Any, Iterable

from .chinese_semantic_receipts import build_receipt

CHINESE_BUSINESS_CONCEPT_REGISTRY_SCHEMA = "qualibug.chinese-business-concept-registry.v1"

CONCEPT_KINDS = frozenset({"actor", "object", "field"})


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return " ".join(_text(value).split()).strip()


def build_business_concept_registry(asset: dict[str, Any]) -> dict[str, Any]:
    """Build the explicit-evidence concept registry from existing asset data.

    A declared label maps to the HIGHEST-priority canonical identity
    (understanding model > identity registry > permission matrix > data
    tables). Several distinct canonicals at the same priority for one label
    make the label AMBIGUOUS; lower-priority sources never shadow a stronger
    declared identity.
    """
    # (priority, kind, label, canonical, evidence, source_kind)
    entries: list[tuple[int, str, str, str, str, str]] = []
    model = _dict(asset.get("enterprise_understanding_model"))
    for actor in _list(model.get("actors")):
        if not isinstance(actor, dict):
            continue
        canonical = _text(actor.get("actor_id") or actor.get("name"))
        for field in ("name", "role_key", "role"):
            _entry(entries, 0, "actor", actor.get(field), canonical,
                   "understanding_model_actor", "understanding_model")
        for alias in _list(actor.get("aliases")):
            _entry(entries, 0, "actor", alias, canonical,
                   "understanding_model_actor_alias", "understanding_model")
    for obj in _list(model.get("business_objects")):
        if not isinstance(obj, dict):
            continue
        canonical = _text(obj.get("object_id") or obj.get("name"))
        for field in ("name", "object", "canonical_label"):
            _entry(entries, 0, "object", obj.get(field), canonical,
                   "understanding_model_object", "understanding_model")
        for alias in _list(obj.get("aliases")):
            _entry(entries, 0, "object", alias, canonical,
                   "understanding_model_object_alias", "understanding_model")

    registry = _dict(asset.get("enterprise_identity_registry"))
    for entity in _list(registry.get("entities")):
        if not isinstance(entity, dict):
            continue
        # The stable identity is the entity_id; the canonical_label is a label.
        canonical = _text(entity.get("entity_id")) or _text(entity.get("canonical_label"))
        entity_type = _text(entity.get("entity_type"))
        kind = "actor" if entity_type == "actor" else "object"
        _entry(entries, 1, kind, entity.get("canonical_label"), canonical,
               "identity_registry_canonical_label", "identity_registry")
        for alias in _list(entity.get("aliases")):
            _entry(entries, 1, kind, alias, canonical,
                   "identity_registry_alias", "identity_registry")
        for label in _list(entity.get("labels")):
            _entry(entries, 1, kind, label, canonical,
                   "identity_registry_label", "identity_registry")

    for row in _list(asset.get("permission_matrix") or asset.get("permissions")):
        if not isinstance(row, dict):
            continue
        role = _norm(row.get("role") or row.get("actor") or row.get("principal"))
        if role:
            _entry(entries, 2, "actor", role, role,
                   "permission_matrix_role", "permission_matrix")

    for table in _list(asset.get("data_tables")):
        if not isinstance(table, dict):
            continue
        canonical = _text(table.get("table_id") or table.get("name"))
        for field in ("name", "business_label", "logical_name"):
            _entry(entries, 3, "object", table.get(field), canonical,
                   "data_table_declared_label", "data_table")
        for field in ("description", "comment", "summary"):
            _entry(entries, 3, "object", table.get(field), canonical,
                   "data_table_description", "data_table")

    for row in _list(asset.get("field_dictionary")):
        if not isinstance(row, dict):
            continue
        canonical = _text(row.get("field_id") or row.get("field"))
        _entry(entries, 3, "field", row.get("field"), canonical,
               "field_dictionary_field", "field_dictionary")
        _entry(entries, 3, "field", row.get("description"), canonical,
               "field_dictionary_description", "field_dictionary")

    # Priority merge: keep the highest-priority canonicals per (kind, label).
    label_canonicals: dict[tuple[str, str], tuple[int, set[str]]] = {}
    label_evidence: dict[tuple[str, str], tuple[str, str]] = {}
    for priority, kind, label, canonical, evidence, source_kind in entries:
        key = (kind, _norm(label))
        if not key[1]:
            continue
        current = label_canonicals.get(key)
        if current is None or priority < current[0]:
            label_canonicals[key] = (priority, {_norm(canonical)})
            label_evidence[key] = (evidence, source_kind)
        elif priority == current[0]:
            label_canonicals[key][1].add(_norm(canonical))

    concepts: dict[str, dict[str, Any]] = {}
    for (kind, label), (priority, canonicals) in sorted(label_canonicals.items()):
        evidence, source_kind = label_evidence[(kind, label)]
        for canonical in sorted(canonicals):
            concept = concepts.setdefault(
                canonical,
                {
                    "kind": kind,
                    "canonical": canonical,
                    "aliases": [],
                    "evidence": [],
                    "source_kinds": [],
                },
            )
            if label not in concept["aliases"]:
                concept["aliases"].append(label)
            if evidence and evidence not in concept["evidence"]:
                concept["evidence"].append(evidence)
            if source_kind and source_kind not in concept["source_kinds"]:
                concept["source_kinds"].append(source_kind)

    receipts = [
        build_receipt(
            receipt_kind="FRAME_VALIDATION",
            status="PASS",
            reason_codes=[],
            payload={
                "concept_count": len(concepts),
                "kind_counts": {
                    kind: sum(
                        1
                        for concept in concepts.values()
                        if _text(concept.get("kind")) == kind
                    )
                    for kind in sorted(CONCEPT_KINDS)
                },
                "similarity_merge_allowed": False,
                "label_priority_sources": [
                    "understanding_model",
                    "identity_registry",
                    "permission_matrix",
                    "data_tables",
                ],
            },
        )
    ]
    return {
        "schema": CHINESE_BUSINESS_CONCEPT_REGISTRY_SCHEMA,
        "concepts": concepts,
        "receipts": receipts,
        "merge_contract": {
            "similarity_merge_allowed": False,
            "merge_requires_declared_equivalence": True,
            "label_priority": "understanding_model > identity_registry > permission_matrix > data_tables",
        },
    }


def _entry(
    entries: list[tuple[int, str, str, str, str, str]],
    priority: int,
    kind: str,
    label: Any,
    canonical: Any,
    evidence: str,
    source_kind: str,
) -> None:
    item = _norm(label)
    if item:
        entries.append((priority, kind, item, _norm(canonical) or item, evidence, source_kind))


def concept_lookup(
    registry: dict[str, Any],
    kind: str,
    mention: Any,
) -> dict[str, Any]:
    """Exact declared-label lookup → canonical concept(s).

    Status: RESOLVED (exactly one canonical), AMBIGUOUS (several distinct
    canonicals), UNKNOWN (no declared match). Containment near-matches are
    candidate-only evidence and never upgrade the status.
    """
    item = _norm(mention)
    concepts = _dict(registry.get("concepts"))
    candidates: dict[str, dict[str, Any]] = {}
    for canonical, concept in concepts.items():
        if _text(concept.get("kind")) != kind:
            continue
        if item in _list(concept.get("aliases")) or item == canonical:
            candidates[canonical] = concept
    if len(candidates) == 1:
        canonical = next(iter(candidates))
        return {
            "status": "RESOLVED",
            "canonical": canonical,
            "aliases": list(_dict(candidates[canonical]).get("aliases")),
            "evidence": list(_dict(candidates[canonical]).get("evidence")),
        }
    if len(candidates) > 1:
        return {
            "status": "AMBIGUOUS",
            "canonical": "",
            "candidates": sorted(candidates),
            "evidence": [
                evidence
                for concept in candidates.values()
                for evidence in _list(concept.get("evidence"))
            ],
        }
    # Candidate-only near matches: containment both ways, never a merge.
    near = [
        canonical
        for canonical, concept in concepts.items()
        if _text(concept.get("kind")) == kind
        and (item in canonical or any(item in alias for alias in _list(concept.get("aliases"))))
    ]
    return {
        "status": "UNKNOWN",
        "canonical": "",
        "candidates": [],
        "near_match_candidates": sorted(set(near))[:10],
        "evidence": [],
    }

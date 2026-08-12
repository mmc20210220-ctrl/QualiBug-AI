"""Project exact source-bound operation -> database FK -> entity body references."""
from __future__ import annotations
from typing import Any

from .database_body_reference_projection_common import (
    RELATION_SCHEMA, _dict, _list, _text, _stable_id, _operation_ref,
)
from .database_body_reference_source_fk_support import (
    _identifier_key, _database_model_tables, _entity_table_id, _entities_by_table,
    _operation_output_entities, _body_property_paths,
)

def _project_source_bound_operation_fk_relations(
    model: dict[str, Any],
    asset: dict[str, Any],
    foreign_keys: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project body references from operation/entity + exact DB FK lineage.

    This path does not reuse database observer mapping approval because it never
    grants database read/oracle authority.  Instead it requires the API operation
    to be source-bound to the exact child entity/table, the body field to match
    one declared child FK column under only camel/snake punctuation normalization,
    and the FK parent columns to be a declared identity key of the exact parent
    table.  Every hop therefore remains source-addressable and fail-closed.
    """
    tables = _database_model_tables(asset)
    entities_by_table = _entities_by_table(model)
    receipts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw_operation in _list(model.get("operations")):
        operation = _dict(raw_operation)
        operation_ref = _operation_ref(operation)
        if not operation_ref:
            continue
        body_paths = _body_property_paths(operation)
        if not body_paths:
            continue
        for child_entity, entity_relation in _operation_output_entities(operation, model):
            child_table_id = _entity_table_id(child_entity)
            child_table = tables.get(child_table_id)
            if not child_table:
                continue
            declared_columns = {_identifier_key(value): _text(value) for value in _list(child_table.get("columns")) if _text(value)}
            for body_path in body_paths:
                leaf = body_path.rsplit(".", 1)[-1]
                child_column = declared_columns.get(_identifier_key(leaf))
                if not child_column:
                    continue
                for relationship in foreign_keys:
                    if _text(relationship.get("child_table_id")) != child_table_id:
                        continue
                    child_columns = [_text(value) for value in _list(relationship.get("child_columns")) if _text(value)]
                    parent_columns = [_text(value) for value in _list(relationship.get("parent_columns")) if _text(value)]
                    # One body scalar can only prove one scalar FK edge. Composite
                    # references require an explicit grouped mapping authority.
                    if child_columns != [child_column] or len(parent_columns) != 1:
                        continue
                    parent_table_id = _text(relationship.get("parent_table_id"))
                    parent_table = tables.get(parent_table_id)
                    parent_entities = entities_by_table.get(parent_table_id, [])
                    if not parent_table or len(parent_entities) != 1:
                        continue
                    identity_fields = {_text(value) for value in _list(parent_table.get("identity_fields")) if _text(value)}
                    if parent_columns[0] not in identity_fields:
                        continue
                    target_entity = parent_entities[0]
                    target_entity_ref = _text(target_entity.get("id"))
                    identity = (operation_ref, body_path, target_entity_ref)
                    if not target_entity_ref or identity in seen:
                        continue
                    seen.add(identity)
                    source_refs = [
                        dict(row)
                        for row in _list(entity_relation.get("source_refs"))
                        if isinstance(row, dict)
                    ]
                    source_refs.extend([
                        {
                            "source_id": _text(relationship.get("source_id")),
                            "locator": _text(relationship.get("source_locator")),
                            "kind": "database_foreign_key",
                        },
                        {
                            "source_id": _text(child_table.get("source_id")),
                            "locator": _text(child_table.get("source_locator")),
                            "kind": "database_child_table",
                        },
                        {
                            "source_id": _text(parent_table.get("source_id")),
                            "locator": _text(parent_table.get("source_locator")),
                            "kind": "database_parent_table",
                        },
                    ])
                    receipts.append({
                        "schema": RELATION_SCHEMA,
                        "id": _stable_id("body_reference", operation_ref, body_path, target_entity_ref),
                        "status": "RESOLVED",
                        "reason_code": "",
                        "operation_ref": operation_ref,
                        "body_path": body_path,
                        "target_entity_ref": target_entity_ref,
                        "child_database_table_id": child_table_id,
                        "child_database_column": child_column,
                        "parent_database_table_id": parent_table_id,
                        "parent_database_column": parent_columns[0],
                        "database_relationship_id": _text(relationship.get("relationship_id")),
                        "operation_entity_relation_id": _text(entity_relation.get("id")),
                        "authority": "source_bound_operation_entity+database_model_exact_fk",
                        "source_refs": source_refs,
                    })
    return receipts

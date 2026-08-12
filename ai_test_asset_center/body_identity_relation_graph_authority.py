"""Install projected body-reference graph authority into semantic binding gate."""
from __future__ import annotations

from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_body_path(value: Any) -> str:
    raw = _text(value)
    return raw[2:] if raw.startswith("$.") else raw


def _projected_authority(
    operation: dict[str, Any], body_paths: list[str], behavior_ir: dict[str, Any]
) -> tuple[bool, str]:
    operation_ref = _text(operation.get("id") or operation.get("operation_id"))
    required = list(dict.fromkeys(_normalize_body_path(path) for path in body_paths if _text(path)))
    if not operation_ref or not required:
        return False, "body_identity_path_missing"
    authorities: list[str] = []
    for body_path in required:
        matches = [
            _dict(row)
            for row in _list(_dict(behavior_ir).get("body_reference_relations"))
            if _text(_dict(row).get("operation_ref")) == operation_ref
            and _normalize_body_path(_dict(row).get("body_path")) == body_path
            and _text(_dict(row).get("status")) == "RESOLVED"
            and _text(_dict(row).get("target_entity_ref"))
            and _list(_dict(row).get("source_refs"))
        ]
        targets = {_text(row.get("target_entity_ref")) for row in matches}
        if len(matches) != 1 or len(targets) != 1:
            return False, f"body_identity_relation_undeclared:{body_path}"
        authorities.append(_text(matches[0].get("authority")) or "body_reference_relation")
    return True, "+".join(sorted(set(authorities)))


def install_body_identity_relation_graph_authority(semantic_module: Any) -> None:
    if getattr(semantic_module, "_qualibug_body_identity_graph_installed", False):
        return
    original_authority = semantic_module._body_identity_relation_authority

    def body_identity_relation_authority(
        operation: dict[str, Any],
        body_paths: list[str],
        behavior_ir: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        allowed, authority = original_authority(operation, body_paths)
        if allowed:
            return allowed, authority
        return _projected_authority(operation, body_paths, _dict(behavior_ir))

    def govern_body_identity_relations(
        plan: list[dict[str, Any]],
        *,
        operation: dict[str, Any],
        behavior_ir: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        ir = _dict(behavior_ir)
        path_placeholders = set(
            semantic_module._authority.extract_placeholders(
                operation.get("path"),
                operation.get("operation_id"),
                *[str(value) for value in _list(operation.get("parameters"))],
            )
        )
        ownership_params = set(
            semantic_module._authority._ownership_params_declared_on_operation(operation)
        )
        governed: list[dict[str, Any]] = []
        for raw in plan:
            row = dict(raw) if isinstance(raw, dict) else raw
            if not isinstance(row, dict):
                governed.append(row)
                continue
            target = _text(row.get("target"))
            if (
                not semantic_module._authority._identity_shaped_target(target)
                or target in path_placeholders
                or target in ownership_params
                or _text(row.get("source_priority"))
                in {
                    "ownership_identity_param",
                    "actor_credential_secret",
                    "sequential_output_binding",
                    "runtime_actor_secret_ref",
                }
            ):
                governed.append(row)
                continue
            if _text(row.get("source_priority")) not in {"same_actor_list_read", "fixture_create_only"}:
                governed.append(row)
                continue
            body_paths = [_text(value) for value in _list(row.get("body_template_paths")) if _text(value)]
            allowed, authority = body_identity_relation_authority(operation, body_paths, ir)
            if allowed:
                row["body_identity_relation_authority"] = authority
                governed.append(row)
                continue
            row.update({
                "status": "blocked",
                "source_priority": "body_identity_relation_unresolved",
                "resolver_operations": [],
                "value_fingerprint": "",
                "blocked_reason": "BODY_IDENTITY_RELATION_NOT_SOURCE_DECLARED",
                "body_identity_relation_authority": authority,
            })
            row.pop("fixture_setup", None)
            governed.append(row)
        return governed

    original_build = semantic_module.build_binding_plan

    def build_binding_plan(*, operation, obligation, actors=None, available_values=None, behavior_ir=None):
        # Call the pre-install semantic builder's lower planner directly so its
        # legacy governor cannot reject a graph-authorized relation first.
        plan = semantic_module._original_build_binding_plan(
            operation=operation,
            obligation=obligation,
            actors=actors,
            available_values=available_values,
            behavior_ir=behavior_ir,
        )
        return govern_body_identity_relations(
            plan, operation=operation, behavior_ir=_dict(behavior_ir)
        )

    semantic_module._body_identity_relation_authority = body_identity_relation_authority
    semantic_module._govern_body_identity_relations = govern_body_identity_relations
    semantic_module.build_binding_plan = build_binding_plan
    semantic_module._authority.build_binding_plan = build_binding_plan
    semantic_module._authority._core.build_binding_plan = build_binding_plan
    semantic_module._qualibug_body_identity_graph_installed = True


__all__ = ["install_body_identity_relation_graph_authority"]

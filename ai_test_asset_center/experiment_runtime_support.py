"""Runtime-support facade with structural identity, operation, observer, binding and credential authority.

The established transport/preflight mechanics live in
``_experiment_runtime_support_mechanics``. Formal runtime execution may not
manufacture identity or credential truth by convenience:

* resource selection is structural; only an explicitly compiled state predicate
  may filter rows;
* every transport step's HTTP method comes from its Behavior IR operation;
* effect-observer derivation requires one exact source-declared write operation
  and one unambiguous materializable observer;
* every FROZEN initial flow binding must still have an executable materialization
  channel at runtime;
* opaque request credential refs must resolve through declared credential
  authorities before transport; and
* test-account live login decrypts declared at-rest material through the same
  authority before the existing login/token-refresh mechanics see it. An
  ``enc$v1$`` envelope is never submitted as a password.
"""
from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import Any

from . import _experiment_runtime_support_mechanics as _core
from . import experiment_runtime_credentials as _credentials
from ._experiment_runtime_support_mechanics import *  # noqa: F401,F403
from .binding_target_materialization_authority import (
    resolve_binding_target_materialization,
)
from .declared_credential_material import (
    prepare_declared_credential_decryption,
    resolve_declared_credential_material,
)
from .real_id_resolver import (
    _extract_entity_candidates as _structural_entity_candidates,
    bind_entity_fields as _structural_bind_entity_fields,
    normalize_path_placeholders,
)
from .request_credential_authority import resolve_request_credentials
from .runtime_binding_graph import (
    declared_effect_observers as _strict_declared_effect_observers,
)

_original_preflight_experiment_executable = _core.preflight_experiment_executable
_original_unresolved_body_placeholders = _core._unresolved_body_placeholders
_original_load_actor_tokens = _core.load_actor_tokens
_original_select_runtime_binding = _core._select_runtime_binding
_CREDENTIAL_UNRESOLVED_TOKEN = "QUALIBUG_CREDENTIAL_REF_UNRESOLVED"
_CREDENTIAL_UNRESOLVED_PLACEHOLDER = "{" + _CREDENTIAL_UNRESOLVED_TOKEN + "}"
_CREDENTIAL_ROOT_CONTEXT: ContextVar[Path | None] = ContextVar(
    "qualibug_credential_root",
    default=None,
)
_ORIGINAL_LOGIN_ATTR = "_qualibug_original_login_declared_account"
if not hasattr(_credentials, _ORIGINAL_LOGIN_ATTR):
    setattr(
        _credentials,
        _ORIGINAL_LOGIN_ATTR,
        _credentials._login_declared_account,
    )
_ORIGINAL_LOGIN_DECLARED_ACCOUNT = getattr(_credentials, _ORIGINAL_LOGIN_ATTR)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _runtime_entity_candidates(value: Any) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _structural_entity_candidates(value)
        if isinstance(row, dict)
    ]


def _select_runtime_binding(
    body: Any,
    target_path: str,
    *,
    preferred_body: Any = None,
) -> dict[str, str]:
    # The established mechanics already implement state-scoped target paths and
    # the source-declared mutation-field selection (``preferred_body``). This
    # facade only supplies the strict runtime entity-candidate authority
    # (``_runtime_entity_candidates`` below); re-implementing selection here
    # previously dropped the mutation-field preference and regressed runtime
    # binding to "first row in list order". Delegate to the original mechanics,
    # which resolves ``_runtime_entity_candidates`` from this module's strict
    # override at call time.
    return _original_select_runtime_binding(
        body,
        target_path,
        preferred_body=preferred_body,
    )


def _operation_method_authority(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> tuple[bool, str, str]:
    operations = {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }
    for phase in ("control", "treatment"):
        for raw in _list(_dict(experiment).get(f"{phase}_plan")):
            step = _dict(raw)
            if not step:
                continue
            if _text(step.get("protocol_step")) == "ui_open":
                continue
            op_ref = _text(step.get("operation_ref"))
            if not op_ref or op_ref not in operations:
                return False, "BLOCKED_MISSING_OPERATION", op_ref or "missing"
            operation = _dict(operations[op_ref])
            declared_method = _text(operation.get("method")).upper()
            if not declared_method:
                return (
                    False,
                    "BLOCKED_MISSING_OPERATION",
                    f"source_declared_method_missing:{op_ref}",
                )
            step_method = _text(step.get("method")).upper()
            if step_method and step_method != declared_method:
                return (
                    False,
                    "BLOCKED_OPERATION_CONTRACT_DRIFT",
                    f"method_mismatch:{op_ref}:step={step_method}:ir={declared_method}",
                )
    return True, "", ""


def _runtime_initial_binding_authority(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> tuple[bool, str, str]:
    """Revalidate the frozen initial-binding set before execution admission."""

    exp = _dict(experiment)
    contract = _dict(exp.get("flow_data_execution_contract"))
    if _text(contract.get("status")).upper() != "FROZEN":
        return True, "", ""

    targets: list[str] = []
    for raw in _list(contract.get("step_contracts")):
        for value in _list(_dict(raw).get("initial_binding_targets")):
            target = _text(value)
            if target and target not in targets:
                targets.append(target)

    for target in targets:
        receipt = resolve_binding_target_materialization(
            target,
            experiment=exp,
            behavior_ir=behavior_ir,
            flow_execution_contract=contract,
        )
        if _text(receipt.get("status")) == "RESOLVED":
            continue
        reason = _text(receipt.get("reason_code")) or (
            "BINDING_TARGET_HAS_NO_EXECUTABLE_MATERIALIZATION_CHANNEL"
        )
        return (
            False,
            "BLOCKED_MISSING_BINDING",
            f"runtime_initial_binding_unexecutable:{target}:{reason}",
        )
    return True, "", ""


def _mask_unresolved_credential_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _mask_unresolved_credential_refs(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_mask_unresolved_credential_refs(child) for child in value]
    if isinstance(value, str) and _text(value).startswith("secret_ref:"):
        return _CREDENTIAL_UNRESOLVED_PLACEHOLDER
    return value


def _contains_credential_unresolved_placeholder(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _contains_credential_unresolved_placeholder(child)
            for child in value.values()
        )
    if isinstance(value, list):
        return any(
            _contains_credential_unresolved_placeholder(child)
            for child in value
        )
    return isinstance(value, str) and _text(value) == _CREDENTIAL_UNRESOLVED_PLACEHOLDER


def _resolve_body_credential_refs(
    value: Any,
    *,
    root: Any,
    project: str,
) -> Any:
    """Resolve declared credential refs or leave an explicit pre-transport block."""

    resolved, receipt = resolve_request_credentials(
        value,
        root=root,
        project=project,
    )
    if int(_dict(receipt).get("unresolved_count") or 0) > 0:
        return _mask_unresolved_credential_refs(resolved)
    return resolved


def _unresolved_body_placeholders(
    value: Any,
    bindings: dict[str, Any],
) -> list[str]:
    unresolved = list(
        _original_unresolved_body_placeholders(value, bindings)
    )
    if (
        _contains_credential_unresolved_placeholder(value)
        and _CREDENTIAL_UNRESOLVED_TOKEN not in unresolved
    ):
        unresolved.append(_CREDENTIAL_UNRESOLVED_TOKEN)
    return unresolved


def _decrypting_login_declared_account(
    *,
    base_url: str,
    login_path: str,
    email: str,
    password: str,
) -> tuple[str, int]:
    """Preserve login mechanics while forbidding encrypted envelopes on transport."""

    root = _CREDENTIAL_ROOT_CONTEXT.get()
    if root is None:
        # The runtime facade owns the decryption context. A direct legacy call
        # with plaintext remains compatible; an encrypted envelope cannot be
        # authenticated without its declared root/key authority.
        if _text(password).startswith("enc$v1$"):
            raise RuntimeError("declared_actor_password_decrypt_context_missing")
        return _ORIGINAL_LOGIN_DECLARED_ACCOUNT(
            base_url=base_url,
            login_path=login_path,
            email=email,
            password=password,
        )

    resolved_password, receipt = resolve_declared_credential_material(
        password,
        root=Path(root),
    )
    if not resolved_password:
        raise RuntimeError(
            "declared_actor_password_decrypt_failed:"
            + (_text(receipt.get("reason_code")) or "unresolved")
        )
    return _ORIGINAL_LOGIN_DECLARED_ACCOUNT(
        base_url=base_url,
        login_path=login_path,
        email=email,
        password=resolved_password,
    )


def load_actor_tokens(
    root: Path,
    project: str,
    *,
    base_url: str = "",
) -> dict[str, str]:
    """Run established token acquisition with one declared decryption context."""

    # Loading an existing key also enables EnterpriseCredentialManager to
    # decrypt service-role credentials. Unavailable key is not fatal by itself:
    # plaintext-only catalogs remain valid, while encrypted values fail closed
    # when actually resolved.
    prepare_declared_credential_decryption(Path(root))
    token = _CREDENTIAL_ROOT_CONTEXT.set(Path(root))
    try:
        return _original_load_actor_tokens(
            Path(root),
            str(project),
            base_url=base_url,
        )
    finally:
        _CREDENTIAL_ROOT_CONTEXT.reset(token)


def _operation_for_observation_path(
    path: str,
    operations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    normalized = normalize_path_placeholders(_text(path))
    if not normalized.startswith("/"):
        return {}
    candidates: list[dict[str, Any]] = []
    for raw in operations.values():
        operation = _dict(raw)
        if not operation:
            continue
        operation_ref = _text(operation.get("id") or operation.get("operation_id"))
        method = _text(operation.get("method")).upper()
        candidate_path = normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        )
        if (
            operation_ref
            and method in {"POST", "PUT", "PATCH", "DELETE"}
            and candidate_path == normalized
        ):
            candidates.append(operation)
    return dict(candidates[0]) if len(candidates) == 1 else {}


def _declared_observation_path(
    path: str,
    operations: dict[str, dict[str, Any]],
    *,
    runtime_bindings: dict[str, Any] | None = None,
    request_body: Any = None,
) -> str:
    operation = _operation_for_observation_path(path, operations)
    if not operation:
        return ""
    observers = _strict_declared_effect_observers(
        operation,
        behavior_ir={"operations": list(operations.values())},
        max_candidates=5,
    )
    binding_values = {
        **_core._scalar_body_bindings(_core._request_example(operation)),
        **_core._scalar_body_bindings(request_body),
        **(runtime_bindings or {}),
    }
    write_placeholders = set(
        _core.infer_path_params(normalize_path_placeholders(path))
    )
    entity_bound: list[str] = []
    collection_bound: list[str] = []
    for observer in observers:
        template = _text(observer.get("path"))
        materialized = template
        for name, value in binding_values.items():
            if value in (None, ""):
                continue
            materialized = materialized.replace(
                "{" + name + "}",
                _core.quote(str(value), safe=""),
            )
        if not (
            materialized.startswith("/")
            and not _core.path_has_placeholders(materialized)
        ):
            continue
        obs_placeholders = set(_core.infer_path_params(template))
        if obs_placeholders and (
            not write_placeholders or (obs_placeholders & write_placeholders)
        ):
            entity_bound.append(materialized)
        elif obs_placeholders:
            continue
        else:
            collection_bound.append(materialized)
    entity_bound = list(dict.fromkeys(entity_bound))
    collection_bound = list(dict.fromkeys(collection_bound))
    if len(entity_bound) == 1:
        return entity_bound[0]
    if not entity_bound and len(collection_bound) == 1:
        return collection_bound[0]
    return ""


def _response_bound_observation_path(
    operation: dict[str, Any],
    operations: dict[str, dict[str, Any]],
    write_body: Any,
) -> dict[str, str]:
    """Return one identity readback only when the source observer is unique."""

    if not isinstance(write_body, (dict, list)):
        return {}
    observers = _strict_declared_effect_observers(
        operation,
        behavior_ir={"operations": list(operations.values())},
        max_candidates=5,
    )
    candidates: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for observer in observers:
        operation_ref = _text(observer.get("operation_ref"))
        method = _text(observer.get("method")).upper()
        path = normalize_path_placeholders(_text(observer.get("path")))
        if (
            not operation_ref
            or method not in {"GET", "HEAD"}
            or not path.startswith("/")
            or not _core.path_has_placeholders(path)
        ):
            continue
        values: dict[str, Any] = {}
        for name in _core.infer_path_params(path):
            value = _core._runtime_setup_value_from_response(write_body, name)
            if value in (None, "", [], {}):
                values = {}
                break
            values[name] = value
        if not values:
            continue
        materialized = path
        for name, value in values.items():
            materialized = materialized.replace(
                "{" + name + "}",
                _core.quote(str(value), safe=""),
            )
        if not (
            materialized.startswith("/")
            and not _core.path_has_placeholders(materialized)
        ):
            continue
        key = (operation_ref, method, path, materialized)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "operation_ref": operation_ref,
                "method": method,
                "path": materialized,
                "path_template": path,
            }
        )
    return candidates[0] if len(candidates) == 1 else {}


def preflight_experiment_executable(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    actor_tokens: dict[str, str],
) -> tuple[bool, str, str]:
    method_ok, method_reason, method_detail = _operation_method_authority(
        experiment,
        behavior_ir,
    )
    if not method_ok:
        return method_ok, method_reason, method_detail
    binding_ok, binding_reason, binding_detail = _runtime_initial_binding_authority(
        experiment,
        behavior_ir,
    )
    if not binding_ok:
        return binding_ok, binding_reason, binding_detail
    return _original_preflight_experiment_executable(
        experiment,
        behavior_ir=behavior_ir,
        actor_tokens=actor_tokens,
    )


# The historical load_actor_tokens function resolves this global from its own
# module at call time, so patching exactly that login seam preserves all existing
# stale-token/restricted-account policy while adding decryption truthfulness.
_credentials._login_declared_account = _decrypting_login_declared_account
_core.load_actor_tokens = load_actor_tokens
_core._runtime_entity_candidates = _runtime_entity_candidates
_core._select_runtime_binding = _select_runtime_binding
_core._resolve_body_credential_refs = _resolve_body_credential_refs
_core._unresolved_body_placeholders = _unresolved_body_placeholders
_core._operation_for_observation_path = _operation_for_observation_path
_core._declared_observation_path = _declared_observation_path
_core._response_bound_observation_path = _response_bound_observation_path
_core.declared_effect_observers = _strict_declared_effect_observers
_core.preflight_experiment_executable = preflight_experiment_executable

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "_runtime_entity_candidates",
        "_select_runtime_binding",
        "_operation_method_authority",
        "_runtime_initial_binding_authority",
        "_resolve_body_credential_refs",
        "_unresolved_body_placeholders",
        "_operation_for_observation_path",
        "_declared_observation_path",
        "_response_bound_observation_path",
        "load_actor_tokens",
        "preflight_experiment_executable",
    }
)

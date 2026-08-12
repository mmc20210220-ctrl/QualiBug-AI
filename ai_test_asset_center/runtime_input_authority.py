"""Source-grounded runtime input authority for blind enterprise scans.

This module contains only generic input normalization used before runtime
execution.  It deliberately derives accounts, authentication contracts and
multi-service routing from customer-provided documents/configuration and never
reads benchmark or oracle material.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable

import yaml

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_STRUCTURED_SUFFIXES = {".json", ".yaml", ".yml"}
_USERNAME_HEADERS = {
    "username", "user", "account", "login", "email", "mail",
    "账号", "帐号", "用户名", "登录名", "邮箱",
}
_PASSWORD_HEADERS = {"password", "pass", "pwd", "secret", "密码", "口令"}
_ROLE_HEADERS = {"role", "roles", "actor", "身份", "角色", "权限角色"}


def _norm_header(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())


def _read_structured(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        payload = json.loads(text) if path.suffix.lower() == ".json" else (yaml.safe_load(text) or {})
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def is_structured_openapi(path: Path) -> bool:
    payload = _read_structured(path)
    return bool(
        payload
        and (payload.get("openapi") or payload.get("swagger"))
        and isinstance(payload.get("paths"), dict)
    )


def load_recursive_openapi(input_dir: str | Path) -> tuple[dict[str, Any], str]:
    """Aggregate nested structured OpenAPI contracts without filename guessing.

    Customer projects frequently provide one contract per service under nested
    ``docs/openapi`` folders.  Only documents with a real OpenAPI/Swagger marker
    and ``paths`` object are accepted, preventing ordinary JSON/YAML config from
    being promoted to API contracts.
    """
    root = Path(input_dir)
    specs: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root)).lower()):
        if not path.is_file() or path.parent == root or path.suffix.lower() not in _STRUCTURED_SUFFIXES:
            continue
        payload = _read_structured(path)
        if payload and (payload.get("openapi") or payload.get("swagger")) and isinstance(payload.get("paths"), dict):
            specs.append((path, payload))
    if not specs:
        return {}, ""

    merged: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {"title": "QualiBug multi-service aggregate", "version": "1"},
        "paths": {},
        "components": {"schemas": {}, "securitySchemes": {}},
        "x-qualibug-openapi-sources": [],
    }
    for source_path, spec in specs:
        rel = source_path.relative_to(root).as_posix()
        merged["x-qualibug-openapi-sources"].append(rel)
        for route, raw_path_item in (spec.get("paths") or {}).items():
            if not isinstance(raw_path_item, dict):
                continue
            target_item = merged["paths"].setdefault(str(route), {})
            for method, operation in raw_path_item.items():
                method_l = str(method).lower()
                if method_l not in _HTTP_METHODS or not isinstance(operation, dict) or method_l in target_item:
                    continue
                copied = dict(operation)
                copied.setdefault("x-qualibug-source-openapi", rel)
                target_item[method_l] = copied
        components = spec.get("components") if isinstance(spec.get("components"), dict) else {}
        for section in ("schemas", "securitySchemes"):
            source_section = components.get(section) if isinstance(components.get(section), dict) else {}
            target_section = merged["components"].setdefault(section, {})
            for key, value in source_section.items():
                target_section.setdefault(str(key), value)
    return merged, f"multi_openapi:{len(specs)}"


def parse_document_accounts(
    input_dir: str | Path,
    *,
    role_key_resolver: Callable[[str], list[str]],
) -> dict[str, dict[str, str]]:
    """Parse Markdown credential tables by column semantics.

    Usernames need not be emails and unrelated columns may appear anywhere in
    the table.  No login is performed here; the executor owns authentication and
    secret redaction.
    """
    user_headers = {_norm_header(item) for item in _USERNAME_HEADERS}
    pass_headers = {_norm_header(item) for item in _PASSWORD_HEADERS}
    role_headers = {_norm_header(item) for item in _ROLE_HEADERS}
    accounts: dict[str, dict[str, str]] = {}

    for path in sorted(Path(input_dir).rglob("*.md")):
        if path.name.lower() in {"prd.md", "api.md", "readme.md", "business_rules.md", "requirements.md"}:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        index = 0
        while index + 1 < len(lines):
            header_line = lines[index].strip()
            separator_line = lines[index + 1].strip()
            if not (
                header_line.startswith("|")
                and separator_line.startswith("|")
                and re.search(r"---", separator_line)
            ):
                index += 1
                continue
            headers = [cell.strip() for cell in header_line.strip("|").split("|")]
            normalized = [_norm_header(cell) for cell in headers]
            try:
                username_index = next(i for i, key in enumerate(normalized) if key in user_headers)
                password_index = next(i for i, key in enumerate(normalized) if key in pass_headers)
                role_index = next(i for i, key in enumerate(normalized) if key in role_headers)
            except StopIteration:
                index += 2
                continue

            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                index += 1
                if max(username_index, password_index, role_index) >= len(cells):
                    continue
                username = cells[username_index]
                password = cells[password_index]
                role_text = cells[role_index]
                if not username or not password or not role_text or set(username) <= {"-", ":"}:
                    continue
                for key in role_key_resolver(role_text):
                    if key:
                        accounts.setdefault(
                            key,
                            {"username": username, "password": password, "role": key},
                        )
    return accounts


def _resolve_local_ref(document: dict[str, Any], ref: Any) -> dict[str, Any]:
    text = str(ref or "")
    if not text.startswith("#/"):
        return {}
    target: Any = document
    try:
        for token in text[2:].split("/"):
            target = target[token.replace("~1", "/").replace("~0", "~")]
    except Exception:
        return {}
    return target if isinstance(target, dict) else {}


def derive_auth_flow(
    api_doc: str,
    openapi: dict[str, Any],
    *,
    markdown_request_example: Callable[[str, str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Derive login path, credential fields and token path from source contracts."""
    login_path = ""
    login_operation: dict[str, Any] = {}
    paths = openapi.get("paths") if isinstance(openapi, dict) else {}
    if isinstance(paths, dict):
        for path, methods in paths.items():
            if "login" not in str(path).lower() or not isinstance(methods, dict):
                continue
            post = methods.get("post") or methods.get("POST")
            if isinstance(post, dict):
                login_path = str(path)
                login_operation = post
                break

    if not login_path:
        match = re.search(r"(?:^|\n)\s*(?:#{1,6}\s+)?POST\s+(/[^\s,]*login[^\s,]*)", api_doc or "", re.I)
        if match:
            login_path = match.group(1).rstrip("/") or "/"
    if not login_path:
        return {}

    example: dict[str, Any] = {}
    if markdown_request_example is not None:
        try:
            maybe_example = markdown_request_example(api_doc or "", "POST", login_path)
            example = maybe_example if isinstance(maybe_example, dict) else {}
        except Exception:
            example = {}

    property_names = [str(key) for key in example]
    if not property_names and login_operation:
        request_body = login_operation.get("requestBody") if isinstance(login_operation.get("requestBody"), dict) else {}
        content = request_body.get("content") if isinstance(request_body.get("content"), dict) else {}
        media = content.get("application/json") if isinstance(content.get("application/json"), dict) else {}
        schema = media.get("schema") if isinstance(media.get("schema"), dict) else {}
        if schema.get("$ref"):
            schema = _resolve_local_ref(openapi, schema.get("$ref"))
        if isinstance(schema.get("properties"), dict):
            property_names = [str(key) for key in schema["properties"]]

    username_field = ""
    password_field = ""
    for key in property_names:
        low = key.lower()
        if not username_field and any(token in low for token in ("email", "username", "user", "mail", "account", "phone", "mobile", "login")):
            username_field = key
        if not password_field and any(token in low for token in ("password", "pass", "pwd", "secret")):
            password_field = key
    if not username_field or not password_field:
        return {}

    token_json_path = "token"
    token_match = re.search(r'"(access_token|accessToken|token|jwt|id_token|idToken)"', api_doc or "")
    if token_match:
        token_json_path = token_match.group(1)
    return {
        "login_path": login_path,
        "username_field": username_field,
        "password_field": password_field,
        "token_json_path": token_json_path,
        "token_header_name": "Authorization",
        "token_header_prefix": "Bearer",
    }


def _candidate_service_config_files(root: Path) -> list[Path]:
    names = {
        "services.json",
        "service.json",
        "service_config.json",
        "services_config.json",
        "service-registry.json",
        "service_registry.json",
        "targets.json",
        "endpoints.json",
    }
    forbidden_parts = {"ground_truth", "oracle", "bug_matrix", "answers", "solutions"}
    candidates: list[Path] = []
    for path in sorted(root.rglob("*.json"), key=lambda item: str(item.relative_to(root)).lower()):
        relative_parts = {part.lower() for part in path.relative_to(root).parts}
        if relative_parts & forbidden_parts:
            continue
        if path.name.lower() in names:
            candidates.append(path)
    return candidates


def derive_service_routes(input_dir: str | Path, base_url: str) -> list[dict[str, Any]]:
    """Derive service base URLs from customer config + per-service OpenAPI docs.

    Supports common config shapes such as ``{"svc": 8110}``,
    ``{"svc": {"port": 8110}}`` and ``{"services": [...]}``.  Port-only
    declarations inherit scheme/host from the already approved project target,
    so this does not invent or widen target hosts.
    """
    root = Path(input_dir)
    parsed_base = urllib.parse.urlparse(str(base_url or ""))
    if not parsed_base.scheme or not parsed_base.hostname:
        return []

    openapi_by_stem: dict[str, Path] = {}
    openapi_paths: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root)).lower()):
        if path.is_file() and path.suffix.lower() in _STRUCTURED_SUFFIXES and is_structured_openapi(path):
            openapi_paths.append(path)
            openapi_by_stem[_norm_header(path.stem)] = path

    declarations: list[tuple[str, dict[str, Any]]] = []
    for cfg_path in _candidate_service_config_files(root):
        payload = _read_structured(cfg_path)
        if not payload:
            continue
        raw_services: Any = payload.get("services", payload)
        if isinstance(raw_services, dict):
            for name, value in raw_services.items():
                if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
                    declarations.append((str(name), {"port": int(value)}))
                elif isinstance(value, dict):
                    declarations.append((str(name), dict(value)))
        elif isinstance(raw_services, list):
            for value in raw_services:
                if isinstance(value, dict):
                    name = str(value.get("name") or value.get("service") or value.get("id") or "").strip()
                    if name:
                        declarations.append((name, dict(value)))

    services: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for name, declaration in declarations:
        service_url = str(declaration.get("base_url") or declaration.get("url") or "").rstrip("/")
        port_raw = declaration.get("port")
        if not service_url and port_raw not in (None, ""):
            try:
                port = int(port_raw)
            except Exception:
                continue
            hostname = parsed_base.hostname
            host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
            service_url = f"{parsed_base.scheme}://{host}:{port}"
        if not service_url:
            continue
        service_host = (urllib.parse.urlparse(service_url).hostname or "").lower()
        if service_host != (parsed_base.hostname or "").lower():
            continue

        explicit_spec = str(
            declaration.get("openapi_spec")
            or declaration.get("openapi")
            or declaration.get("spec")
            or ""
        ).strip()
        spec_path: Path | None = None
        if explicit_spec:
            path = Path(explicit_spec)
            candidates = [path] if path.is_absolute() else [root / path]
            spec_path = next((candidate for candidate in candidates if candidate.exists() and is_structured_openapi(candidate)), None)
        if spec_path is None:
            normalized_name = _norm_header(name)
            matches = [
                path for stem, path in openapi_by_stem.items()
                if stem == normalized_name
                or stem == normalized_name + "service"
                or normalized_name == stem + "service"
            ]
            if len(matches) == 1:
                spec_path = matches[0]
        if spec_path is None:
            continue
        rel = spec_path.relative_to(root).as_posix() if root in spec_path.parents else str(spec_path)
        key = (service_url, rel)
        if key in seen:
            continue
        seen.add(key)
        services.append({"name": name, "base_url": service_url, "openapi_spec": rel})
    return services


def enrich_probe_config_services(config_path: str | Path, *, input_dir: str | Path, base_url: str) -> None:
    """Persist derived service routing only when no explicit service list exists."""
    path = Path(config_path)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(config, dict) or isinstance(config.get("services"), list) and config.get("services"):
        return
    services = derive_service_routes(input_dir, base_url)
    if not services:
        return
    config["services"] = services
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

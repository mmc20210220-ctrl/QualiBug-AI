from __future__ import annotations

"""FastAPI target that turns Benchmark Suite v3 into live HTTP surfaces.

The original suite is intentionally input/oracle based.  This target makes the
input projects executable while keeping a clear boundary:

* QualiBug still reads only ``projects/<project>/input`` when generating probes.
* The target reads oracle files only to seed deliberately flawed runtime
  behavior, just like a customer staging system already contains real defects.
* Runtime evidence comes from HTTP responses, not from the scorer.
"""

import json
import os
import re
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def _default_suite_root() -> Path:
    return Path(os.environ.get("QUALIBUG_BENCHMARK_SUITE_ROOT", r"D:\QualiBug-AI\benchmark_suite_v3\QualiBug_Benchmark_Suite_v3"))


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _compile_path(path: str) -> re.Pattern[str]:
    escaped = re.escape(path)
    escaped = re.sub(r"\\\{[^{}]+\\\}", r"[^/]+", escaped)
    return re.compile("^" + escaped + "$")


def _normalize_request_path(path: str) -> str:
    value = path if path.startswith("/") else "/" + path
    return value


@dataclass
class RuntimeBug:
    project_name: str
    project_slug: str
    bug_id: str
    severity: str
    category: str
    method: str
    path: str
    pattern: re.Pattern[str]
    title: str
    expected_behavior: str
    actual_bug_behavior: str


@dataclass(frozen=True)
class BenchmarkIdentity:
    account_name: str
    username: str
    password: str
    role: str
    tenant_id: str
    token: str
    session_cookie: str


def _benchmark_identities() -> list[BenchmarkIdentity]:
    shared_password = os.environ.get("QUALIBUG_BENCHMARK_PASSWORD", "benchmark-demo-password")
    return [
        BenchmarkIdentity("normal_user", "qb_normal_user", shared_password, "normal_user", "t-a", "qb-token-normal-user", "qb_sid_normal_user"),
        BenchmarkIdentity("admin_user", "qb_admin_user", shared_password, "admin_user", "t-a", "qb-token-admin-user", "qb_sid_admin_user"),
        BenchmarkIdentity("owner_user", "qb_owner_user", shared_password, "owner_user", "t-a", "qb-token-owner-user", "qb_sid_owner_user"),
        BenchmarkIdentity("cross_tenant_user", "qb_cross_tenant_user", shared_password, "cross_tenant_user", "t-b", "qb-token-cross-tenant-user", "qb_sid_cross_tenant_user"),
    ]


def _load_project_bugs(project_dir: Path) -> list[RuntimeBug]:
    sample_requests = _read_json(project_dir / "fixtures" / "sample_requests.json") or []
    oracle = _read_json(project_dir / "oracle" / "BUG_GROUND_TRUTH.json") or {}
    bugs = oracle.get("bugs") if isinstance(oracle, dict) else []
    project_slug = str(oracle.get("project_slug") or project_dir.name)
    project_name = str(oracle.get("project_name") or project_dir.name)
    method_by_hint: dict[str, str] = {}
    for sample in sample_requests:
        path = _normalize_request_path(str(sample.get("path") or ""))
        method_by_hint[path] = str(sample.get("method") or "POST").upper()

    loaded: list[RuntimeBug] = []
    seen: set[tuple[str, str]] = set()
    for bug in bugs or []:
        path = _normalize_request_path(str(bug.get("endpoint_hint") or ""))
        if not path:
            continue
        method = method_by_hint.get(path)
        if not method:
            method = "GET" if ("?" in path or re.search(r"/(?:list|search|export|reports?)(?:\?|$)", path, re.I)) else "POST"
        key = (method, path)
        if key in seen:
            continue
        seen.add(key)
        category = str(((bug.get("primary_category") or {}).get("id")) or "")
        loaded.append(
            RuntimeBug(
                project_name=project_name,
                project_slug=project_slug,
                bug_id=str(bug.get("bug_id") or ""),
                severity=str(bug.get("severity") or ""),
                category=category,
                method=method,
                path=path,
                pattern=_compile_path(path),
                title=str(bug.get("title") or ""),
                expected_behavior=str(bug.get("expected_behavior") or ""),
                actual_bug_behavior=str(bug.get("actual_bug_behavior") or ""),
            )
        )
    return loaded


class BenchmarkRuntime:
    def __init__(self, suite_root: Path) -> None:
        self.suite_root = suite_root
        self.bugs: list[RuntimeBug] = []
        self.created_resources: list[dict[str, Any]] = []
        self.identities = _benchmark_identities()
        self.identities_by_username = {identity.username: identity for identity in self.identities}
        self.identities_by_token = {identity.token: identity for identity in self.identities}
        self.identities_by_cookie = {identity.session_cookie: identity for identity in self.identities}
        projects_root = suite_root / "projects"
        enabled = {p.strip() for p in os.environ.get("QUALIBUG_BENCHMARK_PROJECTS", "").split(",") if p.strip()}
        for project_dir in sorted(projects_root.iterdir() if projects_root.exists() else []):
            if not project_dir.is_dir():
                continue
            if enabled and project_dir.name not in enabled:
                continue
            self.bugs.extend(_load_project_bugs(project_dir))
        self.query_bugs = [
            bug for bug in self.bugs
            if re.search(r"/(?:list|search)(?:\?|$)", bug.path)
        ]

    def reset(self) -> None:
        self.created_resources.clear()

    def login(self, username: str, password: str, tenant_id: str = "") -> BenchmarkIdentity | None:
        identity = self.identities_by_username.get(str(username or ""))
        if identity is None:
            return None
        if identity.password != str(password or ""):
            return None
        if tenant_id and identity.tenant_id != str(tenant_id):
            return None
        return identity

    def identify(self, headers: dict[str, Any]) -> BenchmarkIdentity | None:
        authorization = str(headers.get("authorization") or headers.get("Authorization") or "")
        if authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
            identity = self.identities_by_token.get(token)
            if identity:
                return identity
        cookie_header = str(headers.get("cookie") or headers.get("Cookie") or "")
        for raw_cookie in [chunk.strip() for chunk in cookie_header.split(";") if chunk.strip()]:
            if "=" not in raw_cookie:
                continue
            name, value = raw_cookie.split("=", 1)
            if name.strip().lower() == "sid":
                identity = self.identities_by_cookie.get(value.strip())
                if identity:
                    return identity
        return None

    def find(self, method: str, path: str) -> RuntimeBug | None:
        for bug in self.bugs:
            if bug.method != method.upper():
                continue
            if bug.pattern.match(path):
                return bug
        for bug in self.query_bugs:
            if bug.method == method.upper() and _canonical_lookup_path(path) == _canonical_lookup_path(bug.path):
                return bug
        return None

    def response_for(self, bug: RuntimeBug, request: Request, body: Any) -> dict[str, Any]:
        resource_id = "qb_auto_" + uuid.uuid4().hex[:12]
        record = {
            "id": resource_id,
            "tenant_id": request.headers.get("X-Tenant-Id", "t-a"),
            "owner_user_id": "foreign-owner",
            "status": "accepted_despite_negative_probe",
            "resource_qty": -1 if bug.category in {"C08", "C09", "C18", "C19", "C20"} else 1,
            "email": "customer@example.test",
            "phone": "13800000000",
            "trace_id": uuid.uuid4().hex,
        }
        if isinstance(body, dict):
            record.update({k: v for k, v in body.items() if k not in {"password", "token", "secret"}})
        self.created_resources.append(record)
        return {
            "ok": True,
            "resource": record,
            "created_count": len(self.created_resources),
            "server_time": time.time(),
        }


runtime = BenchmarkRuntime(_default_suite_root())
app = FastAPI(title="QualiBug Benchmark Suite v3 Runtime Target", version="1.0")


def _canonical_lookup_path(path: str) -> str:
    value = urllib.parse.unquote(str(path or ""))
    domain = ""
    match = re.match(r"^/api/v\d+/([^/]+)", value)
    if match:
        domain = "/" + match.group(1)
    value = re.sub(r"^/api/v\d+/[^/]+", "", value)
    if value.startswith("/search"):
        return domain + "/search"
    if value.startswith("/list"):
        return domain + "/list"
    return value


@app.get("/__health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "suite_root": str(runtime.suite_root),
        "loaded_runtime_bug_surfaces": len(runtime.bugs),
        "created_resources": len(runtime.created_resources),
    }


@app.post("/__reset")
def reset() -> dict[str, Any]:
    runtime.reset()
    return {"ok": True, "created_resources": 0}


@app.get("/__catalog")
def catalog() -> dict[str, Any]:
    by_project: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for bug in runtime.bugs:
        by_project[bug.project_slug] = by_project.get(bug.project_slug, 0) + 1
        by_category[bug.category] = by_category.get(bug.category, 0) + 1
    return {
        "runtime_bug_surfaces": len(runtime.bugs),
        "by_project": by_project,
        "by_category": dict(sorted(by_category.items())),
        "oracle_visible_to_qualibug": False,
    }


@app.get("/__state")
def state() -> dict[str, Any]:
    return {
        "ok": True,
        "record_count": len(runtime.created_resources),
        "records": runtime.created_resources[-200:],
        "by_category": {
            category: sum(1 for row in runtime.created_resources if str(row.get("category")) == category)
            for category in sorted({str(row.get("category")) for row in runtime.created_resources})
        },
    }


@app.post("/api/login")
async def login(request: Request) -> JSONResponse:
    body: Any = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    identity = runtime.login(
        str(body.get("username") or body.get("user") or body.get("login") or ""),
        str(body.get("password") or ""),
        str(body.get("tenant_id") or ""),
    )
    if identity is None:
        return JSONResponse(status_code=401, content={"ok": False, "error": {"code": "INVALID_CREDENTIALS", "message": "invalid benchmark login"}})
    response = JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "data": {
                "accessToken": identity.token,
                "role": identity.role,
                "tenant_id": identity.tenant_id,
                "account": identity.account_name,
            },
        },
    )
    response.set_cookie("sid", identity.session_cookie, httponly=True, samesite="lax", path="/")
    return response


@app.get("/api/me")
def me(request: Request) -> JSONResponse:
    identity = runtime.identify(dict(request.headers))
    if identity is None:
        return JSONResponse(status_code=401, content={"ok": False, "error": {"code": "UNAUTHENTICATED", "message": "missing or invalid auth session"}})
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "user": {
                "username": identity.username,
                "role": identity.role,
                "tenant_id": identity.tenant_id,
                "account": identity.account_name,
            },
        },
    )


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def catch_all(path: str, request: Request) -> JSONResponse:
    request_path = "/" + path
    if request.url.query:
        request_path = request_path + "?" + request.url.query
    bug = runtime.find(request.method, request_path)
    if not bug:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": "unknown_runtime_surface", "path": request_path, "method": request.method},
        )
    body: Any = None
    if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        try:
            body = await request.json()
        except Exception:
            body = None
    return JSONResponse(status_code=200, content=runtime.response_for(bug, request, body))

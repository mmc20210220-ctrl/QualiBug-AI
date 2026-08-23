"""Declared-actor credential and token resolution for experiment runtime.

Extracted from ``experiment_runtime_support`` to restore that module's
architecture budget. Symbols are re-exported from ``experiment_runtime_support``
so existing import paths and symbol identity stay stable.

The authority here is source-declared test accounts and the configured
enterprise credential manager. No account is invented, and a stale or orphan
token snapshot is never handed to the executor as if it were live.

``load_actor_tokens`` is run-level idempotent: within one scan process the
catalog is resolved once per (root, project, base_url, login-env, input-file
fingerprint, TTL bucket) and reused, because repeated resolution re-parses the
account files, re-probes login endpoints per actor and re-logs every stale
snapshot for every caller (~450×/run measured 2026-08-22, CMP_77d5dfe1 —
minutes of pure redundant HTTP/file churn between governed writes).
File-fingerprint invalidation keeps operator edits authoritative;
``QUALIBUG_ACTOR_TOKEN_CACHE_DISABLED=1`` bypasses the cache entirely, and
``QUALIBUG_ACTOR_TOKEN_CACHE_TTL_SECONDS`` (default 300) bounds how long a
resolved catalog may be reused before wall-clock expiry is re-evaluated.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .sandbox_write_executor import _http_request


_LOGGER = logging.getLogger(__name__)

_ACTOR_TOKEN_CACHE: dict[tuple[Any, ...], dict[str, str]] = {}
_ACTOR_TOKEN_CACHE_LOCK = threading.Lock()
_ACTOR_TOKEN_CACHE_MAX_KEYS = 16
# Dedup is TIME-WINDOWED, not once-per-process: a long-lived backend runs many
# scans against the same project, and silencing every repeat would hide the
# fact that THOSE scans also executed with stale credentials. Within the
# window (default 600s, QUALIBUG_ACTOR_TOKEN_DEDUP_WINDOW_SECONDS) repeats are
# suppressed — that kills the 3150-print flood inside one scan — while each
# new scan past the window warns once again.
_STALE_SNAPSHOT_PRINTED: dict[tuple[str, str, str, str], float] = {}
_EXPIRED_SUMMARY_WARNED: dict[tuple[str, str, tuple[str, ...]], float] = {}
_STALE_DEDUP_LOCK = threading.Lock()


def _stale_dedup_window_seconds() -> float:
    raw = str(os.environ.get("QUALIBUG_ACTOR_TOKEN_DEDUP_WINDOW_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 600.0
    return value if value >= 0 else 600.0


def _dedup_should_emit(key: Any, table: dict) -> bool:
    """True when this dedup key may emit now (first time or window elapsed)."""
    now = time.monotonic()
    with _STALE_DEDUP_LOCK:
        last = table.get(key)
        if last is not None and (now - last) < _stale_dedup_window_seconds():
            return False
        table[key] = now
        # Bound memory: drop entries older than twice the window.
        horizon = 2 * _stale_dedup_window_seconds()
        for stale_key in [k for k, v in table.items() if (now - v) > horizon]:
            del table[stale_key]
        return True

# A successful live-login refresh persists the renewed tokens back into
# ``test_accounts.json``, which bumps that file's fingerprint. Without this
# bookkeeping every cached resolution would invalidate itself through its own
# persist (resolve → persist → new fingerprint → resolve → …), an unbounded
# login/persist churn. The fingerprint written by our own persist maps back to
# the fingerprint the resolving cache entry used, so the very next load hits
# that entry; an operator edit produces a third, unseen fingerprint and
# invalidates normally.
_SELF_WRITE_FINGERPRINT: dict[tuple[Any, Any], tuple[Any, Any]] = {}


def _env_truthy(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _file_fingerprint(path: Path | None) -> tuple[Any, ...]:
    if path is None:
        return ("", -1, -1)
    try:
        st = path.stat()
        return (str(path), int(st.st_size), int(st.st_mtime_ns))
    except OSError:
        return (str(path), -1, -1)


def _actor_token_cache_ttl_seconds() -> int:
    raw = str(os.environ.get("QUALIBUG_ACTOR_TOKEN_CACHE_TTL_SECONDS") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return 300
    return value if value > 0 else 300


def _actor_token_input_fingerprint(root: Path, project: str) -> tuple[Any, ...]:
    parts: list[tuple[Any, ...]] = [
        os.environ.get("QUALIBUG_LOGIN_PATH") or "",
        os.environ.get("QUALIBUG_LOGIN_BASE_URL") or "",
        _file_fingerprint(Path(root) / "platform_inputs" / str(project) / "test_accounts.json"),
        # The credential base_url fallback resolves from this declared config;
        # an operator edit must invalidate cached catalogs resolved through it.
        _file_fingerprint(Path(root) / "platform_inputs" / str(project) / "real_project_config.json"),
        _file_fingerprint(_credential_config_path(root, project)),
    ]
    for directory in (
        Path(root) / "projects" / str(project) / "input",
        Path(root) / "platform_inputs" / str(project),
        Path(root) / "platform_workspace" / str(project) / "input",
    ):
        for fname in ("TEST_ACCOUNTS.md", "test_accounts.md"):
            parts.append(_file_fingerprint(directory / fname))
    return tuple(parts)


def _load_actor_tokens_cached(root: Path, project: str, *, base_url: str) -> dict[str, str]:
    ttl = _actor_token_cache_ttl_seconds()
    identity = (str(root), str(project))
    raw_fp = _actor_token_input_fingerprint(root, project)
    prior_fp = _SELF_WRITE_FINGERPRINT.get(identity)
    effective_fp = raw_fp
    if prior_fp is not None and raw_fp == prior_fp[0]:
        # The only change is our own token-refresh persist — not a real edit.
        effective_fp = prior_fp[1]
    key: tuple[Any, ...] = (
        identity[0],
        identity[1],
        str(base_url),
        int(time.time()) // ttl,
        effective_fp,
    )
    with _ACTOR_TOKEN_CACHE_LOCK:
        cached = _ACTOR_TOKEN_CACHE.get(key)
        if cached is not None:
            return dict(cached)
    tokens = _load_actor_tokens_uncached(root, project, base_url=base_url)
    snapshot = dict(tokens)
    post_fp = _actor_token_input_fingerprint(root, project)
    if post_fp != effective_fp:
        # The resolution itself rewrote an input file (token refresh persist).
        # Map the post-write fingerprint back to this entry's fingerprint so
        # the next load resolves to the same cache key instead of churning.
        with _ACTOR_TOKEN_CACHE_LOCK:
            _SELF_WRITE_FINGERPRINT[identity] = (post_fp, effective_fp)
    with _ACTOR_TOKEN_CACHE_LOCK:
        current_bucket = key[3]
        for stale_key in [
            k for k in _ACTOR_TOKEN_CACHE if k[:4] == key[:4] and k[3] != current_bucket
        ]:
            _ACTOR_TOKEN_CACHE.pop(stale_key, None)
        if len(_ACTOR_TOKEN_CACHE) >= _ACTOR_TOKEN_CACHE_MAX_KEYS:
            _ACTOR_TOKEN_CACHE.clear()
        _ACTOR_TOKEN_CACHE[key] = snapshot
    return dict(snapshot)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


@contextmanager
def _exclusive_file_lock(path: Path, *, timeout_seconds: float = 30.0) -> Iterator[None]:
    """A cross-process advisory lock over ``path.lock`` (best-effort).

    Concurrent scans refresh the same ``test_accounts.json``.  A plain
    read-modify-write lets two processes interleave: each logs in (which the
    target treats as single-session, invalidating the other's token) and each
    overwrites the other's freshly written snapshot — the observed
    ``declared_actor_tokens_expired`` ×85 thrash loop.  Serializing the
    refresh closes the lost-update without any cross-process registry.

    Uses ``msvcrt.locking`` on Windows and ``fcntl.flock`` elsewhere; a failure
    to acquire the lock (or an unavailable primitive) degrades to no locking
    rather than deadlocking a scan.  Single-yield by construction: acquisition
    completes before ``yield`` and release runs in ``finally``, so a write
    exception inside the block can never re-enter the generator.
    """
    lock_path = Path(str(path) + ".lock")
    handle = None
    locked = False
    try:
        try:
            handle = open(lock_path, "a+b")
        except OSError:
            handle = None
        if handle is not None:
            deadline = time.monotonic() + timeout_seconds
            try:
                import msvcrt
            except ImportError:
                msvcrt = None
            try:
                import fcntl
            except ImportError:
                fcntl = None
            while True:
                acquired = False
                try:
                    if msvcrt is not None:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                        acquired = True
                    elif fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                except OSError:
                    acquired = False
                if acquired:
                    locked = True
                    break
                if msvcrt is None and fcntl is None:
                    # No locking primitive: proceed without an advisory lock.
                    break
                if time.monotonic() >= deadline:
                    # Timeout: proceed without the lock rather than block a scan.
                    break
                time.sleep(0.05)
        yield
    finally:
        if locked and handle is not None:
            try:
                try:
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except ImportError:
                    try:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except ImportError:
                        pass
            except OSError:
                pass
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` atomically via a same-directory temp file + replace.

    A direct ``write_text`` is a torn-write hazard for concurrent readers: a
    reader can observe a half-written JSON and drop every account as unparsable.
    ``os.replace`` is atomic on the same volume, so readers see either the old
    or the new snapshot, never a partial one.

    Windows: ``os.replace`` onto an existing file fails with PermissionError
    when the target is briefly held open by a concurrent reader.  Fall back to
    a direct write then — a token refresh must never crash the scan — while
    still cleaning up the temp file.
    """
    tmp = Path(str(path) + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            path.write_text(text, encoding="utf-8")
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass



def _jwt_expired(token: str, *, skew_seconds: int = 30) -> bool:
    """Whether a JWT's own ``exp`` claim has passed.

    Signature is not checked and must not be -- the target owns the secret. Only
    the expiry claim is read, and a token whose claim cannot be parsed is treated
    as usable so a non-JWT bearer (opaque token, API key) is not discarded.

    A stored token is a snapshot, and a snapshot goes stale. Returning one blind
    made the executor send a dead credential; the target answered 401 and the
    oracle recorded that as "the endpoint rejected this actor", which is a
    fabricated authorization defect rather than a finding about the target.
    """
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return False
    import base64

    try:
        segment = parts[1]
        segment += "=" * (-len(segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(segment.encode("ascii")).decode("utf-8"))
    except Exception:
        return False
    exp = claims.get("exp") if isinstance(claims, dict) else None
    if not isinstance(exp, (int, float)):
        return False
    return bool(time.time() + skew_seconds >= float(exp))


def _token_from_login_response(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    return _text(
        body.get("token")
        or body.get("access_token")
        or body.get("jwt")
        or _dict(body.get("data")).get("token")
        or _dict(body.get("data")).get("access_token")
    )


def _login_declared_account(
    *,
    base_url: str,
    login_path: str,
    email: str,
    password: str,
    identity_field: str = "",
) -> tuple[str, int]:
    """Acquire a live bearer token from a source-declared account password.

    Neither the login body's identity key nor the login path is hardcoded: an
    enterprise target may declare ``username``/``account``/``loginName``/``mobile``
    and serve login at ``/api/v1/auth/login`` instead of ``/api/auth/login``.
    Hardcoding either fabricated an authorization defect where the real gap was
    the harness's own login shape. Identity keys are probed via the same
    ``_identity_field_candidates`` authority the enterprise credential manager
    uses, and login paths are probed over the same candidate list that manager
    uses; an explicitly declared ``identity_field`` wins alone. The first
    (path, identity-key) pair that returns a token is authoritative.

    Returns ``(token, http_status)``. An empty token means login did not yield
    usable credentials; callers must not fall back to an orphan snapshot when a
    password was declared for this account.
    """
    identity = str(email or "").strip()
    if identity_field:
        fields = [identity_field]
    else:
        try:
            from .enterprise_credential_manager import _identity_field_candidates

            fields = _identity_field_candidates(identity)
        except Exception:
            fields = ["email", "username", "account", "loginName", "mobile"]

    # Probe login paths most-likely-first: the declared path, then the shared
    # common-path safety net (single candidate authority shared with the
    # enterprise credential manager and outcome validation).
    declared = str(login_path or "").strip().strip("/")
    paths: list[str] = []
    if declared:
        paths.append(declared)
    from .enterprise_credential_manager import COMMON_LOGIN_PATH_CANDIDATES

    paths.extend(COMMON_LOGIN_PATH_CANDIDATES)
    seen: set[str] = set()
    unique_paths: list[str] = []
    for p in paths:
        p = p.strip().strip("/")
        if p and p not in seen:
            seen.add(p)
            unique_paths.append(p)

    last_status = 0
    for path in unique_paths:
        url = base_url.rstrip("/") + "/" + path
        for field in fields:
            resp = _http_request(
                "POST",
                url,
                body={field: identity, "password": password},
                timeout=8.0,
            )
            status = int(resp.get("status") or 0)
            last_status = status
            token = _token_from_login_response(resp.get("body"))
            if status == 200 and token:
                return token, status
            # A 4xx on a probe is a shape/path mismatch, not a transport error;
            # keep probing the remaining identity fields and paths.
    return "", last_status


def _register_actor_token_aliases(
    tokens: dict[str, str],
    *,
    token: str,
    role: str,
    account_ref: str,
    email: str,
    status: str,
    aliases: list[Any],
) -> None:
    for alias in dict.fromkeys(_text(value) for value in aliases):
        if not alias:
            continue
        tokens[alias] = token
        tokens[f"secret_ref:test_accounts:{alias}"] = token
    if status not in {"DISABLED", "LOCKED"}:
        if role:
            tokens.setdefault(role, token)
            tokens.setdefault(f"secret_ref:test_accounts:{role}", token)
            tokens.setdefault(f"secret_ref:context:{role}", token)
            tokens.setdefault(f"secret_ref:actor:{role}", token)
    if email.count("@") == 1:
        local = email.split("@", 1)[0]
        if local:
            tokens.setdefault(local, token)
            tokens.setdefault(f"secret_ref:test_accounts:{local}", token)
    if account_ref:
        tokens.setdefault(account_ref, token)
        tokens.setdefault(f"secret_ref:test_accounts:{account_ref}", token)


def _persist_refreshed_account_tokens(
    path: Path,
    payload: Any,
    refreshed: dict[str, str],
) -> None:
    """Write live login tokens back into the declared account catalog.

    Other readers (interface discovery, bootstrap) consume the same file. Leaving
    an orphan JWT snapshot after a successful password login recreates the
    create-time foreign-key failure on the next consumer.
    """
    if not refreshed:
        return

    def apply_row(row: dict[str, Any], *, source_key: str = "") -> bool:
        email = _text(row.get("email") or row.get("username"))
        account_ref = _text(
            row.get("account_ref")
            or row.get("profile")
            or row.get("name")
            or row.get("id")
            or source_key
        )
        token = refreshed.get(email) or refreshed.get(account_ref)
        if not token:
            return False
        row["token"] = token
        if "access_token" in row:
            row["access_token"] = token
        if "jwt" in row:
            row["jwt"] = token
        row["identity_observation_source"] = "login_response"
        return True

    changed = False
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict):
                changed = apply_row(row) or changed
    elif isinstance(payload, dict):
        matched_collection = False
        for collection_key in ("accounts", "actors", "users"):
            collection = payload.get(collection_key)
            if not isinstance(collection, list):
                continue
            matched_collection = True
            for row in collection:
                if isinstance(row, dict):
                    changed = apply_row(row) or changed
            break
        if not matched_collection:
            for key, row in payload.items():
                if key in {"schema", "schema_version", "meta"} or not isinstance(row, dict):
                    continue
                changed = apply_row(row, source_key=_text(key)) or changed
    if not changed:
        return
    # Serialize the read-modify-write across concurrent scans and write
    # atomically: a torn or clobbered snapshot is what drives the token-thrash
    # loop (each process overwrites the other's fresh token, then re-logs in
    # and invalidates the sibling's in-memory token at the target).
    with _exclusive_file_lock(path):
        # Re-read under the lock so a sibling's fresh token is preserved rather
        # than clobbered by this process's older payload.
        current = payload
        try:
            on_disk = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            on_disk = None
        if isinstance(on_disk, list) and isinstance(payload, list):
            if len(on_disk) == len(payload):
                current = on_disk
        elif isinstance(on_disk, dict) and isinstance(payload, dict):
            current = on_disk
        # Apply this process's refreshed tokens onto the re-read snapshot so the
        # write never reverts a sibling's refresh.
        if current is not payload:
            payload = current
            changed = False
            if isinstance(payload, list):
                for row in payload:
                    if isinstance(row, dict):
                        changed = apply_row(row) or changed
            elif isinstance(payload, dict):
                matched_collection = False
                for collection_key in ("accounts", "actors", "users"):
                    collection = payload.get(collection_key)
                    if not isinstance(collection, list):
                        continue
                    matched_collection = True
                    for row in collection:
                        if isinstance(row, dict):
                            changed = apply_row(row) or changed
                    break
                if not matched_collection:
                    for key, row in payload.items():
                        if key in {"schema", "schema_version", "meta"} or not isinstance(row, dict):
                            continue
                        changed = apply_row(row, source_key=_text(key)) or changed
            if not changed:
                return
        _atomic_write_text(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )


def _credential_config_path(root: Path, project: str) -> Path | None:
    """Return the project credential config from its existing SSOT location."""
    for candidate in (
        Path(root) / "platform_workspace" / str(project) / "multi_service_config.json",
        Path(root) / "platform_inputs" / str(project) / "multi_service_config.json",
    ):
        if candidate.exists():
            return candidate
    return None


def _configured_credential_manager(root: Path, project: str) -> Any | None:
    """Load the configured enterprise credential manager, or no manager.

    The credential file is control-plane input, not a knowledge source. Keep its
    schema and decryption authority in ``EnterpriseCredentialManager`` instead
    of re-parsing service accounts in the execution path. Invalid JSON and an
    unavailable decryption key are surfaced to the caller so a configured
    credential cannot silently turn into an anonymous run.
    """
    config_path = _credential_config_path(root, project)
    if config_path is None:
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8") or "null")
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("runtime_credential_config_invalid") from exc
    if not isinstance(config, dict):
        raise RuntimeError("runtime_credential_config_root_invalid")
    from .enterprise_credential_manager import EnterpriseCredentialManager

    manager = EnterpriseCredentialManager(str(project), Path(root))
    manager.load_from_dict(config)
    return manager


def configured_runtime_accounts(root: Path, project: str) -> list[dict[str, str]]:
    """Project runtime actors from the existing service credential authority.

    A role-to-account coordinate is executable only when the operator declared
    the role and login identity in the credential manager. No localized display
    label is translated here, and no account is invented from a role name.
    """
    manager = _configured_credential_manager(root, project)
    if manager is None:
        return []
    rows: list[dict[str, str]] = []
    credentials = sorted(
        manager.store.all(),
        key=lambda item: (
            _text(getattr(item, "service", "")).lower(),
            _text(getattr(item, "role", "")).lower(),
            _text(getattr(item, "username", "")).lower(),
        ),
    )
    for credential in credentials:
        role = _text(getattr(credential, "role", ""))
        service = _text(getattr(credential, "service", ""))
        username = _text(getattr(credential, "username", ""))
        if not role:
            continue
        row = {
            "role": role,
            "service": service,
            "status": "active",
        }
        if username:
            row.update({
                "account_ref": username,
                "secret_ref": f"secret_ref:test_accounts:{username}",
            })
        elif getattr(credential, "bearer_token", "") or getattr(credential, "api_key", ""):
            # A pre-authenticated service credential has no login identity. Keep
            # it role-scoped; never fabricate an account_ref for it.
            row["secret_ref"] = f"secret_ref:service:{service}:{role}"
        else:
            continue
        rows.append(row)
    return rows


def _configured_credential_tokens(
    root: Path,
    project: str,
    *,
    base_url: str = "",
) -> dict[str, str]:
    """Acquire tokens through the existing enterprise credential manager."""
    manager = _configured_credential_manager(root, project)
    if manager is None:
        return {}
    results = manager.login_all_services(timeout=8)
    failed = [
        f"{service}/{role}"
        for service, role_results in results.items()
        for role, ok in role_results.items()
        if not ok
    ]
    if failed:
        _LOGGER.warning(
            "configured_actor_login_incomplete project=%s credential_count=%s "
            "failed=%s",
            project,
            sum(len(value) for value in results.values()),
            ",".join(sorted(failed)[:12]),
        )
    tokens: dict[str, str] = {}
    role_tokens: dict[str, list[str]] = {}
    for credential in manager.store.all():
        service = _text(getattr(credential, "service", ""))
        role = _text(getattr(credential, "role", ""))
        if not service or not role:
            continue
        token = _text(manager.get_token(service, role, auto_refresh=False))
        if not token:
            continue
        username = _text(getattr(credential, "username", ""))
        if username:
            tokens[username] = token
            tokens[f"secret_ref:test_accounts:{username}"] = token
        else:
            tokens[f"secret_ref:service:{service}:{role}"] = token
        role_tokens.setdefault(role, []).append(token)
    for role, values in role_tokens.items():
        # Role aliases are safe only when the configured authority has one
        # active credential for that role. Account-qualified actors always use
        # their exact secret_ref above.
        unique_values = list(dict.fromkeys(values))
        if len(unique_values) != 1:
            continue
        token = unique_values[0]
        tokens.setdefault(role, token)
        tokens.setdefault(f"secret_ref:test_accounts:{role}", token)
        tokens.setdefault(f"secret_ref:context:{role}", token)
        tokens.setdefault(f"secret_ref:actor:{role}", token)
    return tokens


def _effective_target_base_url(root: Path, project: str, base_url: str) -> str:
    """Resolve the approved target base_url for credential acquisition.

    Priority: caller-supplied value > ``QUALIBUG_TARGET_BASE_URL`` > the
    project's own declared runtime config (``approved_base_url``/``base_url``).
    The config leg exists because a base_url-less caller previously silenced
    the whole live-login/MD-fallback branch (`if base_url:`): CMP_77d5dfe1
    measured 450 such calls in one run — every declared actor stale, zero login
    attempts, entire campaign executing without credentials. Only a declared,
    operator-owned value is consulted; nothing is inferred or fabricated.
    """
    resolved = _text(base_url) or _text(os.environ.get("QUALIBUG_TARGET_BASE_URL") or "")
    if resolved:
        return resolved
    try:
        from .project_runtime_config import load_real_project_config

        cfg = load_real_project_config(project, Path(root)) or {}
    except Exception:
        return ""
    return _text(cfg.get("approved_base_url") or cfg.get("base_url") or "")


def load_actor_tokens(root: Path, project: str, *, base_url: str = "") -> dict[str, str]:
    """Map role / secret_ref → bearer token from declared test accounts only.

    Run-level idempotent entry point: see module docstring for the caching
    contract. Resolution itself lives in ``_load_actor_tokens_uncached``.
    The config fallback for an empty ``base_url`` resolves INSIDE the uncached
    resolver so a cache hit performs zero file I/O — resolving it here would
    re-parse the project config on every call and reintroduce exactly the
    per-call churn this cache exists to remove. The cache key therefore uses
    the RAW caller value; two callers (one explicit, one falling back to the
    same declared URL) occupy distinct keys with identical results, which only
    costs one extra resolution on their first miss each.
    """
    if _env_truthy("QUALIBUG_ACTOR_TOKEN_CACHE_DISABLED"):
        return _load_actor_tokens_uncached(
            Path(root), project,
            base_url=_effective_target_base_url(Path(root), project, base_url),
        )
    return _load_actor_tokens_cached(Path(root), project, base_url=base_url)


def _load_actor_tokens_uncached(root: Path, project: str, *, base_url: str = "") -> dict[str, str]:
    """Resolve the actor-token catalog from declared accounts (uncached).

    P0-4/P0-7 enhanced: falls back to parsing TEST_ACCOUNTS.md from the
    project input directory and performing login when tokens are absent.
    Priority: test_accounts.json > TEST_ACCOUNTS.md (with login) > empty.

    Stored tokens that have expired are dropped so the MD-login fallback runs,
    rather than being handed to the executor as if they were live. ``base_url`` is
    threaded from the caller's approved target because relying on the
    QUALIBUG_TARGET_BASE_URL environment variable made that fallback dead under
    the HTTP scan entrypoint, which never sets it.

    When a declared account still carries a password and an approved ``base_url``
    is available, password login is preferred over any stored JWT snapshot. An
    unexpired token can still be orphaned after a target DB reset: the signature
    validates and reads may return empty 200, but writes fail with a user-identity
    foreign key. Preferring live login closes that gap without inventing bodies.
    """
    base_url = _effective_target_base_url(Path(root), project, base_url)
    # Login endpoint authority: project-declared login_api/login_base_url first,
    # environment override second, the generic default last. In a multi-service
    # deployment the auth service is a different host than the scan target, so
    # login must be sent to the declared auth base_url while stored cross-service
    # JWTs remain a valid credential channel when login is unreachable.
    login_path = _text(os.environ.get("QUALIBUG_LOGIN_PATH") or "")
    login_base_url = _text(os.environ.get("QUALIBUG_LOGIN_BASE_URL") or "")
    try:
        from .project_runtime_config import load_real_project_config

        _rpc = load_real_project_config(project, root) or {}
    except Exception:
        _rpc = {}
    if not login_path:
        login_path = _text(_rpc.get("login_api") or "/api/auth/login")
    if not login_base_url:
        login_base_url = _text(_rpc.get("login_base_url") or "")
    login_url_base = login_base_url or base_url
    multi_service_login = bool(
        login_base_url and login_base_url.rstrip("/") != base_url.rstrip("/")
    )
    path = Path(root) / "platform_inputs" / str(project) / "test_accounts.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            payload = {}
        tokens: dict[str, str] = {}
        expired_roles: list[str] = []
        password_login_failed: list[str] = []
        refreshed_for_persist: dict[str, str] = {}
        rows: list[Any] = []
        if isinstance(payload, dict):
            rows = list(payload.get("accounts") or payload.get("actors") or payload.get("users") or [])
            if not rows:
                rows = [
                    {**(value if isinstance(value, dict) else {}), "account_ref": key}
                    for key, value in payload.items()
                    if isinstance(value, dict) and key not in {"schema", "schema_version", "meta"}
                ]
        elif isinstance(payload, list):
            rows = payload
        for row in rows:
            if not isinstance(row, dict):
                continue
            role = _text(
                row.get("authenticated_role")
                or row.get("role")
                or row.get("name")
                or row.get("id")
            )
            account_ref = _text(
                row.get("account_ref")
                or row.get("profile")
                or row.get("name")
                or row.get("id")
                or row.get("email")
                or row.get("username")
            )
            email = _text(row.get("email") or row.get("username"))
            password = _text(row.get("password"))
            status = _text(
                row.get("authenticated_status")
                or row.get("status")
                or row.get("account_status")
                or row.get("state")
                or "active"
            ).upper()
            stored_token = _text(row.get("token") or row.get("access_token") or row.get("jwt"))
            # A restricted account (DISABLED/LOCKED/...) is itself a declared
            # authorization test subject: the property under test is that the
            # target rejects its credential.  Its password/stored token must
            # therefore remain resolvable so authorization obligations can
            # observe the real rejection.  Live login is still attempted when a
            # password is declared: an unexpected 200 is itself defect evidence
            # (a disabled account that can still authenticate), while a
            # rejected login is the expected observation for this subject.
            status_restricted = status in {"DISABLED", "LOCKED", "SUSPENDED", "INACTIVE"}
            token = ""
            if base_url and email and password:
                try:
                    live_token, login_status = _login_declared_account(
                        base_url=login_url_base,
                        login_path=login_path,
                        email=email,
                        password=password,
                    )
                except Exception as exc:
                    _LOGGER.warning(
                        "actor_login_transport_failed project=%s role=%s error=%s",
                        project,
                        role or account_ref or email,
                        type(exc).__name__,
                    )
                    if not status_restricted:
                        password_login_failed.append(role or account_ref or email)
                        continue
                else:
                    if login_status == 200 and live_token:
                        token = live_token
                        refreshed_for_persist[email or account_ref] = live_token
                    elif status_restricted:
                        # Rejected login is the declared expected behavior for
                        # this subject; the stored credential still resolves so
                        # the executor can observe the target's real rejection.
                        _LOGGER.info(
                            "restricted_actor_login_rejected_expected project=%s "
                            "role=%s status=%s account_ref=%s",
                            project,
                            role or account_ref or email,
                            login_status,
                            account_ref or email,
                        )
                    else:
                        _LOGGER.warning(
                            "actor_login_rejected project=%s role=%s status=%s "
                            "token_present=%s action=%s",
                            project,
                            role or account_ref or email,
                            login_status,
                            False,
                            "skip_orphan_snapshot" if not multi_service_login else "fallback_stored_token",
                        )
                        password_login_failed.append(role or account_ref or email)
                        if not multi_service_login:
                            # Password was the authority. Do not hand the executor an
                            # orphan JWT that reads as authenticated but cannot insert.
                            continue
            if not token:
                if not role or not stored_token:
                    continue
                if _jwt_expired(stored_token):
                    if status_restricted:
                        # A stale restricted-account snapshot is exactly the
                        # credential the target is expected to reject.  Keeping
                        # it resolvable lets the authorization obligation observe
                        # the real rejection instead of fabricating a missing
                        # credential; active accounts still drop stale snapshots
                        # so a 401 is never misread as an authorization defect.
                        token = stored_token
                    else:
                        # Recorded, not silently skipped: a stale snapshot is the
                        # difference between "no credential" and "a credential the
                        # target will reject". Printed once per role/account per
                        # process — repeat callers re-resolving the same stale
                        # catalog must not flood the run log (3150×/run measured
                        # 2026-08-22); the structured summary below stays the
                        # machine-readable signal.
                        stale_key = (
                            str(root),
                            str(project),
                            role,
                            account_ref,
                        )
                        expired_roles.append(role)
                        if _dedup_should_emit(stale_key, _STALE_SNAPSHOT_PRINTED):
                            print(
                                f"[STALE] declared actor token expired role={role} "
                                f"account_ref={account_ref}",
                                flush=True,
                            )
                        continue
                else:
                    token = stored_token
            if not token:
                continue
            if not role and not account_ref and not email:
                continue
            aliases = [
                row.get("account_ref"),
                row.get("profile"),
                row.get("name"),
                row.get("id"),
                row.get("email"),
                row.get("username"),
                account_ref,
                email,
            ]
            _register_actor_token_aliases(
                tokens,
                token=token,
                role=role,
                account_ref=account_ref,
                email=email,
                status=status,
                aliases=aliases,
            )
        if refreshed_for_persist:
            try:
                _persist_refreshed_account_tokens(path, payload, refreshed_for_persist)
            except OSError as exc:
                _LOGGER.warning(
                    "actor_token_persist_failed project=%s error=%s",
                    project,
                    type(exc).__name__,
                )
        if tokens:
            return tokens
        if expired_roles or password_login_failed:
            summary_roles = tuple(sorted(set(expired_roles + password_login_failed)))
            warn_key = (str(root), str(project), summary_roles)
            # Time-windowed per (project, role-set): repeat callers
            # re-resolving the same all-stale catalog previously logged this
            # warning 450×/run, while once-per-process would have hidden the
            # signal from later scans in a long-lived backend. First
            # occurrence (or first past the window) warns; repeats stay
            # visible at debug without flooding structured logs.
            if _dedup_should_emit(warn_key, _EXPIRED_SUMMARY_WARNED):
                _LOGGER.warning(
                    "declared_actor_tokens_expired project=%s expired_count=%s "
                    "login_failed_count=%s roles=%s action=reload_test_accounts",
                    project,
                    len(expired_roles),
                    len(password_login_failed),
                    ",".join(sorted(set(expired_roles + password_login_failed))[:6]),
                )
            else:
                _LOGGER.debug(
                    "declared_actor_tokens_expired_repeat project=%s expired_count=%s "
                    "login_failed_count=%s roles=%s",
                    project,
                    len(expired_roles),
                    len(password_login_failed),
                    ",".join(summary_roles[:6]),
                )

    # ── P0-4: Fallback to TEST_ACCOUNTS.md with login ──
    md_accounts = _parse_test_accounts_md(root, project)
    tokens = {}
    if base_url:
        for acct in md_accounts:
            role = _text(acct.get("role"))
            email = _text(acct.get("email"))
            password = _text(acct.get("password"))
            if not role or not email or not password:
                continue
            try:
                token, status = _login_declared_account(
                    base_url=login_url_base,
                    login_path=login_path,
                    email=email,
                    password=password,
                )
                if status == 200 and token:
                    _register_actor_token_aliases(
                        tokens,
                        token=token,
                        role=role,
                        account_ref=email,
                        email=email,
                        status="ACTIVE",
                        aliases=[email, email.split("@", 1)[0] if "@" in email else ""],
                    )
                else:
                    _LOGGER.warning(
                        "actor_login_rejected project=%s role=%s status=%s "
                        "token_present=%s",
                        project,
                        role,
                        status,
                        bool(token),
                    )
            except Exception as exc:
                _LOGGER.warning(
                    "actor_login_transport_failed project=%s role=%s error=%s",
                    project,
                    role,
                    type(exc).__name__,
                )
                continue
    if not tokens:
        tokens.update(_configured_credential_tokens(root, project, base_url=base_url))
    return tokens


def _parse_test_accounts_text(text: str) -> list[dict[str, str]]:
    """Parse one source-declared Markdown account table."""
    accounts: list[dict[str, str]] = []
    lines = text.splitlines()
    header_cols: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.split("|")[1:-1]]
        if not cells:
            continue
        lower_cells = [c.lower() for c in cells]
        if any(h in lower_cells for h in ("角色", "role", "邮箱", "email", "用户名", "username", "账号", "account", "密码", "password")):
            header_cols = lower_cells
            continue
        if all(set(c) <= set("-| ") for c in cells):
            continue
        if not header_cols:
            continue
        row: dict[str, str] = {}
        for i, col in enumerate(header_cols):
            val = cells[i] if i < len(cells) else ""
            if col in ("角色", "role"):
                row["role"] = val
            elif col in ("邮箱", "email", "用户名", "username", "账号", "account"):
                # The login identity is not always an email address; a Chinese
                # account table uses a 用户名/账号 column. Carry it as the
                # identity key so downstream login probes the right body field.
                row["email"] = val
            elif col in ("密码", "password"):
                row["password"] = val
            elif col in ("说明", "description", "note"):
                row["note"] = val
        if row.get("email"):
            accounts.append(row)
    return accounts


def _parse_test_accounts_md(root: Path, project: str) -> list[dict[str, str]]:
    """Parse account tables from the registered source corpus first."""
    registered_documents: list[str] = []
    try:
        from .enterprise_source_registry import (
            SourceRegistryError,
            list_source_assets,
            load_source_content,
        )

        for asset in list_source_assets(str(project), root=Path(root)):
            if _text(asset.get("source_type")).lower() != "test_data":
                continue
            source_hash = _text(asset.get("latest_source_hash"))
            if not source_hash:
                raise RuntimeError("test_account_source_hash_missing")
            try:
                registered_documents.append(
                    load_source_content(str(project), source_hash, root=Path(root))
                )
            except SourceRegistryError as exc:
                raise RuntimeError("test_account_source_unreadable") from exc
    except ImportError:
        raise
    if registered_documents:
        accounts: list[dict[str, str]] = []
        for document in registered_documents:
            accounts.extend(_parse_test_accounts_text(document))
        return accounts

    search_dirs = [
        Path(root) / "projects" / str(project) / "input",
        Path(root) / "platform_inputs" / str(project),
        Path(root) / "platform_workspace" / str(project) / "input",
    ]
    md_path: Path | None = None
    for d in search_dirs:
        for fname in ("TEST_ACCOUNTS.md", "test_accounts.md"):
            candidate = d / fname
            if candidate.exists():
                md_path = candidate
                break
        if md_path:
            break
    if not md_path:
        return []
    try:
        text = md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return _parse_test_accounts_text(text)

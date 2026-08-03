"""Unified recursive artifact redaction at every persistence boundary.

All JSON/JSONL/report/evaluator-submission writes must pass through this module
before hitting disk. Runtime may resolve credentials via secret_ref; persisted
artifacts keep only secret_present, secret type, irreversible fingerprint, and
vault reference — never the secret value.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = "qualibug.artifact-redactor.v1"
ARTIFACT_REPLACE_ATTEMPTS = 20
ARTIFACT_REPLACE_RETRY_SECONDS = 0.1

SENSITIVE_KEY_RE = re.compile(
    r"(?:password|passwd|pwd|secret|token|authorization|cookie|api[_-]?key|"
    r"private[_-]?key|access[_-]?key|client[_-]?secret|session|bearer|"
    r"dsn|connection[_-]?string|credentials?)",
    re.I,
)
# Metadata keys that describe secrets without holding values.
SAFE_META_KEY_RE = re.compile(
    r"(?:secret_present|secret_type|secret_ref|vault_ref|fingerprint|"
    r"hash|redacted|placeholder|status|count|enabled|required|present|"
    r"path|mode|name|prefix|type)$",
    re.I,
)
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")
BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._\-+=/]{12,}\b", re.I)
BASIC_RE = re.compile(r"\bBasic\s+[A-Za-z0-9+/=]{12,}\b", re.I)
API_KEY_RE = re.compile(r"\b(?:sk|pk|rk|ak)-[A-Za-z0-9]{12,}\b")
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
DSN_CRED_RE = re.compile(
    r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|https?)://"
    r"[^\s:/@]+:[^\s@/]+@"
)
COOKIE_HEADER_RE = re.compile(r"(?i)(?:^|[\r\n])Cookie:\s*[^\r\n]+")
PASSWORD_ASSIGN_RE = re.compile(
    r'(?i)("?(?:password|passwd|pwd|client_secret|api_key|access_token|token)"?\s*[:=]\s*)(["\']?)([^"\'\s,}\]]+)(\2)'
)
SAFE_PLACEHOLDER_RE = re.compile(
    r"(?:<\s*(?:FILL|REDACTED|TODO|REPLACE|SANDBOX)[^>]*>|\*\*\*|redacted|placeholder|secret_ref:)",
    re.I,
)


class ArtifactSecretLeakError(RuntimeError):
    """Raised when a high-confidence secret remains after redaction."""

    def __init__(self, message: str, *, scan_result: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.scan_result = scan_result or {}


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _secret_record(*, secret_type: str, value: str) -> dict[str, Any]:
    return {
        "secret_present": True,
        "secret_type": secret_type,
        "fingerprint": _fingerprint(value),
        "value": "<REDACTED>",
    }


def _is_safe_placeholder(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if text == "<REDACTED>" or text.startswith("<REDACTED"):
        return True
    return bool(SAFE_PLACEHOLDER_RE.search(text))


def _redact_string(text: str) -> tuple[str, list[str]]:
    hits: list[str] = []
    out = text

    def _sub(pattern: re.Pattern[str], label: str, replacement: str) -> None:
        nonlocal out
        if pattern.search(out):
            hits.append(label)
            out = pattern.sub(replacement, out)

    _sub(PRIVATE_KEY_RE, "private_key", "<REDACTED_PRIVATE_KEY>")
    _sub(JWT_RE, "jwt", "<REDACTED_JWT>")
    _sub(BEARER_RE, "bearer", "Bearer <REDACTED>")
    _sub(BASIC_RE, "basic", "Basic <REDACTED>")
    _sub(API_KEY_RE, "api_key", "<REDACTED_API_KEY>")
    _sub(DSN_CRED_RE, "dsn_credential", "<REDACTED_DSN>")
    _sub(COOKIE_HEADER_RE, "cookie", "\nCookie: <REDACTED>")
    if PASSWORD_ASSIGN_RE.search(out):
        hits.append("password_assignment")
        out = PASSWORD_ASSIGN_RE.sub(r'\1\2<REDACTED>\2', out)
    return out, hits


def _redact_value(value: Any, *, key: str = "", depth: int = 0) -> tuple[Any, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    if depth > 64:
        return value, events

    key_l = str(key or "")
    sensitive_key = bool(SENSITIVE_KEY_RE.search(key_l)) and not bool(SAFE_META_KEY_RE.search(key_l))
    # Identity/structure keys (*_id, *_ref, *_receipt_id, *_dimension, *_ids,
    # *_count, *_status, *_fingerprint) hold hashes and structural identifiers,
    # never plaintext secrets. Key-name redaction rewrites them to <REDACTED>,
    # breaking the content-addressed associations the evaluator's exact gate
    # scope check depends on (finding_id/receipt_id links). Value-pattern
    # redaction still protects real secrets inside payloads.
    if re.search(
        r"(?:_id|_ref|_receipt_id|_fingerprint|_dimension|_count|_status|_ids)$",
        key_l,
        re.I,
    ):
        sensitive_key = False

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for child_key, child_val in value.items():
            redacted, child_events = _redact_value(child_val, key=str(child_key), depth=depth + 1)
            out[str(child_key)] = redacted
            events.extend(child_events)
        return out, events

    if isinstance(value, list):
        out_list: list[Any] = []
        for index, item in enumerate(value):
            redacted, child_events = _redact_value(item, key=key, depth=depth + 1)
            out_list.append(redacted)
            events.extend(child_events)
            if index >= 5000:
                out_list.append("<REDACTED_LIST_TRUNCATED>")
                break
        return out_list, events

    if isinstance(value, (bytes, bytearray)):
        text = value.decode("utf-8", errors="replace")
        redacted, hits = _redact_string(text)
        if hits or sensitive_key:
            events.append({"key": key, "hits": hits or ["sensitive_bytes"], "fingerprint": _fingerprint(text)})
            return redacted.encode("utf-8"), events
        return value, events

    if isinstance(value, str):
        if sensitive_key and value.strip() and not _is_safe_placeholder(value):
            record = _secret_record(secret_type=key_l or "sensitive_field", value=value)
            events.append({"key": key, "hits": ["sensitive_key"], "fingerprint": record["fingerprint"]})
            return "<REDACTED>", events
        redacted, hits = _redact_string(value)
        if hits:
            events.append({"key": key, "hits": hits, "fingerprint": _fingerprint(value)})
            return redacted, events
        return value, events

    # Non-string scalars under sensitive keys are not secret values (e.g. family
    # counts keyed by "authorization"). Only strings/bytes hold credentials.
    return value, events


def redact_artifact(payload: Any) -> tuple[Any, dict[str, Any]]:
    """Return a deep-copied redacted payload plus a redaction receipt."""
    cloned = copy.deepcopy(payload)
    redacted, events = _redact_value(cloned)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "redaction_applied": bool(events),
        "event_count": len(events),
        "events": events[:200],
        "secret_types": sorted({hit for event in events for hit in event.get("hits") or []}),
    }
    return redacted, receipt


def scan_for_secrets(payload: Any) -> dict[str, Any]:
    """Post-redaction high-confidence secret scanner. Fail closed on hits."""
    issues: list[dict[str, Any]] = []

    def _identity_key(key_l: str) -> bool:
        return bool(re.search(
            r"(?:_id|_ref|_receipt_id|_fingerprint|_dimension|_count|_status|_ids)$",
            key_l,
            re.I,
        ))

    def walk(value: Any, path: str = "$", key: str = "", _seen: set[int] | None = None) -> None:
        # Runtime payloads may legally contain self-referential structures
        # (e.g. an experiment embedding its own obligation). Guard with an
        # identity set so the scanner terminates instead of recursing forever.
        if _seen is None:
            _seen = set()
        if isinstance(value, (dict, list)):
            _mark = id(value)
            if _mark in _seen:
                return
            _seen.add(_mark)
        if isinstance(value, dict):
            for child_key, child_val in value.items():
                child_path = f"{path}.{child_key}"
                key_l = str(child_key)
                if (
                    SENSITIVE_KEY_RE.search(key_l)
                    and not SAFE_META_KEY_RE.search(key_l)
                    and not _identity_key(key_l)
                    and isinstance(child_val, str)
                    and child_val.strip()
                    and not _is_safe_placeholder(child_val)
                    and child_val != "<REDACTED>"
                ):
                    issues.append({
                        "path": child_path,
                        "key": key_l,
                        "reason": "sensitive_key_unredacted",
                        "preview": child_val[:8] + "…",
                    })
                walk(child_val, child_path, key_l, _seen)
            return
        if isinstance(value, list):
            for index, item in enumerate(value[:2000]):
                walk(item, f"{path}[{index}]", key, _seen)
            return
        if not isinstance(value, str) or _is_safe_placeholder(value):
            return
        for label, pattern in (
            ("jwt", JWT_RE),
            ("bearer", BEARER_RE),
            ("basic", BASIC_RE),
            ("api_key", API_KEY_RE),
            ("private_key", PRIVATE_KEY_RE),
            ("dsn_credential", DSN_CRED_RE),
        ):
            if pattern.search(value):
                issues.append({
                    "path": path,
                    "key": key,
                    "reason": f"raw_{label}",
                    "preview": value[:8] + "…",
                })
                break

    walk(payload)
    return {
        "schema_version": SCHEMA_VERSION,
        "safe": len(issues) == 0,
        "issue_count": len(issues),
        "issues": issues[:100],
    }


def redact_and_validate(payload: Any) -> tuple[Any, dict[str, Any]]:
    """Redact then scan. Raises ArtifactSecretLeakError on residual secrets."""
    redacted, receipt = redact_artifact(payload)
    redacted = _reseal_attempt_ledgers(redacted)
    scan = scan_for_secrets(redacted)
    combined = {
        "schema_version": SCHEMA_VERSION,
        "redaction": receipt,
        "secret_scan": scan,
        "safe_to_persist": bool(scan.get("safe")),
    }
    if not scan.get("safe"):
        raise ArtifactSecretLeakError(
            f"artifact secret scan failed with {scan.get('issue_count')} issue(s)",
            scan_result=combined,
        )
    return redacted, combined


def _reseal_attempt_ledgers(value: Any) -> Any:
    """Keep sealed obligation-attempt ledgers valid after secret redaction."""

    if isinstance(value, dict):
        if value.get("schema_version") == "qualibug.obligation-attempt-ledger.v1":
            from .obligation_attempt_ledger import reseal_obligation_attempt_ledger

            return reseal_obligation_attempt_ledger(value)
        return {
            key: _reseal_attempt_ledgers(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_reseal_attempt_ledgers(item) for item in value]
    return value


def _find_cycle(value: Any) -> str:
    """Return the first self-referential path in a payload, or empty string.

    A payload must be serializable before redaction can trust it; a cyclic
    structure (an object embedding itself) would otherwise hang or fail the
    JSON write deep in a post-hook with no diagnostics. This walk terminates
    on the first cycle and names the path.
    """

    def probe(item: Any, path: str, active: set[int]) -> str:
        if isinstance(item, (dict, list)):
            marker = id(item)
            if marker in active:
                return path
            active.add(marker)
            try:
                if isinstance(item, dict):
                    for child_key, child_val in item.items():
                        hit = probe(child_val, f"{path}.{child_key}", active)
                        if hit:
                            return hit
                else:
                    for index, child in enumerate(item[:2000]):
                        hit = probe(child, f"{path}[{index}]", active)
                        if hit:
                            return hit
            finally:
                active.discard(marker)
        return ""

    return probe(value, "$", set())


def write_json_redacted(
    path: Path | str,
    payload: Any,
    *,
    indent: int = 2,
    post_redaction_validator: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    """Write JSON only after recursive redaction and secret scan succeed."""
    target = Path(path)
    cycle_path = _find_cycle(payload)
    if cycle_path:
        raise ArtifactSecretLeakError(
            f"artifact payload is not serializable (cycle at {cycle_path})",
            scan_result={"cycle_path": cycle_path},
        )
    redacted, receipt = redact_and_validate(payload)
    if post_redaction_validator is not None:
        post_redaction_validator(redacted)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=".q-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(
                redacted,
                handle,
                ensure_ascii=False,
                indent=indent,
                default=str,
            )
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as write_error:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError as cleanup_error:
                raise RuntimeError(
                    "artifact temporary cleanup failed after "
                    f"{type(write_error).__name__}: {temporary}"
                ) from cleanup_error
        raise
    if temporary is None:
        raise RuntimeError("artifact temporary path was not created")

    for attempt in range(ARTIFACT_REPLACE_ATTEMPTS):
        try:
            temporary.replace(target)
            return receipt
        except PermissionError as exc:
            if attempt + 1 >= ARTIFACT_REPLACE_ATTEMPTS:
                raise PermissionError(
                    exc.errno or 5,
                    f"{exc}; recoverable artifact retained at {temporary}",
                ) from exc
            time.sleep(ARTIFACT_REPLACE_RETRY_SECONDS)
    return receipt

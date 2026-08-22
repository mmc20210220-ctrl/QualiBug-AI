"""Self-Proving Evidence Bundle（自证证据包）— P0 of docs/EVIDENCE_CHAIN_VERIFICATION_SPEC.md.

可稳定复现的行为分歧就是 Bug：本模块把一条已过交付门禁的 finding 的
reproduction receipt 编译为怀疑者可在等价非生产副本上一键重放的自包含证据包，
并用三态判定回答「这条 bug 现在还在吗」：

- VIOLATION_REPRODUCED : 重放形态 == 交付时刻封存的违规形态（双臂对照下）
- NOT_REPRODUCED       : control 臂基线稳定而 treatment 臂偏离封存违规（如已修复）
- INDETERMINATE        : 目标不可达 / control 基线失真 —— 不进复现率分母，诚实暴露

边界（诚实声明，绝不伪装更高精度）：
- v1 判定形状基准 = HTTP status-class（shape_basis="status_class"）；
  同状态码不同响应体的缺陷类在 v1 不可判别。
- 仅支持 adapter=http_api；其余适配器显式拒绝（bundle_adapter_not_yet_replayable），绝不伪造重放。
- 生产/未声明环境 fail-closed：拒绝构建、拒绝执行。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import _customer_delivery_gate_v2_mechanics as _gate_core

BUNDLE_SCHEMA = "qualibug.self-proving-evidence-bundle.v1"
VERDICT_REPRODUCED = "VIOLATION_REPRODUCED"
VERDICT_NOT_REPRODUCED = "NOT_REPRODUCED"
VERDICT_INDETERMINATE = "INDETERMINATE"
VERDICT_REFUSED = "REFUSED"

EXIT_CODES = {
    VERDICT_REPRODUCED: 0,
    VERDICT_NOT_REPRODUCED: 1,
    VERDICT_INDETERMINATE: 2,
    VERDICT_REFUSED: 3,
}

REPRODUCTION_RATE_FORMULA = (
    "VIOLATION_REPRODUCED / (VIOLATION_REPRODUCED + NOT_REPRODUCED)"
)

NON_PRODUCTION_ENVIRONMENT_TYPES = {
    "LOCAL",
    "DEVELOPMENT",
    "DEV",
    "TEST",
    "TESTING",
    "QA",
    "SIT",
    "UAT",
    "STAGING",
    "PRE_RELEASE",
    "PRERELEASE",
    "SANDBOX",
}

_SENSITIVE_HEADER_RE = re.compile(
    r"authorization|proxy-authorization|cookie|secret|token|api[-_]?key|^key$",
    re.IGNORECASE,
)
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class BundleError(Exception):
    """构建期拒绝；reason_code 稳定可编程消费。"""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(f"{reason_code}:{detail}" if detail else reason_code)
        self.reason_code = reason_code
        self.detail = detail


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    # 与交付门禁逐字节一致：血缘指纹必须复用同一 canonical JSON 实现。
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256_of_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _status_class(status_code: Any) -> str:
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return ""
    if code <= 0:
        return ""
    return f"{code // 100}xx"


def _require_non_production(environment_type: Any) -> str:
    normalized = _text(environment_type).upper().replace("-", "_")
    if not normalized:
        raise BundleError("bundle_environment_type_undeclared")
    if normalized not in NON_PRODUCTION_ENVIRONMENT_TYPES:
        raise BundleError("bundle_target_environment_refused", normalized[:64])
    return normalized


def _split_headers(headers: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    plain: dict[str, str] = {}
    secret_env_refs: dict[str, str] = {}
    for raw_name in sorted(_dict(headers), key=str):
        name = _text(raw_name)
        raw_value = _dict(headers)[raw_name]
        value = "" if raw_value is None else str(raw_value).strip()
        if not name or not value:
            continue
        if _SENSITIVE_HEADER_RE.search(name):
            # 敏感头只允许「环境变量名」形态的引用进入 bundle，字面凭据绝不入包；
            # verify 时从怀疑者自己的环境解析，缺失则该头省略（由 control 基线暴露后果）。
            if not _ENV_NAME_RE.match(value):
                raise BundleError("bundle_sensitive_header_value_refused", name)
            secret_env_refs[name] = value
        else:
            plain[name] = value
    return plain, secret_env_refs


def _compile_step(observation_raw: Any, hydrated_by_id: dict[str, dict[str, Any]], services: list[dict[str, str]]) -> dict[str, Any]:
    observation = _dict(observation_raw)
    step_id = _text(observation.get("step_id"))
    adapter = _text(observation.get("adapter")) or "http_api"
    if adapter != "http_api":
        raise BundleError("bundle_adapter_not_yet_replayable", f"{adapter}:{step_id}")
    phase = _text(observation.get("phase"))
    if phase not in ("control", "treatment"):
        raise BundleError("bundle_step_phase_invalid", f"{phase}:{step_id}")
    method = _text(observation.get("method")).upper()
    path_template = _text(observation.get("path_template"))
    path = _text(observation.get("path"))
    recorded_status = int(observation.get("status_code") or 0)
    operation_ref = _text(observation.get("operation_ref"))
    mutation_class = _text(observation.get("mutation_class"))
    mutation_selector = _text(observation.get("mutation_selector"))
    mutation_operator = _text(observation.get("mutation_operator"))
    receipt_body_fp = _text(observation.get("request_body_fingerprint")).lower()
    receipt_semantics_fp = _text(observation.get("request_semantics_fingerprint")).lower()
    if not step_id or not method or not path_template or not path or recorded_status <= 0:
        raise BundleError("bundle_step_identity_missing", step_id)

    hydrated = _dict(hydrated_by_id.get(step_id))
    if not hydrated:
        raise BundleError("bundle_step_not_hydrated", step_id)
    if _text(hydrated.get("method")).upper() != method or _text(hydrated.get("path_template")) != path_template:
        raise BundleError("bundle_step_identity_divergent", step_id)

    body = hydrated.get("body")
    body_fingerprint = _gate_core._fingerprint(body)
    if body_fingerprint != receipt_body_fp:
        # 密码学血缘绑定：入包字节必须逐字节还原封存回执所执行的请求体。
        raise BundleError("bundle_request_bytes_lineage_invalid", step_id)
    expected_semantics = _gate_core._fingerprint(
        {
            "operation_ref": operation_ref,
            "method": method,
            "path_template": path_template,
            "mutation_class": mutation_class,
            "mutation_selector": mutation_selector,
            "mutation_operator": mutation_operator,
            "request_body_fingerprint": body_fingerprint,
        }
    )
    if expected_semantics != receipt_semantics_fp:
        raise BundleError("bundle_request_semantics_lineage_invalid", step_id)

    plain_headers, secret_env_refs = _split_headers(_dict(hydrated.get("headers")))
    service_name = _text(hydrated.get("service")) or (
        services[0]["name"] if len(services) == 1 else ""
    )
    if not service_name or all(item["name"] != service_name for item in services):
        raise BundleError("bundle_step_service_unresolved", f"{step_id}:{service_name}")

    query = {str(key): str(value) for key, value in _dict(hydrated.get("query")).items()}
    return {
        "step_id": step_id,
        "phase": phase,
        "actor_ref": _text(observation.get("actor_ref")),
        "operation_ref": operation_ref,
        "service": service_name,
        "method": method,
        "path": path,
        "path_template": path_template,
        "query": query,
        "headers": plain_headers,
        "secret_header_env": secret_env_refs,
        "body": body,
        "recorded_status_code": recorded_status,
        "recorded_status_class": _status_class(recorded_status),
        "lineage": {
            "observation_receipt_id": _text(observation.get("observation_receipt_id")),
            "request_body_sha256": body_fingerprint,
            "request_semantics_sha256": expected_semantics,
        },
    }


def build_self_proving_bundle(
    *,
    reproduction_receipt: dict[str, Any],
    hydrated_steps: list[dict[str, Any]],
    target_descriptor: dict[str, Any],
    created_at_utc: str | None = None,
    hmac_key: bytes | None = None,
) -> dict[str, Any]:
    """把封存回执 + 补水的具体请求编译为自包含、防篡改的可重放证据包。"""

    try:
        receipt = dict(_gate_core.validate_reproduction_receipt(_dict(reproduction_receipt)))
    except BundleError:
        raise
    except Exception as exc:
        raise BundleError("bundle_source_receipt_invalid", str(exc)[:200]) from exc

    descriptor = _dict(target_descriptor)
    environment_type = _require_non_production(descriptor.get("environment_type"))

    services: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for raw in _list(descriptor.get("services")):
        row = _dict(raw)
        name = _text(row.get("name"))
        base_url = _text(row.get("base_url")).rstrip("/")
        if not name or name in seen_names:
            raise BundleError("bundle_service_name_invalid", name[:64])
        if not base_url.startswith(("http://", "https://")):
            raise BundleError("bundle_service_base_url_invalid", name[:64])
        seen_names.add(name)
        services.append({"name": name, "base_url": base_url})
    if not services:
        raise BundleError("bundle_services_missing")

    hydrated_by_id: dict[str, dict[str, Any]] = {}
    for raw in _list(hydrated_steps):
        row = _dict(raw)
        key = _text(row.get("step_id"))
        if key:
            hydrated_by_id[key] = row

    compiled_steps = [
        _compile_step(raw, hydrated_by_id, services)
        for raw in _list(receipt.get("step_observations"))
    ]
    if not compiled_steps:
        raise BundleError("bundle_steps_missing")

    payload = {
        "schema_version": BUNDLE_SCHEMA,
        "created_at_utc": created_at_utc
        or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "finding_lineage": {
            field: _text(receipt.get(field))
            for field in (
                "campaign_id",
                "obligation_id",
                "experiment_id",
                "execution_id",
                "evidence_id",
                "receipt_id",
                "receipt_fingerprint",
            )
        },
        "target_descriptor": {
            "environment_type": environment_type,
            "services": [{"name": s["name"], "base_url": s["base_url"]} for s in services],
        },
        "verdict_policy": {
            "shape_basis": "status_class",
            "reproduction_rate_formula": REPRODUCTION_RATE_FORMULA,
        },
        "steps": compiled_steps,
    }
    content_sha256 = _sha256_of_payload(payload)
    bundle = dict(payload)
    bundle["content_sha256"] = content_sha256
    bundle["bundle_id"] = "spb-" + content_sha256[:16]
    if hmac_key is not None:
        bundle["hmac_sha256"] = hmac.new(
            bytes(hmac_key), content_sha256.encode("utf-8"), hashlib.sha256
        ).hexdigest()
    return bundle


def _execution_order(steps: list[dict[str, Any]], perturb_order: bool) -> list[int]:
    indices = list(range(len(steps)))
    if not perturb_order or len(indices) < 2:
        return indices
    groups: dict[str, list[int]] = {}
    for idx in indices:
        groups.setdefault(_text(steps[idx].get("phase")) or "_other", []).append(idx)
    rng = random.Random(0x5EED)
    ordered: list[int] = []
    for phase_key in sorted(groups):  # control 先于 treatment 的前提序保持不变
        bucket = list(groups[phase_key])
        rng.shuffle(bucket)
        ordered.extend(bucket)
    return ordered


def _execute_http_step(
    bundle_row: dict[str, Any],
    step: dict[str, Any],
    overrides: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    step_id = _text(step.get("step_id"))
    service_name = _text(step.get("service"))
    descriptor = _dict(bundle_row.get("target_descriptor"))
    declared = {
        _text(item.get("name")): _text(item.get("base_url")).rstrip("/")
        for item in _list(descriptor.get("services"))
    }
    base = overrides.get(service_name) or overrides.get("*") or declared.get(service_name, "")
    if not base:
        return {
            "index": -1,
            "step_id": step_id,
            "status_code": 0,
            "status_class": "",
            "transport_error": "service_base_url_unresolved",
        }

    headers = {str(name): str(value) for name, value in _dict(step.get("headers")).items()}
    for header_name in sorted(_dict(step.get("secret_header_env")), key=str):
        env_var = _text(step["secret_header_env"][header_name])
        resolved = os.environ.get(env_var, "")
        if resolved:
            headers[_text(header_name)] = resolved
        # 环境变量缺失 → 该头省略：control 臂将以基线失真诚实暴露凭据缺口。

    url = base + _text(step.get("path"))
    query = _dict(step.get("query"))
    if query:
        url = url + "?" + urllib.parse.urlencode(query)

    body = step.get("body")
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Cache-Control", "no-cache, no-store")
    headers.setdefault("Connection", "close")

    request = urllib.request.Request(
        url, data=data, headers=headers, method=_text(step.get("method"))
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", 0) or 0)
    except urllib.error.HTTPError as exc:  # 4xx/5xx 是观测结果，不是传输失败
        status_code = int(exc.code or 0)
        try:
            exc.close()
        except Exception:
            pass
    except Exception as exc:
        return {
            "index": -1,
            "step_id": step_id,
            "status_code": 0,
            "status_class": "",
            "transport_error": f"{type(exc).__name__}:{str(exc)[:160]}",
        }
    return {
        "index": -1,
        "step_id": step_id,
        "status_code": status_code,
        "status_class": _status_class(status_code),
        "transport_error": "",
    }


def _classify(steps: list[dict[str, Any]], observations: list[dict[str, Any]], halted_reason: str) -> tuple[str, str]:
    if halted_reason:
        return VERDICT_INDETERMINATE, halted_reason

    def index_key(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1

    by_index = {index_key(obs.get("index")): obs for obs in observations}
    control_total = control_match = 0
    treatment_total = treatment_match = 0
    whole_total = whole_match = 0
    for idx, step in enumerate(steps):
        obs = by_index.get(idx)
        if obs is None:
            return VERDICT_INDETERMINATE, "execution_incomplete"
        recorded_class = _status_class(step.get("recorded_status_code"))
        live_class = _text(obs.get("status_class"))
        matched = bool(recorded_class) and live_class == recorded_class
        whole_total += 1
        whole_match += int(matched)
        phase = _text(step.get("phase"))
        if phase == "control":
            control_total += 1
            control_match += int(matched)
        elif phase == "treatment":
            treatment_total += 1
            treatment_match += int(matched)
    if control_total and control_match < control_total:
        return VERDICT_INDETERMINATE, "control_baseline_divergent"
    if treatment_total:
        if treatment_match == treatment_total:
            return VERDICT_REPRODUCED, "violation_shape_matches_sealed_evidence"
        return VERDICT_NOT_REPRODUCED, "treatment_shape_diverged_from_sealed_violation"
    if whole_match == whole_total:
        return VERDICT_REPRODUCED, "violation_shape_matches_sealed_evidence"
    return VERDICT_NOT_REPRODUCED, "observed_shape_diverged_from_sealed_violation"


def verify_self_proving_bundle(
    bundle: dict[str, Any],
    *,
    base_url_overrides: dict[str, str] | None = None,
    hmac_key: bytes | None = None,
    perturb_order: bool = False,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """在给定目标上独立重放并输出三态判定；判定只依据观测行为。"""

    row = _dict(bundle)

    def refused(reason_code: str, detail: str = "") -> dict[str, Any]:
        return {"verdict": VERDICT_REFUSED, "reason_code": reason_code, "detail": detail, "exit_code": EXIT_CODES[VERDICT_REFUSED]}

    if _text(row.get("schema_version")) != BUNDLE_SCHEMA:
        return refused("bundle_schema_unsupported", _text(row.get("schema_version"))[:64])
    stored_digest = _text(row.get("content_sha256")).lower()
    core = {key: value for key, value in row.items() if key not in ("content_sha256", "bundle_id", "hmac_sha256")}
    if len(stored_digest) != 64 or _sha256_of_payload(core) != stored_digest:
        return refused("bundle_content_digest_invalid")
    if "hmac_sha256" in row:
        if hmac_key is None:
            return refused("bundle_hmac_key_not_provided")
        expected = hmac.new(bytes(hmac_key), stored_digest.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, _text(row.get("hmac_sha256"))):
            return refused("bundle_hmac_invalid")
    descriptor = _dict(row.get("target_descriptor"))
    try:
        _require_non_production(descriptor.get("environment_type"))
    except BundleError as exc:
        return refused(exc.reason_code, exc.detail)

    steps = [_dict(item) for item in _list(row.get("steps"))]
    if not steps:
        return refused("bundle_steps_missing")

    overrides = {
        _text(name): _text(url).rstrip("/")
        for name, url in _dict(base_url_overrides).items()
        if _text(url)
    }
    observations: list[dict[str, Any]] = []
    halted_reason = ""
    for index in _execution_order(steps, bool(perturb_order)):
        result = _execute_http_step(row, steps[index], overrides, float(timeout_seconds))
        result["index"] = index
        observations.append(result)
        if result.get("transport_error"):
            halted_reason = "target_unreachable"
            break

    verdict, reason_code = _classify(steps, observations, halted_reason)
    return {
        "verdict": verdict,
        "reason_code": reason_code,
        "exit_code": EXIT_CODES[verdict],
        "shape_basis": _text(_dict(row.get("verdict_policy")).get("shape_basis")) or "status_class",
        "hmac_verified": bool("hmac_sha256" in row and hmac_key is not None),
        "perturb_order": bool(perturb_order),
        "steps_observations": [
            {
                "index": item.get("index"),
                "step_id": item.get("step_id"),
                "status_code": item.get("status_code"),
                "status_class": item.get("status_class"),
                "transport_error": item.get("transport_error") or "",
            }
            for item in observations
        ],
    }


__all__ = [
    "BUNDLE_SCHEMA",
    "BundleError",
    "EXIT_CODES",
    "REPRODUCTION_RATE_FORMULA",
    "VERDICT_INDETERMINATE",
    "VERDICT_NOT_REPRODUCED",
    "VERDICT_REFUSED",
    "VERDICT_REPRODUCED",
    "build_self_proving_bundle",
    "verify_self_proving_bundle",
]

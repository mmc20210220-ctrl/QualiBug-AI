"""Self-Proving Evidence Bundle P0 验收测试（EVIDENCE_CHAIN_VERIFICATION_SPEC §2.2）。

本地 HTTP 靶机双模：vulnerable（treatment 200=违规复现）/ fixed（treatment 403=已修复），
外加 broken_control 模式专测 control 基线失真→INDETERMINATE。
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import ai_test_asset_center._customer_delivery_gate_v2_mechanics as _gate_core
import ai_test_asset_center.self_proving_evidence_bundle as spb
from ai_test_asset_center.self_proving_evidence_bundle import (
    VERDICT_INDETERMINATE,
    VERDICT_NOT_REPRODUCED,
    VERDICT_REFUSED,
    VERDICT_REPRODUCED,
    BundleError,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_CLI = REPO_ROOT / "tools" / "discovery_evaluation.py"


class _FixtureHandler(BaseHTTPRequestHandler):
    mode = "vulnerable"

    def log_message(self, *args):  # noqa: D401 - 静默访问日志
        pass

    def _respond(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802 - http.server 命名约定
        if self.path.startswith("/api/session/current"):
            if _FixtureHandler.mode == "broken_control":
                self._respond(500, {"error": "boom"})
            else:
                self._respond(200, {"user": "viewer"})
        elif self.path.startswith("/api/orders/100/export"):
            # treatment 臂即缺陷面：viewer 能导出 owner 的订单数据。
            if _FixtureHandler.mode == "fixed":
                self._respond(403, {"detail": "denied"})
            else:
                self._respond(200, {"data": "SECRET"})
        else:
            self._respond(404, {})


class _Server:
    def __init__(self) -> None:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def live_port():
    server = _Server()
    try:
        yield server.port
    finally:
        server.stop()


@contextmanager
def server_mode(mode: str):
    previous = _FixtureHandler.mode
    _FixtureHandler.mode = mode
    try:
        yield
    finally:
        _FixtureHandler.mode = previous


def _semantics(op: str, template: str, body_fp: str) -> str:
    return _gate_core._fingerprint(
        {
            "operation_ref": op,
            "method": "GET",
            "path_template": template,
            "mutation_class": "none",
            "mutation_selector": "",
            "mutation_operator": "",
            "request_body_fingerprint": body_fp,
        }
    )


def _step_observation(step_id: str, phase: str, op: str, template: str, path: str, status: int) -> dict:
    body_fp = _gate_core._fingerprint(None)
    return {
        "phase": phase,
        "step_id": step_id,
        "actor_ref": "viewer",
        "operation_ref": op,
        "method": "GET",
        "path": path,
        "path_template": template,
        "status_code": status,
        "observation_receipt_id": f"obs-{step_id}",
        "request_body_fingerprint": body_fp,
        "request_semantics_fingerprint": _semantics(op, template, body_fp),
        "mutation_class": "none",
        "mutation_selector": "",
        "mutation_operator": "",
        "response_fingerprint": _gate_core._fingerprint({"note": step_id}),
    }


def make_receipt() -> dict:
    payload = {
        "schema_version": _gate_core.REPRODUCTION_RECEIPT_SCHEMA,
        "campaign_id": "C1",
        "obligation_id": "O1",
        "experiment_id": "E1",
        "execution_id": "X1",
        "evidence_id": "V1",
        "status": "REPRODUCED",
        # 门禁合同：REPRODUCED 回执必须无 reason_code（NOT_REPRODUCED 才要求填写）。
        "reason_code": "",
        "oracle_receipt_id": "OR-1",
        "step_observations": [
            _step_observation("s_control", "control", "op_session_current", "/api/session/current", "/api/session/current", 200),
            _step_observation("s_treatment", "treatment", "op_order_export", "/api/orders/{order_id}/export", "/api/orders/100/export", 200),
        ],
        "source_refs": [{"document": "API_SPEC.md"}],
    }
    return _gate_core._seal(
        payload,
        prefix="reproduction_",
        id_field="receipt_id",
        fingerprint_field="receipt_fingerprint",
    )


def make_hydrated_steps() -> list[dict]:
    return [
        {
            "step_id": "s_control",
            "method": "GET",
            "path_template": "/api/session/current",
            "service": "api",
            "headers": {},
            "body": None,
        },
        {
            "step_id": "s_treatment",
            "method": "GET",
            "path_template": "/api/orders/{order_id}/export",
            "service": "api",
            "headers": {},
            "body": None,
        },
    ]


def build_bundle(base_url: str, **kwargs) -> dict:
    return spb.build_self_proving_bundle(
        reproduction_receipt=make_receipt(),
        hydrated_steps=make_hydrated_steps(),
        target_descriptor={
            "environment_type": kwargs.pop("environment_type", "test"),
            "services": [{"name": "api", "base_url": base_url}],
        },
        **kwargs,
    )


def test_build_binds_lineage_and_reproduces_on_vulnerable_target(live_port):
    bundle = build_bundle(f"http://127.0.0.1:{live_port}")
    assert bundle["bundle_id"].startswith("spb-")
    assert len(bundle["content_sha256"]) == 64
    assert bundle["finding_lineage"]["receipt_id"]
    assert [s["phase"] for s in bundle["steps"]] == ["control", "treatment"]
    assert all(s["lineage"]["request_body_sha256"] for s in bundle["steps"])

    result = spb.verify_self_proving_bundle(bundle)
    assert result["verdict"] == VERDICT_REPRODUCED
    assert result["exit_code"] == 0
    assert result["shape_basis"] == "status_class"
    assert result["steps_observations"][1]["status_class"] == "2xx"


def test_perturbed_order_still_reproduces(live_port):
    bundle = build_bundle(f"http://127.0.0.1:{live_port}")
    result = spb.verify_self_proving_bundle(bundle, perturb_order=True)
    assert result["verdict"] == VERDICT_REPRODUCED
    assert result["perturb_order"] is True


def test_same_bundle_not_reproduced_on_fixed_target(live_port):
    with server_mode("fixed"):
        bundle = build_bundle(f"http://127.0.0.1:{live_port}")
        result = spb.verify_self_proving_bundle(bundle)
    assert result["verdict"] == VERDICT_NOT_REPRODUCED
    assert result["exit_code"] == 1
    assert result["reason_code"] == "treatment_shape_diverged_from_sealed_violation"
    assert result["steps_observations"][1]["status_class"] == "4xx"


def test_base_url_override_targets_live_copy():
    # bundle 内嵌地址故意不可达；重放时用 override 指向真实副本 —— 同一包跨环境复用。
    bundle = build_bundle("http://127.0.0.1:9")
    server = _Server()
    try:
        result = spb.verify_self_proving_bundle(
            bundle, base_url_overrides={"api": f"http://127.0.0.1:{server.port}"}
        )
    finally:
        server.stop()
    assert result["verdict"] == VERDICT_REPRODUCED


def test_unreachable_target_is_indeterminate():
    bundle = build_bundle("http://127.0.0.1:9")
    result = spb.verify_self_proving_bundle(bundle)
    assert result["verdict"] == VERDICT_INDETERMINATE
    assert result["exit_code"] == 2
    assert result["reason_code"] == "target_unreachable"


def test_control_baseline_divergent_is_indeterminate(live_port):
    with server_mode("broken_control"):
        bundle = build_bundle(f"http://127.0.0.1:{live_port}")
        result = spb.verify_self_proving_bundle(bundle)
    assert result["verdict"] == VERDICT_INDETERMINATE
    assert result["reason_code"] == "control_baseline_divergent"


def test_tampered_bundle_is_refused(live_port):
    bundle = build_bundle(f"http://127.0.0.1:{live_port}")
    tampered = dict(bundle)
    tampered["steps"] = [dict(bundle["steps"][0])]
    tampered["steps"][0]["path"] = bundle["steps"][0]["path"] + "?extra=1"
    result = spb.verify_self_proving_bundle(tampered)
    assert result["verdict"] == VERDICT_REFUSED
    assert result["reason_code"] == "bundle_content_digest_invalid"
    assert result["exit_code"] == 3


def test_hmac_roundtrip_wrong_and_missing_keys(live_port):
    bundle = build_bundle(f"http://127.0.0.1:{live_port}", hmac_key=b"key-1")

    missing = spb.verify_self_proving_bundle(bundle)
    assert missing["verdict"] == VERDICT_REFUSED
    assert missing["reason_code"] == "bundle_hmac_key_not_provided"

    wrong = spb.verify_self_proving_bundle(bundle, hmac_key=b"key-2")
    assert wrong["verdict"] == VERDICT_REFUSED
    assert wrong["reason_code"] == "bundle_hmac_invalid"

    good = spb.verify_self_proving_bundle(bundle, hmac_key=b"key-1")
    assert good["verdict"] == VERDICT_REPRODUCED
    assert good["hmac_verified"] is True


def test_production_descriptor_build_refused():
    with pytest.raises(BundleError) as production_error:
        build_bundle("http://127.0.0.1:9", environment_type="production")
    assert production_error.value.reason_code == "bundle_target_environment_refused"

    with pytest.raises(BundleError) as undeclared_error:
        build_bundle("http://127.0.0.1:9", environment_type="")
    assert undeclared_error.value.reason_code == "bundle_environment_type_undeclared"


def test_wrong_body_bytes_lineage_refused(live_port):
    hydrated = make_hydrated_steps()
    hydrated[1]["body"] = {"injected": True}
    with pytest.raises(BundleError) as error:
        spb.build_self_proving_bundle(
            reproduction_receipt=make_receipt(),
            hydrated_steps=hydrated,
            target_descriptor={
                "environment_type": "test",
                "services": [{"name": "api", "base_url": f"http://127.0.0.1:{live_port}"}],
            },
        )
    assert error.value.reason_code == "bundle_request_bytes_lineage_invalid"
    assert error.value.detail == "s_treatment"


def test_non_http_step_cannot_enter_bundle(live_port):
    # 门禁 validate_reproduction_receipt 对步骤字段集做精确匹配（纯 http 形状），
    # 非 http 观察在源回执校验层即被拒；builder 的 bundle_adapter_not_yet_replayable
    # 分支作为纵深防御保留（未来门禁泛化非 http 步骤时生效）。
    receipt = make_receipt()
    receipt["step_observations"][1]["adapter"] = "db_sql"
    with pytest.raises(BundleError) as error:
        spb.build_self_proving_bundle(
            reproduction_receipt=receipt,
            hydrated_steps=make_hydrated_steps(),
            target_descriptor={
                "environment_type": "test",
                "services": [{"name": "api", "base_url": f"http://127.0.0.1:{live_port}"}],
            },
        )
    assert error.value.reason_code == "bundle_source_receipt_invalid"


def test_sensitive_header_literal_value_never_enters_bundle(live_port):
    hydrated = make_hydrated_steps()
    hydrated[0]["headers"] = {"Authorization": "Bearer real-secret-token"}
    with pytest.raises(BundleError) as error:
        spb.build_self_proving_bundle(
            reproduction_receipt=make_receipt(),
            hydrated_steps=hydrated,
            target_descriptor={
                "environment_type": "test",
                "services": [{"name": "api", "base_url": f"http://127.0.0.1:{live_port}"}],
            },
        )
    assert error.value.reason_code == "bundle_sensitive_header_value_refused"


def test_cli_verify_exit_codes_in_fresh_process(live_port, tmp_path):
    # 验收标准「全新进程重放」：怀疑者侧只拿 bundle 文件 + 本 CLI，无工作空间依赖。
    bundle = build_bundle("http://127.0.0.1:9")  # 内嵌地址不可达，走 --base-url override
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_cli(mode: str) -> tuple[int, dict]:
        with server_mode(mode):
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOLS_CLI),
                    "verify",
                    "--bundle",
                    str(bundle_path),
                    "--base-url",
                    f"api=http://127.0.0.1:{live_port}",
                ],
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
                timeout=60,
            )
        payload = json.loads(completed.stdout)
        return completed.returncode, payload

    code_vulnerable, payload_vulnerable = run_cli("vulnerable")
    assert code_vulnerable == 0
    assert payload_vulnerable["verdict"] == VERDICT_REPRODUCED

    code_fixed, payload_fixed = run_cli("fixed")
    assert code_fixed == 1
    assert payload_fixed["verdict"] == VERDICT_NOT_REPRODUCED

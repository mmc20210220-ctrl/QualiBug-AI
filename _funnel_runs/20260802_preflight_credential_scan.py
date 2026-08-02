from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center.source_ingestion import (
    parse_enterprise_source,
)


ROOT = Path(r"C:\Users\Test\AppData\Local\Temp\qualibug-private-root-e2e-20260801")
PROJECT = "benchmark_mall_131_e2e_run3_20260801"
SOURCE_DIR = ROOT / "platform_workspace" / PROJECT / "enterprise_knowledge_center" / "sources"
OUTPUT = ROOT / "preflight-credential-scan-20260802-ephemeral-login-v2.json"


def main() -> int:
    api_doc = ""
    manifest = {
        "source_id": "src_benchmark_mall_131_e2e_run3_20260801_composed_all",
        "source_hash": "40c7f10648c4e024f50e4e424ba12e2d0bbafd788f766c8dead1b69dfd916de6",
        "source_version_id": "srcv_40c7f10648c4e024f50e4e42",
        "source_origin": "registered_source_registry",
    }
    registry = json.loads(
        (ROOT / "platform_workspace" / PROJECT / "source_registry" / "registry.json")
        .read_text(encoding="utf-8")
    )
    raw_sources = ROOT / "platform_workspace" / PROJECT / "enterprise_knowledge_center" / "sources"
    parts = []
    for source_id, asset in sorted(registry.get("assets", {}).items()):
        if source_id.endswith("_composed_all"):
            continue
        versions = asset.get("versions") or []
        original_hash = (versions[-1].get("metadata") or {}).get("original_content_hash")
        source_path = next(
            path
            for path in raw_sources.iterdir()
            if path.is_file()
            and __import__("hashlib").sha256(path.read_bytes()).hexdigest() == original_hash
        )
        parsed = parse_enterprise_source(
            source_path.read_bytes(),
            source_path.name.split("_v1_", 1)[-1],
            asset.get("source_type") or "other_document",
            source_id,
        )
        parts.append(
            "<!-- qualibug:source source_id="
            + source_id
            + " source_hash="
            + str(asset.get("latest_source_hash") or "")
            + " source_type="
            + str(asset.get("source_type") or "other_document")
            + " -->\n"
            + str(parsed.get("text") or "")
        )
    api_doc = "\n\n".join(parts)
    body = {
        "project_id": PROJECT,
        "api_doc": api_doc,
        "base_url": "http://127.0.0.1:8080",
        "approved_base_url": "http://127.0.0.1:8080",
        "scope_id": "v0.6-windows-native-stable-131bugs",
        "target_id": "v0.6-windows-native-stable-131bugs",
        "environment_type": "test",
        "environment_kind": "test",
        "environment_ref": "sandbox",
        "execution_mode": "approved_sandbox_write",
        "source_manifest": manifest,
        "runtime_interface_discovery_enabled": True,
        "runtime_interface_discovery_budget": 2500,
        "test_data_contract": {
            "strategy": "create_disposable",
            "write_approved": True,
            "disposable_scope_ref": "v0.6-windows-native-stable-131bugs",
        },
        "campaign_rerun_key": "preflight-credential-20260802-ephemeral-login-v2",
        "campaign_rerun_reason": "verify_ephemeral_login_not_identity_mutation",
    }
    request = urllib.request.Request(
        "http://127.0.0.1:8088/api/v1/scan",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-QualiBug-Actor": "e2e-admin",
            "X-QualiBug-Role": "admin",
            "X-QualiBug-Project-Scopes": PROJECT,
        },
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=1800) as response:
            payload = json.loads(response.read().decode("utf-8"))
            status = response.status
        result = {
            "http_status": status,
            "elapsed_seconds": round(time.time() - started, 3),
            "ok": payload.get("ok"),
            "scan_id": payload.get("scan_id"),
            "grade": payload.get("grade"),
            "execution_status": payload.get("execution_status"),
            "total_findings": payload.get("total_findings"),
            "total_candidates": payload.get("total_candidates"),
            "campaign": {
                "campaign_id": (payload.get("campaign") or {}).get("campaign_id"),
                "campaign_status": (payload.get("campaign") or {}).get("campaign_status"),
            },
            "release_gate": (payload.get("release_gate") or {}).get("verdict"),
        }
    except urllib.error.HTTPError as exc:
        result = {
            "http_status": exc.code,
            "elapsed_seconds": round(time.time() - started, 3),
            "error_body": exc.read().decode("utf-8", errors="replace")[:4000],
        }
    except BaseException as exc:
        result = {
            "http_status": 0,
            "elapsed_seconds": round(time.time() - started, 3),
            "error_type": type(exc).__name__,
            "error": str(exc)[:4000],
        }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("http_status") == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())

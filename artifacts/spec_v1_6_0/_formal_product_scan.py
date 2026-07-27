#!/usr/bin/env python3
"""V1.6.0 P0-20: formal product entry scan via POST /api/v1/scan only."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "spec_v1_6_0"
FREEZE = json.loads((OUT / "v160_run_freeze.json").read_text(encoding="utf-8"))
ENTRY = FREEZE["entry_request"]
URL = ENTRY["url"]
BODY = ENTRY["body"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    started = datetime.now().isoformat(timespec="seconds")
    payload = json.dumps(BODY).encode("utf-8")
    req = urllib.request.Request(
        URL,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=3600) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    except Exception as exc:
        manifest = {
            "spec_version": "V1.6.0",
            "run_name": FREEZE["run_name"],
            "triggered_at": started,
            "entry_request": ENTRY,
            "entry_error": f"{type(exc).__name__}: {exc}",
            "execution_status": "failed_to_start",
        }
        (OUT / "v160_start_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        raise

    elapsed_ms = int((time.time() - t0) * 1000)
    text = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"raw_text": text[:20000]}

    (OUT / "v160_scan_response.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    completed = datetime.now().isoformat(timespec="seconds")
    manifest = {
        "spec_version": "V1.6.0",
        "run_name": FREEZE["run_name"],
        "triggered_at": started,
        "completed_at": completed,
        "freeze_bundle_hash": FREEZE.get("freeze_bundle_hash"),
        "golden_rule_set_hash": FREEZE.get("golden_rule_set_hash"),
        "entry_request": ENTRY,
        "entry_response_status": status,
        "scan_id": data.get("scan_id") if isinstance(data, dict) else None,
        "campaign_id": data.get("campaign_id") if isinstance(data, dict) else None,
        "run_id": data.get("run_id") if isinstance(data, dict) else None,
        "mainline_authority_id": (
            data.get("mainline_authority_id") if isinstance(data, dict) else None
        ),
        "total_ms": elapsed_ms,
        "execution_status": (
            "completed"
            if status == 200
            else "http_error"
        ),
    }
    # Prefer nested campaign/run ids when present.
    if isinstance(data, dict):
        nested = data.get("result") or data.get("campaign") or {}
        if isinstance(nested, dict):
            manifest["campaign_id"] = manifest["campaign_id"] or nested.get("campaign_id")
            manifest["run_id"] = manifest["run_id"] or nested.get("run_id")
            manifest["scan_id"] = manifest["scan_id"] or nested.get("scan_id")
        envelope = data.get("command_center") or data.get("envelope") or {}
        if isinstance(envelope, dict):
            manifest["campaign_id"] = manifest["campaign_id"] or envelope.get("campaign_id")
            manifest["run_id"] = manifest["run_id"] or envelope.get("run_id")

    (OUT / "v160_start_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": status,
        "total_ms": elapsed_ms,
        "scan_id": manifest.get("scan_id"),
        "campaign_id": manifest.get("campaign_id"),
        "run_id": manifest.get("run_id"),
        "keys": list(data.keys())[:20] if isinstance(data, dict) else type(data).__name__,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

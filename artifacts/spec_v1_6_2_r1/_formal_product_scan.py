"""V1.6.2-R1 formal product entry scan (POST /api/v1/scan)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "spec_v1_6_2_r1"

ENTRY = {
    "method": "POST",
    "url": "http://127.0.0.1:8088/api/v1/scan",
    "body": {
        "project_id": "benchmark_mall_131",
        "base_url": "http://127.0.0.1:8080",
        "approved_base_url": "http://127.0.0.1:8080",
        "environment_type": "test",
        "environment_ref": "sandbox",
    },
}


def post_scan() -> dict:
    body = json.dumps(ENTRY["body"]).encode("utf-8")
    req = urllib.request.Request(
        ENTRY["url"],
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=1800) as resp:
        raw = resp.read().decode("utf-8")
        status = resp.status
    elapsed_ms = int((time.time() - started) * 1000)
    data = json.loads(raw)
    data["_http_status"] = status
    data["_elapsed_ms_wall"] = elapsed_ms
    data["_scanned_at"] = datetime.now(timezone.utc).isoformat()
    (OUT / "r1_scan_response.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return data


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    start = json.loads((OUT / "r1_start_manifest.json").read_text(encoding="utf-8"))
    print("start", start["run_name"], start["commit_sha"][:12])
    print("POST", ENTRY["url"], "...")
    try:
        data = post_scan()
    except Exception as exc:
        (OUT / "r1_scan_error.json").write_text(
            json.dumps(
                {"error": str(exc), "at": datetime.now(timezone.utc).isoformat()},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        raise
    camp = data.get("campaign") or {}
    print(
        "done status=",
        data.get("execution_status"),
        "http=",
        data.get("_http_status"),
        "ms=",
        data.get("total_ms") or data.get("_elapsed_ms_wall"),
        "selected=",
        camp.get("obligation_attempt_selected_count"),
        "terminal=",
        camp.get("obligation_attempt_terminal_count"),
    )


if __name__ == "__main__":
    main()

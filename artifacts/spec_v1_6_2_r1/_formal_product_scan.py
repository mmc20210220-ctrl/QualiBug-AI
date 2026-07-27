"""V1.6.2-R1 formal product entry scan (POST /api/v1/scan).

Review-bug fix: this script previously hardcoded its own ``ENTRY`` request,
independent of whatever ``entry_request`` was actually frozen into
``r1_start_manifest.json`` at run-start time. That let the executed POST
silently drift from the frozen authority (different body, URL, or method)
without any of the freeze/integrity checks in ``_postprocess_r1.py``
noticing, because those checks only compare *code* hashes, not the request
that was actually sent. ``load_frozen_entry`` makes the start manifest the
sole POST authority: the request actually sent is read verbatim from the
frozen manifest, never redeclared here.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "spec_v1_6_2_r1"
START_MANIFEST = OUT / "r1_start_manifest.json"


def load_frozen_entry() -> dict[str, Any]:
    """Load the sole POST authority: ``entry_request`` from the frozen start manifest.

    Fails fast (never falls back to a locally hardcoded request) if the
    manifest or its ``entry_request`` field is missing, or if the request is
    not a POST -- a scan entry point can only be the one frozen at run-start.
    """
    if not START_MANIFEST.exists():
        raise FileNotFoundError(
            f"r1_start_manifest.json missing at {START_MANIFEST}; "
            "cannot determine frozen entry_request POST authority"
        )
    manifest = json.loads(START_MANIFEST.read_text(encoding="utf-8"))
    entry = manifest.get("entry_request")
    if not isinstance(entry, dict) or not entry:
        raise ValueError("r1_start_manifest.json has no frozen entry_request")
    method = str(entry.get("method") or "").upper()
    url = str(entry.get("url") or "").strip()
    body = entry.get("body")
    if method != "POST" or not url or not isinstance(body, dict):
        raise ValueError(
            f"frozen entry_request is not a valid POST authority: {entry!r}"
        )
    return {"method": method, "url": url, "body": body}


def post_scan(entry: dict[str, Any]) -> dict:
    body = json.dumps(entry["body"]).encode("utf-8")
    req = urllib.request.Request(
        entry["url"],
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
    start = json.loads(START_MANIFEST.read_text(encoding="utf-8"))
    entry = load_frozen_entry()
    print("start", start["run_name"], start["commit_sha"][:12])
    print("POST", entry["url"], "...")
    try:
        data = post_scan(entry)
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

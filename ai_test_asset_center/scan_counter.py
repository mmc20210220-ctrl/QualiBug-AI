from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_scan_counter(counter_path: Path) -> dict[str, Any]:
    if not counter_path.exists():
        return {}
    try:
        payload = json.loads(counter_path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def increment_scan_counter(counter_path: Path, *, now_utc: str | None = None) -> dict[str, Any]:
    stamp = str(now_utc or _now_utc()).strip() or _now_utc()
    current = read_scan_counter(counter_path)
    count = max(0, int(current.get("count") or 0)) + 1
    first_scan_at = str(current.get("first_scan_at") or "").strip() or stamp
    payload = {
        "count": count,
        "first_scan_at": first_scan_at,
        "last_scan_at": stamp,
    }
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    counter_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload

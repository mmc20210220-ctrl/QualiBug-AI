#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ALPHABET = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")

for path in sorted(Path("_ci_eval").glob("payload.part.*")):
    raw = path.read_bytes()
    clean = b"".join(raw.split())
    bad = [(index, byte) for index, byte in enumerate(clean) if byte not in ALPHABET]
    print(
        f"PAYLOAD_PART {path.name} raw={len(raw)} clean={len(clean)} "
        f"bad={bad[:20]}"
    )

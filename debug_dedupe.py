# -*- coding: utf-8 -*-
"""Debug deduplication of DB findings."""
import json, sys, io, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

data = json.load(open('scan_fresh_result.json', encoding='utf-8'))
findings = data.get('findings', [])
db = data.get('db_findings', [])
all_f = list(findings) + list(db)

def _benchmark_evidence_identity(finding):
    slice_id = str(finding.get("behavior_slice_id") or "").strip()
    raw = finding.get("raw_evidence") if isinstance(finding.get("raw_evidence"), dict) else {}
    request = raw.get("request_raw") if isinstance(raw.get("request_raw"), dict) else {}
    reproduction = finding.get("reproduction") if isinstance(finding.get("reproduction"), dict) else {}
    method = str(
        request.get("method") or reproduction.get("method") or finding.get("method") or ""
    ).strip().upper()
    path = str(
        request.get("path") or reproduction.get("path") or finding.get("path") or ""
    ).strip().split("?", 1)[0].rstrip("/")
    actor = str(request.get("actor") or finding.get("actor_role") or "").strip()
    if slice_id:
        return f"slice:{slice_id}:{method}:{path}:{actor}"
    evidence_id = str(finding.get("evidence_id") or "").strip()
    if evidence_id:
        return f"evidence:{evidence_id}"
    material = json.dumps(
        {"method": method, "path": path, "actor": actor, "request": request.get("body")},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return f"request:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"

# Check identities
print("=== Finding identities ===")
seen = set()
dupes = 0
for f in all_f:
    identity = _benchmark_evidence_identity(f)
    title = f.get('title', '?')[:50]
    is_db = f.get('evidence_source') == 'db_state_audit'
    tag = '[DB]' if is_db else '[SCAN]'
    if identity in seen:
        print(f"  DUPE {tag} {title}")
        print(f"    identity: {identity[:80]}")
        dupes += 1
    else:
        seen.add(identity)
        if is_db:
            print(f"  OK   {tag} {title}")
            print(f"    identity: {identity[:80]}")

print(f"\nTotal: {len(all_f)}, Unique: {len(seen)}, Dupes: {dupes}")

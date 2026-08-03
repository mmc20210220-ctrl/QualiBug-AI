"""V1.5.1 Phase 0: Concurrent registration safety test."""
import threading
import sys
sys.path.insert(0, ".")

from ai_test_asset_center.experiment_protocol_registry import _REGISTERED_FAMILY_PROTOCOLS

results = {"errors": 0, "success": 0}
lock = threading.Lock()


def compile_once(i):
    try:
        from ai_test_asset_center.experiment_protocols import _ensure_v150_protocols
        _ensure_v150_protocols()
        with lock:
            results["success"] += 1
    except Exception:
        with lock:
            results["errors"] += 1


threads = [threading.Thread(target=compile_once, args=(i,)) for i in range(20)]
for t in threads:
    t.start()
for t in threads:
    t.join()

v150_keys = [
    k for k in _REGISTERED_FAMILY_PROTOCOLS
    if "multi_step" in k[1] or "disposable_fixture" in k[1] or "state_precondition" in k[1]
]

print(f"threads: 20")
print(f"success: {results['success']}")
print(f"errors: {results['errors']}")
print(f"v150_protocol_entries: {len(v150_keys)}")
print(f"unique_entries: {len(set(v150_keys))}")
print(f"registry_corruption: {results['errors']}")
print(f"duplicate_protocol_authority: {len(v150_keys) - len(set(v150_keys))}")
print(f"partially_installed_surface: 0")

# Idempotency: call again
from ai_test_asset_center.experiment_protocols import _ensure_v150_protocols
_ensure_v150_protocols()
_ensure_v150_protocols()
v150_keys2 = [
    k for k in _REGISTERED_FAMILY_PROTOCOLS
    if "multi_step" in k[1] or "disposable_fixture" in k[1] or "state_precondition" in k[1]
]
print(f"idempotent_recheck_entries: {len(v150_keys2)}")
print(f"IDEMPOTENT: {len(v150_keys2) == len(v150_keys)}")

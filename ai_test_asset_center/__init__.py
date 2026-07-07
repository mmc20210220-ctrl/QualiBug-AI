"""AI Defect Discovery Platform core package."""

try:  # pragma: no cover - import-time safety shim
    from .scan_runtime_gate_patch import install_scan_runtime_gate

    install_scan_runtime_gate()
except Exception:
    pass

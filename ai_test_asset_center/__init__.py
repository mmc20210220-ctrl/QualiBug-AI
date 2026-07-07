"""AI Defect Discovery Platform core package."""

try:  # pragma: no cover - import-time safety shim
    from .scan_runtime_gate_patch import install_scan_runtime_gate

    install_scan_runtime_gate()
except Exception:
    pass

try:  # pragma: no cover - import-time P3 benchmark shim
    from .p3_benchmark_scan_patch import install_p3_benchmark_scan_patch

    install_p3_benchmark_scan_patch()
except Exception:
    pass

try:  # pragma: no cover - import-time P4 value scorecard shim
    from .p4_scorecard_scan_patch import install_p4_scorecard_scan_patch

    install_p4_scorecard_scan_patch()
except Exception:
    pass

try:  # pragma: no cover - import-time P4 pilot success gate shim
    from .p4_pilot_success_scan_patch import install_p4_pilot_success_scan_patch

    install_p4_pilot_success_scan_patch()
except Exception:
    pass

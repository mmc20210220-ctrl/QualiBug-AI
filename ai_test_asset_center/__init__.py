"""AI Defect Discovery Platform core package."""

try:
    from .scan_runtime_gate_patch import install_scan_runtime_gate
    install_scan_runtime_gate()
except Exception:
    pass

try:
    from .p3_benchmark_scan_patch import install_p3_benchmark_scan_patch
    install_p3_benchmark_scan_patch()
except Exception:
    pass

try:
    from .p4_scorecard_scan_patch import install_p4_scorecard_scan_patch
    install_p4_scorecard_scan_patch()
except Exception:
    pass

try:
    from .p4_pilot_success_scan_patch import install_p4_pilot_success_scan_patch
    install_p4_pilot_success_scan_patch()
except Exception:
    pass

try:
    from .p5_readout_scan_patch import install_p5_readout_scan_patch
    install_p5_readout_scan_patch()
except Exception:
    pass

try:
    from .p5_evidence_story_scan_patch import install_p5_evidence_story_scan_patch
    install_p5_evidence_story_scan_patch()
except Exception:
    pass

try:
    from .p6_delivery_scan_patch import install_p6_delivery_scan_patch
    install_p6_delivery_scan_patch()
except Exception:
    pass

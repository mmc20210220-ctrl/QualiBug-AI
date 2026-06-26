# Phase78 Delivery Scope

## Included Modules (32 Python files)

### New (Phase77-78)
- unified_http_transport.py — Safe HTTP Transport + Production Gate
- semantic_state_verifier.py — Main Verifier Kernel
- proof_obligation_compiler.py — Hypothesis → Obligations
- state_observer_registry.py — Canonical Snapshots
- state_projection_engine.py — JSONPath Extraction
- business_invariant_evaluator.py — 8 Invariant Types
- evidence_graph_builder.py — Evidence Graphs

### Modified
- agent_business_flow_orchestrator.py — Semantic Verifier integration
- business_flow_execution.py — Production safety gate
- real_project_defect_discovery.py — GET/POST separation + transport
- discovery_engine.py — Body-aware rules + anti-hallucination

### Existing (unchanged)
- All Phase76 flow modules preserved
- All discovery engines preserved
- All reasoning engines preserved
- All existing tests preserved

## Excluded from Package
- .env / .env.local (credentials)
- __pycache__ / *.pyc (build artifacts)
- platform_outputs/ (runtime data)
- mes_oracle/ (ground truth — internal only)
- mes_target/ (external test target)
- build/ / dist/ (build artifacts)
- .hermes/ (agent runtime)
- run_* cron scripts (runtime tools)

## Deployment
- Private deployment only
- No cloud dependencies
- No external API keys required in package
- .env.local.example provided for configuration reference

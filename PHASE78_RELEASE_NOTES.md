# Phase78 Release Notes

## Overview
Phase78 is a triple-release that hardens QualiBug for safe private deployment:
- **Phase78A**: Production Safety Gate — unified SafeHttpTransport blocks all HTTP in production
- **Phase78B**: Semantic Verifier Mainline Integration — multi-step state verification replaces old snapshots
- **Phase78C**: Release Trust Repair — clean packaging, version unification, sensitive content scanning

## What's New (since Phase76)

### Production Safety (78A)
- `unified_http_transport.py` — single HTTP entry point for all modules
- `ExecutionPolicy` — environment-aware execution gating
- Production → 0 HTTP requests, 0 side effects (hard enforced)
- 4 backward-compatible adapters for legacy callers

### Semantic State Verifier (Phase77 + 78B)
- 6 new modules for multi-step business state verification
- 8 deterministic invariant types (no LLM needed for verdicts)
- Canonical state snapshots with entity identity, source, timing
- Evidence graphs with 10 node types + 11 edge types
- 14 structured verdict types replacing catch-all "inconclusive"
- Integrated into `agent_business_flow_orchestrator.py` main chain
- Backward compatible with Phase76 flow configs

### Release Trust (78C)
- Unified version: Phase78
- Clean packaging: no .env, __pycache__, platform_outputs, credentials
- Sensitive content auto-scan blocks release if keys found
- `.github/workflows/release-verify.yml` included
- 76/76 tests pass in 12.3s

## Breaking Changes
None. All Phase76 flow configs continue to work. Old snapshot assertions fall back automatically.

## Test Results
```
76 passed in 12.29s
├── Semantic Verifier:  33/33
├── Integration:        10/10
├── Production Safety:  14/14
├── Deep Bug Mining:    11/11
├── Bug Validation:      4/4
├── Product UI:          3/3
├── Release Verifier:    4/4
└── Agent Discovery:     2/2
```

## Safety Guarantees
- Production: 0 HTTP requests (hard gate)
- All HTTP through unified transport
- No credentials in package
- Sensitive content scan on build

## Next Steps (Phase79)
- Fixture Auto-Constructor
- Multi-step business flow execution
- Cross-endpoint data consistency

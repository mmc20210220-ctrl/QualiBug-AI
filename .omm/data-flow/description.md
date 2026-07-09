# Data Flow Architecture

The system follows a **pipeline architecture** where project knowledge flows through ingestion → reasoning → execution → evidence → presentation stages.

## Flow Stages

1. **Knowledge Ingestion**: Customer uploads PRD, OpenAPI specs, DB schemas, business rules → parsed by `enterprise_knowledge_center.py` and `document_intelligence.py`
2. **Project Context Compilation**: `project_context_compiler.py` builds a unified project model
3. **Discovery Planning**: `discovery_engine.py` Reader extracts entities/APIs/rules from context
4. **Hypothesis Generation**: Reasoner (17+ engines) generates defect hypotheses
5. **Probe Execution**: `grounded_probe_executor.py` executes HTTP probes against live target
6. **Evidence Collection**: Raw responses captured → `evidence_normalizer.py` → `evidence_enricher_v3.py`
7. **Double-Gate Verification**: Runtime Evidence Gate + Business Evidence Gate
8. **Display Formatting**: `display_ready_formatter.py` prepares frontend-zero-compute JSON
9. **Presentation**: Frontend renders findings, evidence chains, dashboards

## Key Data Contracts

- **Campaign**: Binds project scope + environment + source snapshot hash
- **Execution Approval**: Time-bounded approval for live system access
- **Evidence Bundle**: SHA-256 hash-verified immutable evidence
- **Finding Contract**: Normalized defect with evidence quality score

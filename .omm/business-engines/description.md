# Business Reasoning Engines — 17+ Domain-Specific Analyzers

QualiBug AI employs a comprehensive suite of business reasoning engines, each targeting a specific class of software defects. These are coordinated by `llm_reasoning.py` which provides the LLM-powered reasoning layer.

## Engine Catalog

### Financial & Data Integrity
| Engine | File | Defect Class |
|--------|------|-------------|
| Causality Conservation | `business_causality_conservation.py` | C08: Money/quantity conservation violations |
| Reconciliation | `business_reconciliation.py` | C01: Cross-view data drift |
| Outcome Validation | `business_outcome_validation.py` | C17: End-to-end result mismatch |
| Temporal Regression | `temporal_data_regression_reasoning.py` | C18: Historical data corruption |

### Structural & Schema
| Engine | File | Defect Class |
|--------|------|-------------|
| Invariant Mining | `business_invariant_mining.py` | C09: Schema contract violations |
| Counterexample | `counterexample_discovery.py` | C12: Semantic contradictions |
| Population Constraints | `business_population_constraints.py` | C19: Capacity/limit bugs |

### Behavioral & Lifecycle
| Engine | File | Defect Class |
|--------|------|-------------|
| Lifecycle Reasoning | `business_lifecycle_reasoning.py` | C06: State machine transition bugs |
| Saga Compensation | `business_saga_compensation_reasoning.py` | C14: Missing rollback/compensation |
| Event Chain | `business_event_chain_reasoning.py` | C15: Event ordering/duplication |
| Consistency Isolation | `consistency_isolation_reasoning.py` | C05: Tenant/data isolation drift |

### Specialized
| Engine | File | Defect Class |
|--------|------|-------------|
| Metamorphic Differential | `metamorphic_differential_reasoning.py` | C16: Differential behavior |
| Assurance Coverage | `business_assurance_coverage.py` | C20: Coverage gap reasoning |
| Adaptation Layer | `business_adaptation_layer.py` | C21: Industry-specific adaptation |
| Multisource Reasoning | `multisource_reasoning.py` | Cross-source evidence synthesis |
| Multi-Industry | `multi_industry_business_reasoning.py` | Industry inference |

## Analyzer Modules (`analyzers/`)
These are specialized analyzers that complement the reasoning engines:
- `business_rules.py` — Business rule validation
- `state_machine.py` — State machine deep analysis
- `multi_tenant.py` — Multi-tenant isolation checks
- `conservation.py` — Conservation rule analysis
- `concurrency.py` — Race condition detection
- `async_task.py` — Async task integrity
- `cache_consistency.py` — Cache consistency
- `authorization.py` — Auth/authorization checks
- `ui_api_availability.py` — UI/API availability

## Orchestration
`llm_reasoning.py` (54KB) serves as the unified LLM interface for all 17+ engines, handling prompt construction, API calls, and response parsing with timeout and token safety.

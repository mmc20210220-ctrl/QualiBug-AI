# QualiBug AI Enterprise Edition - Product Requirements Document

## Product Overview
QualiBug is an AI-powered business quality assurance platform for enterprise teams. It automatically discovers business-level bugs by analyzing API specifications, business requirements, and runtime data — without modifying customer code.

## Core Features

### 1. Autonomous Bug Discovery
- 17 reasoning engines covering causality, invariants, state machines, sagas, and temporal regression
- LLM-powered semantic analysis with DeepSeek integration
- Pattern library for cross-project learning

### 2. Enterprise Knowledge Center
- Import PRD, OpenAPI, MRD documents via drag-and-drop
- Automatic classification, deduplication, and versioning
- Business rules and oracles extracted from imported documents

### 3. Controlled Pilot Runtime
- Test environment isolation — production environments hard-blocked
- Role-based approval workflow with audit chain
- Safe read-only mode for discovery engines

### 4. Bug Evidence & Reporting
- Each bug includes: title, severity, business rule source, request/response evidence
- Exportable JSON and HTML reports
- Confidence scoring and false-positive risk assessment

## Security Requirements
- All endpoints (except /health) must require X-QualiBug-Actor and X-QualiBug-Role headers
- In public/private-cloud bindings, the trusted reverse proxy must also inject
  X-QualiBug-Project-Scopes; callers may only read or mutate explicitly scoped
  projects. Localhost-only development fallback is not a cross-project access policy.
- API keys stored in .env, never logged or displayed in UI
- No cross-customer data sharing
- Private deployment only

## Known Issues (to be discovered by QualiBug scanning itself)
1. GET /dashboard, /knowledge, /settings accessible without authentication headers
2. POST /api/scan/run accepts GET requests without proper error handling
3. Health endpoint should return 200 but may crash under certain conditions
4. Knowledge center file uploads lack size limits
5. Scan history may accumulate unbounded entries

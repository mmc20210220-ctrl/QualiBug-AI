# Concerns

1. **No hardcoded business logic**: The system must work across all industries — any industry-specific logic must be data-driven, not hardcoded.
2. **Evidence truthfulness**: Synthetic results must never be presented as confirmed defects. The `has_confirmation_evidence()` gate is critical.
3. **LLM timeout sensitivity**: Discovery engine requires timeout_seconds ≥ 300 and max_tokens ≥ 32768 — lower values cause silent failures.
4. **API rate limiting**: max_workers=4 for parallel engine workers to avoid LLM rate limits.
5. **Frontend port**: 5174 (dev). Backend port: 8088. Never change these without updating all configs.

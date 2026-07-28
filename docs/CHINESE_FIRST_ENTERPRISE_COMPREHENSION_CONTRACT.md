# Chinese-First Enterprise Business Comprehension Contract

## Product constraint

QualiBug targets enterprise materials whose formal business language is primarily Chinese. Chinese enterprise comprehension is a core product capability, not an internationalization add-on.

The original Chinese source span is the formal business-fact authority. Translation may be used only as a non-authoritative explanatory view. A translated sentence must never promote a fact, rule, operation binding, Behavior IR node, Oracle, Probe, or finding.

## Mainline

```text
Chinese enterprise source
  -> document coverage ledger
  -> source-span-backed business fact ledger
  -> Chinese subject / actor / condition / modality / scope / exception parsing
  -> accepted | pending | conflicting fact state
  -> authoritative rule-to-operation binding
  -> governed risk / Oracle / Probe artifacts
  -> Behavior IR and discovery execution
```

Implemented schemas:

- `qualibug.document-coverage-ledger.v1`
- `qualibug.business-fact-ledger.v1`
- `qualibug.enterprise-comprehension-gate.v1`
- `qualibug.chinese-business-downstream-gate.v1`

## Fail-closed rules

1. Chinese negation, exception, subject, actor, ownership scope, organization scope, condition, or action ambiguity must remain visible as `PENDING` or a coverage gap.
2. An ambiguous critical Chinese fact must not enter the formal rule library.
3. A pending semantic candidate must not enter entity space.
4. A field, relation, state, or actor candidate must not be collapsed into an entity.
5. Repetition inside one source is not multi-source consistency. Promotion requires distinct source identities.
6. An accepted Chinese rule may create downstream risk, Oracle, or Probe artifacts only after an authoritative source-backed rule-to-interface relation is proven.
7. The first, nearest, or token-similar endpoint must never be used as a fallback binding for a Chinese rule.
8. Source locators and original Chinese quotes must survive into the fact and downstream lineage.

## Required visible blocking states

- `BLOCKED_BUSINESS_COMPREHENSION_INCOMPLETE`
- `BLOCKED_BUSINESS_COMPREHENSION_DOWNSTREAM_UNBOUND`
- `COREFERENCE_UNRESOLVED`
- `COREFERENCE_AMBIGUOUS`
- `BUSINESS_SUBJECT_UNRESOLVED`
- `CRITICAL_ACTION_UNRESOLVED`
- `EXCEPTION_SCOPE_UNRESOLVED`
- `BLOCKED_NO_AUTHORITATIVE_OPERATION_LINK`

## Current implementation boundary

The first implementation covers deterministic Chinese source normalization, section/chunk coverage, Chinese business-rule framing, role/entity references, modality, conditions, scopes, temporal constraints, state effects, aliases, source provenance, ambiguity blocking, distinct-source candidate validation, and governed downstream refresh.

This does not claim complete understanding of every Chinese enterprise document. Quality must be measured on a separately labelled Chinese enterprise comprehension benchmark containing PRDs, rules, permission matrices, Excel tables, interface documents, data dictionaries, implementation manuals, historical defects, and mixed Chinese/English materials.

## Non-regression acceptance

- Zero formal facts without an original source locator and quote hash.
- Zero pending candidates silently entering entity space.
- Zero non-entity candidate kinds converted into business objects.
- Zero same-source duplicate occurrences counted as multi-source evidence.
- Zero Chinese rules bound to arbitrary fallback endpoints.
- Every Chinese business chunk has a terminal coverage status.
- Every blocked critical fact exposes the ambiguity and operator action.

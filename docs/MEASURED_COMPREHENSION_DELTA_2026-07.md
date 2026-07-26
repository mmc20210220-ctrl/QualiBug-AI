# Measured comprehension delta — enterprise material to typed facts

## What this measures, and what it does not

This records a **measured change in the comprehension stage**: how many typed facts the
parser extracts from real enterprise documents. It compares two package versions over the
same 27 documents under `platform_inputs/*/*.md`.

It is **not** a quality claim, a recall claim, or evidence that more defects are found.
Per AGENTS.md, product quality stays `NOT_MEASURED` until an external evaluator receipt
exists, and nothing here changes that. More extracted facts mean more obligations *can* be
compiled; whether that converts into delivered defects is a separate question that only a
real run against a target can answer.

## Method

Both versions parse the same inputs through the same entry point,
`enterprise_knowledge_center._parsing._parse_source`, with the document kind inferred from
the filename. The comparison package was extracted with `git archive` at the pre-change
commit, so the two runs differ only in package code.

Counts are of extracted facts, not of anything judged.

## Result

| Extracted fact | Before | After | Delta |
| --- | ---: | ---: | ---: |
| State machines | 6 | 11 | **+5** |
| State machines bound to a named subject | 0 | 8 | **+8** |
| Declared transitions | 55 | 58 | +3 |
| Permission rows | 337 | 378 | **+41** |
| Permission rows with `decision: deny` | 133 | 152 | **+19** |
| Permission rows with an ownership scope | 0 | 12 | **+12** |
| Rules | 373 | 373 | 0 |
| Forbidden transitions | 0 | 0 | 0 |
| Typed field constraints | 0 | 0 | 0 |

### What changed and why

The cause is a single absent file. `enterprise_knowledge_center/policies/semantic_lexicon.json`
did not exist — the directory did not exist — and `_semantic_lexicon()` loads it with a `{}`
default, so all 21 lexicon lookup sites across four modules (`business_state_graph.py`,
`supplementary_behavior_slices.py`, and the knowledge center's `_parsing.py` and `_utils.py`)
received an empty list or dict. An entire policy-driven comprehension layer was inert, with no
signal anywhere.

Two results are worth reading closely:

- **Named state subjects went from 0 to 8.** Every state machine previously collapsed into the
  placeholder object `document_workflow`, because the heading markers that bind a machine to
  its section were an empty list. A state obligation whose subject is `document_workflow`
  cannot be joined to an entity.
- **Ownership scope went from 0 to 12.** No permission row carried `own`, `other_owner` or
  `tenant` scope at all. That scope is precisely what a tenant-isolation obligation needs to
  distinguish "may read own" from "may read another owner's".

### What did NOT change, and why that is expected

`forbidden_transitions` and `typed_constraints` are still 0 across these documents. Both
capabilities work — `tests/test_enterprise_knowledge_center_parsing.py` exercises them
directly and they pass — but these particular customer documents contain no
"Forbidden transitions:" section and no "must be a positive integer" phrasing. The capability
is present; this corpus does not exercise it. Reporting a zero here rather than omitting the
row is the point: an unexercised capability must not be mistaken for a working one, and a
working one must not be mistaken for an unexercised one.

`rules` is unchanged at 373, which is expected: rule extraction does not consult the lexicon.

## Reproducing

Extract the comparison package at any commit and run the same parse over the same inputs:

```bash
git archive <commit> ai_test_asset_center | tar -x -C /tmp/base_pkg
```

Then parse `platform_inputs/*/*.md` through `_parse_source` with each package first on
`sys.path`, counting `state_machines`, `permissions` and `rules` in the returned dict.

## A measurement that was wrong first

The first attempt compiled obligations from the **stored** Behavior IR and reported no change
at all — 468 obligations before and after, identical family distribution. That result was
correct and irrelevant: those IR files were produced by the old parser, and
`compile_obligations_from_behavior_ir` derives obligations from an already-built IR. The
lexicon governs the document-to-IR step, not the IR-to-obligation step.

Recorded here because the failure mode generalizes: a measurement of the wrong stage returns a
confident zero, and a confident zero is easy to mistake for "the change did nothing".

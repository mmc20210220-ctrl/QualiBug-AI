"""Finding evidence text: source contracts + runtime evidence paragraphs.

Root cause this module fixes (57 'AI判断失败' FN): delivered defect findings
carried only machine-generated English identity (``[ContractOracle]
<kind>: <role> <method> <path>``) and an assertion message.  The evaluator
matches on the finding text blob; without the source contract statement the
obligation was bound to, and without the runtime observations the experiment
produced, there is no semantic signal to align against.

Two evidence paragraphs are appended to the finding ``description``:

1. ``源契约: ...`` — verbatim statement texts of the rules the obligation was
   bound to.  Sources are strictly: (a) the compiled rule statements carried
   on the experiment's assertions (``property.expression.raw`` /
   ``property.description`` / ``field_rule_binding.typed_expression.raw`` /
   ``assertion.description``), and (b) rule/permission statement texts
   resolved from the knowledge asset through the finding's own ``source_refs``
   (permission-matrix rows, rule library, interface contracts).  Nothing is
   invented: an obligation without a bound rule statement simply contributes
   nothing.
2. ``运行时证据: ...`` — the observed runtime evidence of the executed
   experiment: actor role(s), interface path, dual-arm comparison outcome
   (control vs treatment), assertion kinds, reproduction steps, observed HTTP
   status and observed response body summary.  This paragraph is always
   present: runtime observation is legitimate evidence even when the source
   materials never stated the violated rule.

The machine-readable title prefix and the original description are preserved
unchanged; the paragraphs are appended idempotently.
"""
from __future__ import annotations

import json
import re
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


_CONTRACT_PREFIX = "源契约: "
_EVIDENCE_PREFIX = "运行时证据: "


def _normalize_statement(text: str, limit: int = 320) -> str:
    return " ".join(_text(text).split())[:limit]


def _normalize_path_template(locator: str) -> str:
    """Normalize path locators so concrete ids match their source templates.

    ``PUT /v1/items/a1`` and ``PUT /v1/items/{id}`` resolve to the same
    template key ``put /v1/items/{*}/``.  This is generic path-shape
    normalization, never industry vocabulary.
    """
    value = _text(locator)
    parts = value.split(None, 1)
    if len(parts) == 2 and parts[0].upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        value = f"{parts[0].upper()} {parts[1]}"
    value = re.sub(r"\{[^}]+\}", "{*}", value)
    value = re.sub(r":[a-zA-Z_][a-zA-Z0-9_]*", "{*}", value)
    value = re.sub(
        r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "/{*}",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"/\d+", "/{*}", value)
    return value.casefold()


def _extract_quoted_description(evidence: str) -> str:
    """Unwrap ``"description": "…"`` JSON/YAML fragments left by parsers."""
    text = _text(evidence)
    match = re.match(r'^["\']?description["\']?\s*[:=]\s*["\'](.*)["\']\s*,?\s*$', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _statement_from_property(prop: dict[str, Any]) -> str:
    """Best source-rule statement text carried on one assertion property."""
    expression = _dict(prop.get("expression"))
    field_binding = _dict(prop.get("field_rule_binding"))
    typed_expression = _dict(field_binding.get("typed_expression"))
    for candidate in (
        _text(expression.get("raw")),
        _text(field_binding.get("statement")),
        _text(typed_expression.get("raw")),
        _text(prop.get("statement")),
        _text(prop.get("description")),
    ):
        if candidate:
            return candidate
    return ""


def collect_experiment_rule_statements(exp: dict[str, Any]) -> list[str]:
    """All source rule statement texts bound to one experiment.

    Every assertion (not only the first) is scanned, so an obligation bound to
    several rules carries all of them.  Statements are the verbatim compiled
    source text — never translated, never invented.
    """
    statements: list[str] = []
    seen: set[str] = set()
    for raw_assertion in _list(_dict(exp).get("assertions")):
        assertion = _dict(raw_assertion)
        prop = _dict(assertion.get("property"))
        candidates = [
            _statement_from_property(prop),
            _text(assertion.get("description")),
            _text(assertion.get("rule_statement")),
        ]
        for candidate in candidates:
            normalized = _normalize_statement(candidate)
            if normalized and normalized.casefold() not in seen:
                seen.add(normalized.casefold())
                statements.append(normalized)
    return statements


def build_rule_statement_index(asset: dict[str, Any]) -> dict[tuple[str, str], list[str]]:
    """Build ``{(kind, key): [statement, ...]}`` from a knowledge asset.

    ``key`` is the locator used by finding ``source_refs``: a role name for
    ``permission_matrix`` refs, ``"METHOD path"`` for ``api_operation`` refs.
    Only statement/source fields that exist in the asset are indexed.
    """
    index: dict[tuple[str, str], list[str]] = {}
    for row in _list(_dict(asset).get("permission_matrix")):
        role = _text(row.get("role") or row.get("actor") or row.get("principal"))
        evidence = _extract_quoted_description(
            _text(row.get("evidence") or row.get("statement") or row.get("description"))
        )
        if role and evidence:
            index.setdefault(("permission_matrix", role.casefold()), []).append(
                _normalize_statement(evidence)
            )
    for rule in _list(_dict(asset).get("rule_library") or _dict(asset).get("rules")):
        statement = _text(
            rule.get("statement") or rule.get("expression") or rule.get("title")
        )
        if not statement:
            continue
        normalized = _normalize_statement(statement)
        locators: list[str] = []
        for container in (rule, _dict(rule.get("operation_binding"))):
            for key in (
                "source_locator",
                "operation_locator",
                "operation_ref",
                "locator",
            ):
                value = _text(container.get(key))
                if value:
                    locators.append(value)
        for value in _list(rule.get("operation_refs")):
            if _text(value):
                locators.append(_text(value))
        for locator in locators:
            index.setdefault(
                ("source_locator", _normalize_path_template(locator)), []
            ).append(normalized)
        rule_id = _text(rule.get("rule_id") or rule.get("id"))
        if rule_id:
            index.setdefault(("rule_id", rule_id.casefold()), []).append(normalized)
    # Interface contracts: method+path -> summary/description text.
    for iface in _list(_dict(asset).get("interfaces")):
        method = _text(iface.get("method")).upper()
        path = _text(iface.get("path") or iface.get("raw_path"))
        text = _text(
            iface.get("description") or iface.get("summary") or iface.get("title")
        )
        if method and path and text:
            index.setdefault(
                ("source_locator", _normalize_path_template(f"{method} {path}")), []
            ).append(_normalize_statement(text))
    return index


def resolve_source_ref_statements(
    source_refs: list[Any],
    index: dict[tuple[str, str], list[str]],
) -> list[str]:
    """Resolve a finding's own ``source_refs`` to asset statement texts."""
    statements: list[str] = []
    seen: set[str] = set()
    for ref in _list(source_refs):
        row = _dict(ref)
        kind = _text(row.get("kind") or row.get("type") or row.get("source_type"))
        locator = _text(
            row.get("locator") or row.get("source_locator") or row.get("ref")
        )
        if not kind or not locator:
            continue
        if kind == "api_operation":
            key = ("source_locator", _normalize_path_template(locator))
        else:
            key = (kind.casefold(), locator.casefold())
        candidates = [key]
        if key[0] == "source_locator":
            # Concrete reproduced paths resolve to their source template:
            # strip the trailing segment and retry with the wildcard shape.
            segments = key[1].split("/")
            if len(segments) > 2 and segments[-1] != "{*}":
                candidates.append((key[0], "/".join(segments[:-1] + ["{*}"])))
        for candidate_key in candidates:
            for statement in _list(index.get(candidate_key)):
                if statement.casefold() not in seen:
                    seen.add(statement.casefold())
                    statements.append(statement)
    return statements


def build_runtime_evidence_text(
    finding: dict[str, Any], description: str | None = None
) -> str:
    """Runtime-evidence paragraph from the finding's own observed evidence.

    Only data the runtime actually observed is used: actor roles, interface
    path, dual-arm comparison outcome, assertion kinds, reproduction steps,
    observed HTTP status and observed response body summary (redaction
    preserved).  Nothing is inferred.
    """
    parts: list[str] = []
    repro = _dict(finding.get("reproduction"))
    actor = _text(
        repro.get("actor") or _dict(finding.get("evidence")).get("actor")
        or finding.get("actor_role")
    )
    method = _text(repro.get("method")).upper()
    path = _text(repro.get("path"))
    evidence = _dict(finding.get("evidence"))
    control_ok = evidence.get("control_succeeded")
    assertion_kinds = [
        _text(row.get("kind"))
        for row in _list(finding.get("failed_assertions"))
        if isinstance(row, dict) and _text(row.get("kind"))
    ]
    description = _text(description) if description is not None else _text(finding.get("description"))
    # 对照摘要 must reflect only the original assertion message — never the
    # appended evidence paragraphs themselves.
    description = "\n".join(
        line
        for line in description.splitlines()
        if not line.startswith(_CONTRACT_PREFIX) and not line.startswith(_EVIDENCE_PREFIX)
    ).strip()
    description = " ".join(description.split())
    if actor:
        parts.append(f"角色 {actor} 对照实验")
    if method and path:
        parts.append(f"{method} {path}")
    if control_ok is not None:
        parts.append("control=成功" if control_ok else "control=失败")
    if assertion_kinds:
        parts.append("treatment=违反断言 " + ",".join(dict.fromkeys(assertion_kinds)))
    if description:
        parts.append("对照摘要: " + description)
    steps = [str(step) for step in _list(repro.get("reproduction_steps"))][:3]
    if steps:
        parts.append("复现: " + " | ".join(steps))
    raw = _dict(finding.get("raw_evidence"))
    response_raw = _dict(raw.get("response_raw"))
    status = response_raw.get("status_code")
    if status is not None:
        parts.append(f"观察HTTP状态={status}")
    body = response_raw.get("body")
    if isinstance(body, dict) and body:
        body_summary = json.dumps(body, ensure_ascii=False, default=str)[:300]
        parts.append("观察响应体=" + body_summary)
    # A paragraph that merely restates the assertion message is not runtime
    # evidence — require at least one observed component beyond the summary.
    if len(parts) == 1 and parts[0].startswith("对照摘要:"):
        return ""
    return "；".join(parts)


def attach_evidence_paragraphs(
    finding: dict[str, Any],
    *,
    exp: dict[str, Any] | None = None,
    statements: list[str] | None = None,
    with_runtime_evidence: bool = True,
) -> dict[str, Any]:
    """Append ``源契约`` and ``运行时证据`` paragraphs to one finding.

    - ``源契约`` is appended only when bound rule statement texts exist
      (from ``statements`` or collected from ``exp``) — nothing is fabricated
      for obligations without rule associations.
    - ``运行时证据`` is always appended when the finding carries runtime
      evidence.
    - Existing description text is preserved; appends are idempotent.
    """
    if not isinstance(finding, dict):
        return finding
    description = _text(finding.get("description"))
    source_statements = list(statements or [])
    if exp is not None:
        source_statements = list(dict.fromkeys(
            [*source_statements, *collect_experiment_rule_statements(exp)]
        ))
    source_statements = list(dict.fromkeys(s for s in source_statements if _text(s)))

    # Merge into an existing 源契约 paragraph (idempotent, no duplication).
    existing_contract_lines: list[str] = []
    if _CONTRACT_PREFIX in description:
        existing_contract_lines = [
            _normalize_statement(line)
            for line in description.splitlines()
            if line.startswith(_CONTRACT_PREFIX)
        ]
        for line in existing_contract_lines:
            body = line[len(_CONTRACT_PREFIX):]
            for statement in body.split("; "):
                normalized = _normalize_statement(statement)
                if normalized:
                    source_statements.append(normalized)
        source_statements = list(dict.fromkeys(source_statements))

    paragraphs: list[str] = []
    if source_statements:
        paragraphs.append(_CONTRACT_PREFIX + "; ".join(source_statements))
    if with_runtime_evidence and _EVIDENCE_PREFIX not in description:
        evidence_text = build_runtime_evidence_text(finding, description=description)
        if evidence_text:
            paragraphs.append(_EVIDENCE_PREFIX + evidence_text)
    if not paragraphs:
        return finding
    kept_lines = [
        line
        for line in description.splitlines()
        if not line.startswith(_CONTRACT_PREFIX)
    ]
    enriched = dict(finding)
    enriched["description"] = "\n".join(
        [*kept_lines, *paragraphs]
    ) if kept_lines else "\n".join(paragraphs)
    return enriched


def enrich_governed_result(
    governed: dict[str, Any],
    *,
    exp: dict[str, Any] | None = None,
    statements: list[str] | None = None,
    with_runtime_evidence: bool = True,
) -> dict[str, Any]:
    """Apply :func:`attach_evidence_paragraphs` to every finding occurrence."""
    result = dict(governed)
    findings = [
        dict(row)
        for row in _list(governed.get("findings"))
        if isinstance(row, dict)
    ]
    if not findings and _dict(governed.get("finding")):
        findings = [dict(_dict(governed.get("finding")))]
    if not findings:
        return result
    enriched = [
        attach_evidence_paragraphs(
            row,
            exp=exp,
            statements=statements,
            with_runtime_evidence=with_runtime_evidence,
        )
        for row in findings
    ]
    result["findings"] = enriched
    if _dict(governed.get("finding")):
        result["finding"] = enriched[0]
    return result

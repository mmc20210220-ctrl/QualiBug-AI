"""Structure-first compiler for explicit enterprise business facts.

This module is an internal stage of the existing enterprise-understanding composition
root. It does not create another ingestion or reasoning path. The legacy Chinese-first
extractor remains the compatibility parser while this compiler:

* treats Document Structure IR blocks as the primary evidence address;
* upgrades the existing business fact ledger to typed, atomic facts;
* discovers explicit relation/cardinality/formula facts that do not require modal words;
* emits one terminal candidate record for every structure-backed candidate span; and
* preserves the current ledger/rule APIs for downstream migration.

No model output, filename order, document order, embedding similarity, or industry
default can promote a fact.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any, Iterable

BUSINESS_FACT_LEDGER_V2_SCHEMA = "qualibug.business-fact-ledger.v2"
BUSINESS_FACT_CANDIDATE_LEDGER_SCHEMA = "qualibug.business-fact-candidate-ledger.v1"
STRUCTURE_FIRST_RECEIPT_SCHEMA = "qualibug.structure-first-business-fact-compilation.v1"

_TERMINAL_CANDIDATE_STATUSES = frozenset(
    {
        "ACCEPTED",
        "PENDING_WITH_REASON",
        "CONFLICTING",
        "SUPERSEDED_BY_AUTHORITY",
        "NON_FACT_CONTEXT",
        "UNSUPPORTED_STRUCTURE",
    }
)
_EXCLUDED_REGIONS = frozenset({"header", "footer", "page_header", "page_footer"})
_FORMAL_TEXT_BLOCK_TYPES = frozenset(
    {"HEADING", "PARAGRAPH", "LIST_ITEM", "TABLE_CELL", "TEXT", "CAPTION", "FORM_FIELD"}
)
_CRITICAL_SIGNAL_RE = re.compile(
    r"不得|严禁|禁止|不允许|不可|不能|无权|只能|仅能|除外|除非|"
    r"审批|权限|本人|本部门|本组织|本区域|本仓库|必须|应当"
)
_ACTION_PATTERN = re.compile(
    r"审批通过|审核通过|审批退回|审核退回|重新提交|重新编辑|"
    r"创建|新建|新增|生成|提交|发起|撤回|撤销|驳回|拒绝|"
    r"修改|编辑|变更|调整|删除|移除|作废|取消|终止|查看|"
    r"查询|读取|浏览|发货|出库|配送|收货|签收|付款|支付|"
    r"退款|退费|核销|冲销|红冲|保存|关闭|写入|更新|释放|"
    r"扣减|增加|发送|通知|关联|对应|包含|组成|构成|归属于|依赖"
)
_MODALITY_RE = re.compile(
    r"必须|应当|应该|需要|不得|严禁|禁止|不允许|不可|不能|"
    r"只能|仅能|可以|允许|有权|无权|仅当|只有|除非"
)
_RELATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "COMPOSED_OF",
        re.compile(
            r"(?P<subject>[\u4e00-\u9fffA-Za-z0-9_.-]{1,30})"
            r"(?:由)(?P<object>[^，,。；;]{1,60}?)(?:组成|构成)"
        ),
    ),
    (
        "CONTAINS",
        re.compile(
            r"(?P<subject>[\u4e00-\u9fffA-Za-z0-9_.-]{1,30})"
            r"(?:包含|包括)(?P<object>[^，,。；;]{1,60})"
        ),
    ),
    (
        "ASSOCIATES_WITH",
        re.compile(
            r"(?P<subject>[\u4e00-\u9fffA-Za-z0-9_.-]{1,30})"
            r"(?:关联|对应)(?P<object>[\u4e00-\u9fffA-Za-z0-9_.-]{1,30})"
        ),
    ),
    (
        "BELONGS_TO",
        re.compile(
            r"(?P<subject>[\u4e00-\u9fffA-Za-z0-9_.-]{1,30})"
            r"(?:归属于|隶属于|属于)(?P<object>[\u4e00-\u9fffA-Za-z0-9_.-]{1,30})"
        ),
    ),
    (
        "DEPENDS_ON",
        re.compile(
            r"(?P<subject>[\u4e00-\u9fffA-Za-z0-9_.-]{1,30})"
            r"(?:依赖于|依赖)(?P<object>[\u4e00-\u9fffA-Za-z0-9_.-]{1,30})"
        ),
    ),
    (
        "GENERATES",
        re.compile(
            r"(?P<subject>[\u4e00-\u9fffA-Za-z0-9_.-]{1,30})"
            r"(?:生成|产生|创建)(?P<object>[\u4e00-\u9fffA-Za-z0-9_.-]{1,30})"
        ),
    ),
)
_CARDINALITY_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "EXACTLY_ONE",
        "1",
        re.compile(
            r"(?:每|任一)(?:个|张|条|份)?"
            r"(?P<subject>[\u4e00-\u9fffA-Za-z0-9_.-]{1,24}?)"
            r"(?:只能|仅能)(?:关联|对应|包含|拥有)"
            r"(?:一个|1个|一条|1条|一份|1份)"
            r"(?P<object>[\u4e00-\u9fffA-Za-z0-9_.-]{1,24})"
        ),
    ),
    (
        "EXACTLY_ONE",
        "1",
        re.compile(
            r"(?:每|任一)(?:个|张|条|份)?"
            r"(?P<subject>[\u4e00-\u9fffA-Za-z0-9_.-]{1,24}?)"
            r"必须(?:关联|对应|包含|拥有)?(?:且仅|并且仅|且只能|并且只能)"
            r"(?:关联|对应|包含|拥有)?"
            r"(?:一个|1个|一条|1条|一份|1份)"
            r"(?P<object>[\u4e00-\u9fffA-Za-z0-9_.-]{1,24})"
        ),
    ),
    (
        "ONE_TO_MANY",
        "MANY",
        re.compile(
            r"(?:每|一个|任一)(?:个|张|条|份)?"
            r"(?P<subject>[\u4e00-\u9fffA-Za-z0-9_.-]{1,24}?)"
            r"(?:可|可以|能够)?(?:包含|关联|对应|拥有)"
            r"(?:多个|多条|多份|若干)"
            r"(?P<object>[\u4e00-\u9fffA-Za-z0-9_.-]{1,24})"
        ),
    ),
)
_FORMULA_RE = re.compile(
    r"(?P<lhs>[\u4e00-\u9fffA-Za-z0-9_.]{1,30})\s*"
    r"(?:=|＝|等于|为)\s*"
    r"(?P<rhs>[^，,。；;]{1,80})"
)
_MULTI_OBJECT_SPLIT_RE = re.compile(r"\s*(?:、|和|与|及|以及)\s*")
_GENERIC_PREFIX_RE = re.compile(
    r"^(?:每个|每张|每条|每份|一个|任一|该|本|此|对应的|相关的|"
    r"由|对|向|给|其|当前|所属)"
)
_GENERIC_SUFFIX_RE = re.compile(
    r"(?:的|记录|数据|对象|实体|信息|内容|业务对象)$"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(
        json.dumps(part, ensure_ascii=False, sort_keys=True, default=str)
        if isinstance(part, (dict, list, tuple))
        else _text(part)
        for part in parts
    )
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _norm(value: Any) -> str:
    return re.sub(r"[\s，,。；;：:（）()【】\[\]“”\"'、]+", "", _text(value)).lower()


def _clean_entity(value: Any) -> str:
    cleaned = _text(value).strip(" ：:、，,；;。\"'“”‘’[]【】")
    cleaned = _GENERIC_PREFIX_RE.sub("", cleaned)
    cleaned = _GENERIC_SUFFIX_RE.sub("", cleaned)
    return _text(cleaned)


def _source_spans(fact: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in _list(fact.get("source_spans")) if isinstance(row, dict)]


def _block_address(block: dict[str, Any], source_id: str, filename: str) -> dict[str, Any]:
    evidence = _dict(block.get("evidence_address"))
    locator = _text(
        block.get("source_locator")
        or evidence.get("source_locator")
        or block.get("locator")
    )
    if not locator:
        block_id = _text(block.get("block_id"))
        locator = (
            f"{filename or source_id}#block={block_id}"
            if block_id
            else f"{filename or source_id}#order={block.get('order', '')}"
        )
    address = {
        "source_id": source_id,
        "locator": locator,
        "source_locator": locator,
        "document_block_id": _text(block.get("block_id")),
        "block_type": _text(block.get("type") or block.get("block_type")).upper(),
        "address_kind": _text(
            evidence.get("address_kind") or block.get("address_kind") or "EXACT_SOURCE_LOCATOR"
        ),
        "order": block.get("order"),
        "page": block.get("page") or block.get("page_number"),
        "row_index": block.get("row_index"),
        "column_index": block.get("column_index"),
        "bbox": block.get("bbox") or evidence.get("bbox"),
        "derivation": "document_structure_ir_primary_evidence",
    }
    return {key: value for key, value in address.items() if value not in (None, "", [])}


def _semantic_spans(parsed_sources: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for source in parsed_sources:
        if not isinstance(source, dict):
            continue
        source_id = _text(source.get("source_id"))
        filename = _text(source.get("filename"))
        structure = _dict(source.get("document_structure"))
        blocks = [
            dict(row)
            for row in _list(structure.get("blocks"))
            if isinstance(row, dict)
        ]
        if blocks:
            for block in blocks:
                region = _text(block.get("region")).lower()
                text_value = _text(block.get("text"))
                address = _block_address(block, source_id, filename)
                spans.append(
                    {
                        "span_id": _stable_id(
                            "semantic_span",
                            source_id,
                            address.get("document_block_id"),
                            address.get("locator"),
                            text_value,
                        ),
                        "source_id": source_id,
                        "filename": filename,
                        "text": text_value,
                        "block_type": address.get("block_type") or "UNKNOWN",
                        "region": region or "body",
                        "eligible_for_business_fact": region not in _EXCLUDED_REGIONS,
                        "evidence_address": address,
                        "structure_authority": True,
                        "legacy_fallback": False,
                    }
                )
            continue

        legacy_text = _text(source.get("text") or source.get("legacy_text"))
        if legacy_text:
            locator = _text(source.get("source_locator")) or f"{filename or source_id}#document"
            spans.append(
                {
                    "span_id": _stable_id("semantic_span", source_id, locator, legacy_text),
                    "source_id": source_id,
                    "filename": filename,
                    "text": legacy_text,
                    "block_type": "LEGACY_TEXT",
                    "region": "body",
                    "eligible_for_business_fact": True,
                    "evidence_address": {
                        "source_id": source_id,
                        "locator": locator,
                        "source_locator": locator,
                        "address_kind": "LEGACY_TEXT_RANGE",
                        "derivation": "legacy_text_projection_fallback",
                    },
                    "structure_authority": False,
                    "legacy_fallback": True,
                }
            )
    return spans


def _split_objects(value: str) -> list[str]:
    result: list[str] = []
    for part in _MULTI_OBJECT_SPLIT_RE.split(_text(value)):
        cleaned = _clean_entity(part)
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _claim(
    *,
    frame_id: str,
    claim_type: str,
    predicate: str,
    subject_refs: Iterable[str] = (),
    object_refs: Iterable[str] = (),
    value: Any = None,
    evidence: Iterable[dict[str, Any]] = (),
    source_backed: bool = True,
) -> dict[str, Any]:
    subject = sorted({_text(item) for item in subject_refs if _text(item)})
    objects = sorted({_text(item) for item in object_refs if _text(item)})
    payload = {
        "claim_type": claim_type,
        "predicate": _text(predicate),
        "subject_refs": subject,
        "object_refs": objects,
        "value": value,
    }
    return {
        "claim_id": _stable_id("claim", frame_id, payload),
        "statement_frame_id": frame_id,
        **payload,
        "evidence": [dict(row) for row in evidence if isinstance(row, dict)],
        "source_backed": source_backed,
    }


def _condition_signature(fact: dict[str, Any]) -> Any:
    frame = _dict(fact.get("condition_frame"))
    if frame:
        return {
            "kind": _text(frame.get("kind")),
            "combinator": _text(frame.get("combinator")),
            "conditions": [_norm(row) for row in _list(frame.get("conditions")) if _norm(row)],
            "exception_scopes": sorted(
                _norm(row) for row in _list(frame.get("exception_scopes")) if _norm(row)
            ),
            "branch": _text(frame.get("branch")),
            "branch_index": frame.get("branch_index"),
            "parent_conditions": [
                _norm(row) for row in _list(frame.get("parent_conditions")) if _norm(row)
            ],
        }
    return {
        "combinator": _text(fact.get("condition_combinator")),
        "conditions": [_norm(row) for row in _list(fact.get("conditions")) if _norm(row)],
    }


def _semantic_signature_payload(fact: dict[str, Any]) -> dict[str, Any]:
    subject = _dict(fact.get("subject"))
    action = _dict(fact.get("action"))
    scope = _dict(fact.get("scope"))
    return {
        "fact_type": _text(fact.get("fact_type") or fact.get("kind")),
        "actor_refs": sorted(_norm(row) for row in _list(subject.get("actor_refs")) if _norm(row)),
        "entity_refs": sorted(
            _norm(row)
            for row in [
                *_list(subject.get("entity_refs")),
                *_list(_dict(fact.get("object")).get("entity_refs")),
            ]
            if _norm(row)
        ),
        "predicate": _norm(
            fact.get("predicate")
            or action.get("canonical")
            or action.get("raw")
            or fact.get("relation_type")
        ),
        "modality": _text(fact.get("modality")),
        "condition": _condition_signature(fact),
        "exception_scope": sorted(
            _norm(row) for row in _list(fact.get("exception_scope")) if _norm(row)
        ),
        "scope": {
            _text(key): _norm(value)
            for key, value in sorted(scope.items())
            if _norm(value)
        },
        "state_effects": sorted(
            (
                _norm(_dict(row).get("from_state")),
                _norm(_dict(row).get("to_state")),
            )
            for row in _list(fact.get("state_effects"))
            if isinstance(row, dict)
        ),
        "data_effects": sorted(
            (
                _norm(_dict(row).get("action")),
                _norm(_dict(row).get("entity")),
                _norm(_dict(row).get("statement")),
            )
            for row in _list(fact.get("data_effects"))
            if isinstance(row, dict)
        ),
        "postconditions": sorted(_norm(row) for row in _list(fact.get("postconditions")) if _norm(row)),
        "compensations": sorted(
            _norm(row)
            for row in _list(fact.get("compensation") or fact.get("compensations"))
            if _norm(row)
        ),
        "quantity_constraints": sorted(
            json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
            for row in _list(fact.get("quantity_constraints"))
            if isinstance(row, dict)
        ),
        "time_window_constraints": sorted(
            json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
            for row in _list(fact.get("time_window_constraints"))
            if isinstance(row, dict)
        ),
        "formula_constraints": sorted(
            json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
            for row in _list(fact.get("formula_constraints"))
            if isinstance(row, dict)
        ),
    }


def _semantic_signature(fact: dict[str, Any]) -> str:
    payload = _semantic_signature_payload(fact)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "fact-signature:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _fact_type(fact: dict[str, Any]) -> str:
    current = _text(fact.get("fact_type"))
    if current:
        return current
    kind = _text(fact.get("kind"))
    if kind == "TERM_ALIAS":
        return "TERM_ALIAS"
    if _list(fact.get("state_effects")) or kind == "STATE_TRANSITION":
        return "STATE_TRANSITION"
    if _list(fact.get("formula_constraints")) and not _dict(fact.get("action")):
        return "DERIVED_VALUE"
    subject = _dict(fact.get("subject"))
    scope = _dict(fact.get("scope"))
    if (
        _list(subject.get("actor_refs"))
        or any(_text(value) for value in scope.values())
        or _text(fact.get("modality")) in {"MAY", "MUST_NOT", "ONLY_IF"}
    ):
        return "PERMISSION_RULE"
    return "BUSINESS_RULE"


def _atomize_existing_fact(fact: dict[str, Any]) -> dict[str, Any]:
    row = dict(fact)
    spans = _source_spans(row)
    frame_id = _text(row.get("statement_frame_id")) or _stable_id(
        "statement_frame",
        row.get("fact_id"),
        row.get("raw_statement"),
        spans,
    )
    row["statement_frame_id"] = frame_id
    row["fact_type"] = _fact_type(row)
    subject = _dict(row.get("subject"))
    actor_refs = [_text(item) for item in _list(subject.get("actor_refs")) if _text(item)]
    entity_refs = [_text(item) for item in _list(subject.get("entity_refs")) if _text(item)]
    action = _dict(row.get("action"))
    claims: list[dict[str, Any]] = [
        dict(claim)
        for claim in _list(row.get("claims"))
        if isinstance(claim, dict) and _text(claim.get("claim_id"))
    ]

    predicate = _text(action.get("canonical") or action.get("raw"))
    if predicate:
        claims.append(
            _claim(
                frame_id=frame_id,
                claim_type="PRIMARY_OPERATION",
                predicate=predicate,
                subject_refs=actor_refs,
                object_refs=entity_refs,
                evidence=spans,
            )
        )
    for effect in _list(row.get("state_effects")):
        if not isinstance(effect, dict):
            continue
        claims.append(
            _claim(
                frame_id=frame_id,
                claim_type="STATE_TRANSITION",
                predicate="STATE_TRANSITION",
                subject_refs=actor_refs,
                object_refs=entity_refs,
                value={
                    "from_state": _text(effect.get("from_state")),
                    "to_state": _text(effect.get("to_state")),
                    "raw": _text(effect.get("raw")),
                },
                evidence=spans,
            )
        )
    for effect in _list(row.get("data_effects")):
        if not isinstance(effect, dict):
            continue
        claims.append(
            _claim(
                frame_id=frame_id,
                claim_type="DATA_EFFECT",
                predicate=_text(effect.get("action")) or "DATA_EFFECT",
                subject_refs=actor_refs,
                object_refs=[_text(effect.get("entity"))],
                value=dict(effect),
                evidence=spans,
            )
        )
    for value in _list(row.get("postconditions")):
        if _text(value):
            claims.append(
                _claim(
                    frame_id=frame_id,
                    claim_type="POSTCONDITION",
                    predicate="RESULTS_IN",
                    subject_refs=actor_refs,
                    object_refs=entity_refs,
                    value=_text(value),
                    evidence=spans,
                )
            )
    for value in _list(row.get("compensation") or row.get("compensations")):
        if _text(value):
            claims.append(
                _claim(
                    frame_id=frame_id,
                    claim_type="COMPENSATION",
                    predicate="COMPENSATES",
                    subject_refs=actor_refs,
                    object_refs=entity_refs,
                    value=_text(value),
                    evidence=spans,
                )
            )
    for field, claim_type in (
        ("quantity_constraints", "QUANTITY_CONSTRAINT"),
        ("time_window_constraints", "TIME_WINDOW_CONSTRAINT"),
        ("formula_constraints", "FORMULA_CONSTRAINT"),
    ):
        for value in _list(row.get(field)):
            if isinstance(value, dict):
                claims.append(
                    _claim(
                        frame_id=frame_id,
                        claim_type=claim_type,
                        predicate=claim_type,
                        subject_refs=actor_refs,
                        object_refs=entity_refs,
                        value=dict(value),
                        evidence=spans,
                    )
                )

    statement = _text(row.get("raw_statement"))
    seen_predicates = {
        _norm(claim.get("predicate"))
        for claim in claims
        if _text(claim.get("claim_type")) == "PRIMARY_OPERATION"
    }
    for match in _ACTION_PATTERN.finditer(statement):
        raw_predicate = _text(match.group(0))
        if _norm(raw_predicate) in seen_predicates:
            continue
        seen_predicates.add(_norm(raw_predicate))
        claims.append(
            _claim(
                frame_id=frame_id,
                claim_type="ATOMIC_OPERATION",
                predicate=raw_predicate,
                subject_refs=actor_refs,
                object_refs=entity_refs,
                evidence=spans,
            )
        )

    row["claims"] = list(
        {
            _text(claim.get("claim_id")): claim
            for claim in claims
            if isinstance(claim, dict) and _text(claim.get("claim_id"))
        }.values()
    )
    ambiguities = [_text(value) for value in _list(row.get("ambiguities")) if _text(value)]
    exact_spans = [
        span
        for span in spans
        if _text(span.get("document_block_id"))
        or _text(span.get("address_kind")) in {"EXACT_SOURCE_LOCATOR", "PAGE_BBOX"}
    ]
    row["resolution_quality"] = {
        "status": _text(row.get("status")) or "PENDING",
        "ambiguity_count": len(ambiguities),
        "actor_resolved": bool(actor_refs) or "OMITTED_ACTOR" not in " ".join(ambiguities),
        "entity_resolved": bool(entity_refs) or "COREFERENCE" not in " ".join(ambiguities),
        "condition_logic_resolved": _text(row.get("condition_combinator")) != "UNRESOLVED",
        "manual_confidence_is_accuracy": False,
    }
    row["evidence_closure"] = {
        "status": "PASS" if spans and exact_spans else "PARTIAL",
        "source_span_count": len(spans),
        "exact_address_span_count": len(exact_spans),
        "structure_first": bool(exact_spans),
        "legacy_text_only": bool(spans) and not bool(exact_spans),
    }
    row["semantic_signature"] = _semantic_signature(row)
    return row


def _new_fact_base(
    *,
    span: dict[str, Any],
    fact_type: str,
    raw_statement: str,
    subject_refs: Iterable[str],
    object_refs: Iterable[str],
    predicate: str,
    modality: str = "ASSERTS",
    value: Any = None,
) -> dict[str, Any]:
    address = dict(_dict(span.get("evidence_address")))
    quote = _text(raw_statement)
    address.update(
        {
            "quote": quote,
            "quote_hash": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        }
    )
    frame_id = _stable_id(
        "statement_frame",
        span.get("span_id"),
        fact_type,
        predicate,
        subject_refs,
        object_refs,
        value,
    )
    subject = sorted({_text(item) for item in subject_refs if _text(item)})
    objects = sorted({_text(item) for item in object_refs if _text(item)})
    fact = {
        "fact_id": _stable_id(
            "fact",
            span.get("source_id"),
            address.get("locator"),
            fact_type,
            predicate,
            subject,
            objects,
            value,
        ),
        "kind": "RULE",
        "fact_type": fact_type,
        "language": "zh-CN",
        "statement_frame_id": frame_id,
        "subject": {
            "actor_refs": [],
            "entity_refs": subject,
            "resolution_evidence": [
                {
                    "method": "document_structure_explicit_subject",
                    "source_backed": True,
                    "document_block_id": address.get("document_block_id"),
                }
            ],
        },
        "object": {"entity_refs": objects},
        "predicate": predicate,
        "value": dict(value) if isinstance(value, dict) else value,
        "action": {},
        "conditions": [],
        "condition_combinator": "",
        "condition_frame": {},
        "scope": {"tenant": "", "organization": "", "ownership": "", "data_scope": ""},
        "modality": modality,
        "polarity": "POSITIVE",
        "exceptions": [],
        "exception_scope": [],
        "postconditions": [],
        "state_effects": [],
        "data_effects": [],
        "temporal_constraints": [],
        "quantity_constraints": (
            [dict(value)]
            if fact_type == "CARDINALITY_CONSTRAINT" and isinstance(value, dict)
            else []
        ),
        "time_window_constraints": [],
        "formula_constraints": [],
        "authorization_delegation": {},
        "compensation": [],
        "compensations": [],
        "raw_statement": quote,
        "normalized_statement": _norm(quote),
        "source_spans": [address],
        "confidence": 1.0 if span.get("structure_authority") else 0.55,
        "status": "ACCEPTED" if span.get("structure_authority") else "PENDING",
        "ambiguities": [] if span.get("structure_authority") else ["LEGACY_TEXT_EVIDENCE_NOT_EXACT"],
        "critical": bool(_CRITICAL_SIGNAL_RE.search(quote)),
        "derivation": "structure_first_explicit_fact_compiler",
        "claims": [
            _claim(
                frame_id=frame_id,
                claim_type=fact_type,
                predicate=predicate,
                subject_refs=subject,
                object_refs=objects,
                value=value,
                evidence=[address],
                source_backed=True,
            )
        ],
        "resolution_quality": {
            "status": "ACCEPTED" if span.get("structure_authority") else "PENDING",
            "ambiguity_count": 0 if span.get("structure_authority") else 1,
            "actor_resolved": True,
            "entity_resolved": bool(subject and objects),
            "condition_logic_resolved": True,
            "manual_confidence_is_accuracy": False,
        },
        "evidence_closure": {
            "status": "PASS" if span.get("structure_authority") else "PARTIAL",
            "source_span_count": 1,
            "exact_address_span_count": 1 if span.get("structure_authority") else 0,
            "structure_first": bool(span.get("structure_authority")),
            "legacy_text_only": bool(span.get("legacy_fallback")),
        },
    }
    fact["semantic_signature"] = _semantic_signature(fact)
    return fact


def _cardinality_match_ranges(text_value: str) -> list[tuple[int, int]]:
    return [
        match.span()
        for _cardinality, _maximum, pattern in _CARDINALITY_PATTERNS
        for match in pattern.finditer(text_value)
    ]


def _relation_facts(span: dict[str, Any]) -> list[dict[str, Any]]:
    text_value = _text(span.get("text"))
    cardinality_ranges = _cardinality_match_ranges(text_value)
    rows: list[dict[str, Any]] = []
    for relation_type, pattern in _RELATION_PATTERNS:
        for match in pattern.finditer(text_value):
            if any(
                match.start() < end and match.end() > start
                for start, end in cardinality_ranges
            ):
                continue
            subject = _clean_entity(match.group("subject"))
            objects = _split_objects(match.group("object"))
            if not subject or not objects:
                continue
            for obj in objects:
                if obj == subject:
                    continue
                rows.append(
                    _new_fact_base(
                        span=span,
                        fact_type="OBJECT_RELATION",
                        raw_statement=match.group(0),
                        subject_refs=[subject],
                        object_refs=[obj],
                        predicate=relation_type,
                        value={"relation_type": relation_type},
                    )
                )
    return rows


def _cardinality_facts(span: dict[str, Any]) -> list[dict[str, Any]]:
    text_value = _text(span.get("text"))
    rows: list[dict[str, Any]] = []
    for cardinality, maximum, pattern in _CARDINALITY_PATTERNS:
        for match in pattern.finditer(text_value):
            subject = _clean_entity(match.group("subject"))
            obj = _clean_entity(match.group("object"))
            if not subject or not obj or subject == obj:
                continue
            rows.append(
                _new_fact_base(
                    span=span,
                    fact_type="CARDINALITY_CONSTRAINT",
                    raw_statement=match.group(0),
                    subject_refs=[subject],
                    object_refs=[obj],
                    predicate=cardinality,
                    modality="ONLY_IF" if cardinality == "EXACTLY_ONE" else "MAY",
                    value={
                        "cardinality": cardinality,
                        "minimum": 1 if cardinality == "EXACTLY_ONE" else 0,
                        "maximum": maximum,
                    },
                )
            )
    return rows


def _formula_facts(span: dict[str, Any]) -> list[dict[str, Any]]:
    text_value = _text(span.get("text"))
    rows: list[dict[str, Any]] = []
    for match in _FORMULA_RE.finditer(text_value):
        lhs = _clean_entity(match.group("lhs"))
        rhs = _text(match.group("rhs"))
        raw = match.group(0)
        if "等于" not in raw and not re.search(r"[+\-*/×÷]|之和|之差|乘以|除以|减去|加上", rhs):
            continue
        if not lhs or not rhs:
            continue
        fact = _new_fact_base(
            span=span,
            fact_type="DERIVED_VALUE",
            raw_statement=raw,
            subject_refs=[lhs],
            object_refs=[lhs],
            predicate="DERIVED_AS",
            value={"lhs": lhs, "rhs": rhs, "raw": raw},
        )
        fact["formula_constraints"] = [
            {"raw": raw, "lhs": lhs, "rhs": rhs, "source_backed": True}
        ]
        fact["semantic_signature"] = _semantic_signature(fact)
        rows.append(fact)
    return rows


def _facts_from_span(span: dict[str, Any]) -> list[dict[str, Any]]:
    if not span.get("eligible_for_business_fact") or not _text(span.get("text")):
        return []
    rows = [
        *_relation_facts(span),
        *_cardinality_facts(span),
        *_formula_facts(span),
    ]
    return list(
        {
            _text(row.get("semantic_signature")) or _text(row.get("fact_id")): row
            for row in rows
            if isinstance(row, dict)
        }.values()
    )


def _match_existing_facts_to_spans(
    facts: list[dict[str, Any]], spans: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, list[str]], int]:
    """Attach exact structure evidence only when one unique block contains the quote."""
    span_fact_ids: dict[str, list[str]] = {}
    aligned_count = 0
    by_source: dict[str, list[dict[str, Any]]] = {}
    for span in spans:
        by_source.setdefault(_text(span.get("source_id")), []).append(span)
    normalized_spans = {
        id(span): _norm(span.get("text")) for span in spans
    }

    result: list[dict[str, Any]] = []
    for raw_fact in facts:
        fact = dict(raw_fact)
        statement = _text(fact.get("raw_statement"))
        normalized_statement = _norm(statement)
        source_ids = {
            _text(row.get("source_id"))
            for row in _source_spans(fact)
            if _text(row.get("source_id"))
        }
        candidates: list[dict[str, Any]] = []
        if normalized_statement:
            search_sources = source_ids or set(by_source)
            for source_id in search_sources:
                for span in by_source.get(source_id, []):
                    if not span.get("structure_authority"):
                        continue
                    span_text = normalized_spans[id(span)]
                    if normalized_statement and normalized_statement in span_text:
                        candidates.append(span)
        if len(candidates) == 1:
            span = candidates[0]
            address = dict(_dict(span.get("evidence_address")))
            address.update(
                {
                    "quote": statement,
                    "quote_hash": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
                    "derivation": "document_structure_exact_statement_alignment",
                }
            )
            existing = _source_spans(fact)
            identities = {
                (
                    _text(row.get("source_id")),
                    _text(row.get("document_block_id")),
                    _text(row.get("locator") or row.get("source_locator")),
                    _text(row.get("quote_hash")),
                )
                for row in existing
            }
            identity = (
                _text(address.get("source_id")),
                _text(address.get("document_block_id")),
                _text(address.get("locator") or address.get("source_locator")),
                _text(address.get("quote_hash")),
            )
            if identity not in identities:
                existing.append(address)
            fact["source_spans"] = existing
            fact["structural_span_attachment"] = {
                "source_id": address.get("source_id"),
                "source_locator": address.get("locator"),
                "document_block_id": address.get("document_block_id"),
                "block_type": address.get("block_type"),
                "address_kind": address.get("address_kind"),
                "status": "EXACT",
            }
            aligned_count += 1
            span_fact_ids.setdefault(_text(span.get("span_id")), []).append(
                _text(fact.get("fact_id"))
            )
        result.append(fact)
    return result, span_fact_ids, aligned_count


def _merge_same_signature(facts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    merged: dict[str, dict[str, Any]] = {}
    merge_count = 0
    for raw in facts:
        fact = _atomize_existing_fact(raw)
        signature = _text(fact.get("semantic_signature")) or _semantic_signature(fact)
        existing = merged.get(signature)
        if existing is None:
            merged[signature] = fact
            continue
        merge_count += 1
        existing_spans = _source_spans(existing)
        seen_spans = {
            (
                _text(row.get("source_id")),
                _text(row.get("document_block_id")),
                _text(row.get("locator") or row.get("source_locator")),
                _text(row.get("quote_hash")),
            )
            for row in existing_spans
        }
        for span in _source_spans(fact):
            identity = (
                _text(span.get("source_id")),
                _text(span.get("document_block_id")),
                _text(span.get("locator") or span.get("source_locator")),
                _text(span.get("quote_hash")),
            )
            if identity not in seen_spans:
                existing_spans.append(span)
                seen_spans.add(identity)
        existing["source_spans"] = existing_spans
        existing_claims = {
            _text(row.get("claim_id")): dict(row)
            for row in _list(existing.get("claims"))
            if isinstance(row, dict) and _text(row.get("claim_id"))
        }
        for claim in _list(fact.get("claims")):
            if isinstance(claim, dict) and _text(claim.get("claim_id")):
                existing_claims.setdefault(_text(claim.get("claim_id")), dict(claim))
        existing["claims"] = list(existing_claims.values())
        existing["evidence_closure"] = {
            **_dict(existing.get("evidence_closure")),
            "source_span_count": len(existing_spans),
            "multi_source_evidence": len(
                {
                    _text(row.get("source_id"))
                    for row in existing_spans
                    if _text(row.get("source_id"))
                }
            )
            > 1,
        }
    return list(merged.values()), merge_count


def _candidate_rows(
    spans: list[dict[str, Any]],
    span_fact_ids: dict[str, list[str]],
    discovered_by_span: dict[str, list[str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for span in spans:
        span_id = _text(span.get("span_id"))
        text_value = _text(span.get("text"))
        extracted = sorted(
            {
                _text(value)
                for value in [
                    *span_fact_ids.get(span_id, []),
                    *discovered_by_span.get(span_id, []),
                ]
                if _text(value)
            }
        )
        has_candidate_signal = bool(
            _MODALITY_RE.search(text_value)
            or _ACTION_PATTERN.search(text_value)
            or any(pattern.search(text_value) for _kind, pattern in _RELATION_PATTERNS)
            or any(pattern.search(text_value) for _kind, _max, pattern in _CARDINALITY_PATTERNS)
            or _FORMULA_RE.search(text_value)
        )
        reasons: list[str] = []
        if not text_value:
            if (
                span.get("eligible_for_business_fact")
                and _text(span.get("block_type")).upper() in _FORMAL_TEXT_BLOCK_TYPES
            ):
                status = "UNSUPPORTED_STRUCTURE"
                reasons.append("FORMAL_STRUCTURE_BLOCK_TEXT_EMPTY")
            else:
                status = "NON_FACT_CONTEXT"
                reasons.append("NON_TEXT_STRUCTURE_BLOCK")
        elif not span.get("eligible_for_business_fact"):
            status = "NON_FACT_CONTEXT"
            reasons.append("HEADER_FOOTER_EXCLUDED")
        elif extracted:
            status = "ACCEPTED"
        elif has_candidate_signal:
            status = "PENDING_WITH_REASON"
            reasons.append("STRUCTURE_BACKED_CANDIDATE_NOT_COMPILED")
        else:
            status = "NON_FACT_CONTEXT"
        rows.append(
            {
                "candidate_id": _stable_id("fact_candidate", span_id),
                "span_id": span_id,
                "source_id": span.get("source_id"),
                "filename": span.get("filename"),
                "block_type": span.get("block_type"),
                "region": span.get("region"),
                "status": status,
                "terminal": status in _TERMINAL_CANDIDATE_STATUSES,
                "contains_candidate_signal": has_candidate_signal,
                "critical": bool(_CRITICAL_SIGNAL_RE.search(text_value)),
                "fact_ids": extracted,
                "reason_codes": reasons,
                "evidence_address": dict(_dict(span.get("evidence_address"))),
                "text_hash": hashlib.sha256(text_value.encode("utf-8")).hexdigest()
                if text_value
                else "",
                "quote": text_value[:500],
            }
        )
    return rows


def compile_structure_first_business_facts(
    asset: dict[str, Any],
    parsed_sources: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Compile structure-backed typed facts into the existing business fact ledger."""
    spans = _semantic_spans(parsed_sources)
    ledger = _dict(asset.get("business_fact_ledger"))
    existing_facts = [
        dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)
    ]
    aligned_facts, span_fact_ids, aligned_count = _match_existing_facts_to_spans(
        existing_facts, spans
    )

    discovered: list[dict[str, Any]] = []
    discovered_by_span: dict[str, list[str]] = {}
    for span in spans:
        rows = _facts_from_span(span)
        discovered.extend(rows)
        if rows:
            discovered_by_span[_text(span.get("span_id"))] = [
                _text(row.get("fact_id")) for row in rows if _text(row.get("fact_id"))
            ]

    facts, semantic_merge_count = _merge_same_signature(
        [*aligned_facts, *discovered]
    )
    candidate_rows = _candidate_rows(spans, span_fact_ids, discovered_by_span)
    candidate_statuses = Counter(_text(row.get("status")) for row in candidate_rows)
    critical_pending = [
        row
        for row in candidate_rows
        if row.get("critical") and _text(row.get("status")) == "PENDING_WITH_REASON"
    ]
    unsupported = [
        row
        for row in candidate_rows
        if _text(row.get("status")) == "UNSUPPORTED_STRUCTURE"
    ]
    accepted_facts = [
        row for row in facts if _text(row.get("status")) == "ACCEPTED"
    ]
    pending_facts = [
        row for row in facts if _text(row.get("status")) == "PENDING"
    ]
    exact_evidence_facts = [
        row
        for row in facts
        if _text(_dict(row.get("evidence_closure")).get("status")) == "PASS"
    ]

    asset["business_fact_ledger"] = {
        "schema": BUSINESS_FACT_LEDGER_V2_SCHEMA,
        "compatibility_schema": ledger.get("schema")
        or "qualibug.business-fact-ledger.v1",
        "fact_authority": ledger.get("fact_authority")
        or "original_chinese_source_span",
        "translation_intermediate_forbidden": bool(
            ledger.get("translation_intermediate_forbidden", True)
        ),
        "items": facts,
        "semantic_signature_authority": "typed_atomic_source_backed_slots",
        "statement_text_is_deduplication_authority": False,
    }
    asset["business_fact_candidate_ledger"] = {
        "schema": BUSINESS_FACT_CANDIDATE_LEDGER_SCHEMA,
        "terminal_statuses": sorted(_TERMINAL_CANDIDATE_STATUSES),
        "items": candidate_rows,
        "all_candidates_terminal": all(
            bool(row.get("terminal")) for row in candidate_rows
        ),
        "silent_drop_allowed": False,
    }
    receipt = {
        "schema": STRUCTURE_FIRST_RECEIPT_SCHEMA,
        "status": "BLOCKED" if critical_pending or unsupported else "PASS",
        "structure_span_count": len(spans),
        "structure_authority_span_count": len(
            [row for row in spans if row.get("structure_authority")]
        ),
        "legacy_fallback_span_count": len(
            [row for row in spans if row.get("legacy_fallback")]
        ),
        "existing_fact_count": len(existing_facts),
        "structure_aligned_existing_fact_count": aligned_count,
        "new_structure_fact_count": len(discovered),
        "final_fact_count": len(facts),
        "accepted_fact_count": len(accepted_facts),
        "pending_fact_count": len(pending_facts),
        "exact_evidence_fact_count": len(exact_evidence_facts),
        "semantic_merge_count": semantic_merge_count,
        "candidate_status_distribution": dict(candidate_statuses),
        "critical_pending_candidate_count": len(critical_pending),
        "unsupported_structure_candidate_count": len(unsupported),
        "candidate_terminal_coverage": (
            sum(1 for row in candidate_rows if row.get("terminal"))
            / len(candidate_rows)
            if candidate_rows
            else 1.0
        ),
        "manual_confidence_is_accuracy": False,
        "document_order_is_business_flow": False,
        "filename_is_business_context": False,
        "embedding_similarity_can_merge_formal_facts": False,
        "model_output_can_promote_fact": False,
    }
    asset["structure_first_business_fact_compilation_receipt"] = receipt

    gate = _dict(asset.get("enterprise_comprehension_gate"))
    prior_status = _text(gate.get("status")) or "UNKNOWN"
    prior_entry_allowed = bool(gate.get("entry_allowed", prior_status == "PASS"))
    gate["structure_first_fact_compilation"] = {
        "status": receipt["status"],
        "critical_pending_candidate_count": len(critical_pending),
        "unsupported_structure_candidate_count": len(unsupported),
        "candidate_terminal_coverage": receipt["candidate_terminal_coverage"],
        "exact_evidence_fact_count": len(exact_evidence_facts),
        "fact_count": len(facts),
    }
    if receipt["status"] != "PASS":
        gate["status"] = "BLOCKED_STRUCTURE_FIRST_FACT_COMPILATION_INCOMPLETE"
        gate["entry_allowed"] = False
        gate["required_operator_action"] = (
            "resolve structure-backed critical candidates and unsupported formal "
            "document blocks before facts enter Behavior IR"
        )
    else:
        gate["entry_allowed"] = prior_entry_allowed
        gate.setdefault("upstream_status_before_structure_first_compilation", prior_status)
    asset["enterprise_comprehension_gate"] = gate

    gaps = [
        dict(row)
        for row in _list(asset.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("kind"))
        != "BLOCKED_STRUCTURE_FIRST_FACT_COMPILATION_INCOMPLETE"
    ]
    if receipt["status"] != "PASS":
        gaps.append(
            {
                "kind": "BLOCKED_STRUCTURE_FIRST_FACT_COMPILATION_INCOMPLETE",
                "gap_type": "structure_backed_business_fact_candidate_incomplete",
                "source_id": "*",
                "critical_pending_candidates": critical_pending,
                "unsupported_structure_candidates": unsupported,
                "operator_action": gate.get("required_operator_action"),
            }
        )
    asset["coverage_gaps"] = gaps

    summary = _dict(asset.get("summary"))
    summary.update(
        {
            "business_fact_ledger_schema": BUSINESS_FACT_LEDGER_V2_SCHEMA,
            "structure_first_business_fact_status": receipt["status"],
            "structure_first_candidate_count": len(candidate_rows),
            "structure_first_candidate_terminal_coverage": receipt[
                "candidate_terminal_coverage"
            ],
            "typed_business_fact_count": len(facts),
            "atomic_business_claim_count": sum(
                len(_list(row.get("claims"))) for row in facts
            ),
            "structure_exact_evidence_fact_count": len(exact_evidence_facts),
            "business_fact_projection_contract": (
                "INTERNAL_EXTRACTION_COMPLETENESS_NOT_RECALL_OR_ACCURACY"
            ),
        }
    )
    asset["summary"] = summary

    governance = _dict(asset.get("governance"))
    governance.update(
        {
            "structure_first_business_fact_compilation": True,
            "document_structure_ir_is_primary_fact_input": True,
            "plain_text_is_legacy_fact_fallback_only": True,
            "typed_atomic_business_fact_ledger": True,
            "every_structure_candidate_has_terminal_status": True,
            "business_fact_silent_drop_forbidden": True,
            "semantic_signature_replaces_statement_text_dedupe": True,
            "embedding_similarity_cannot_merge_formal_facts": True,
            "model_output_cannot_promote_formal_fact": True,
            "manual_confidence_is_not_accuracy": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "BUSINESS_FACT_LEDGER_V2_SCHEMA",
    "BUSINESS_FACT_CANDIDATE_LEDGER_SCHEMA",
    "STRUCTURE_FIRST_RECEIPT_SCHEMA",
    "compile_structure_first_business_facts",
]

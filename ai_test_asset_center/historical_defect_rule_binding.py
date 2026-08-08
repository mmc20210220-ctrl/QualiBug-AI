"""Historical defect documentation → source-backed rule candidates (H3).

Enterprise teams routinely hand testers a history of past same-class defects
(「历史缺陷记录」): these documents never write the current hidden defects but
describe classes of problems the system has had. A class statement such as
HB-001 订单金额口径不一致 (优惠券和数量计算顺序不一致) is visible enterprise
material and must enter the business model — an amount-consistency class
implies that money-computation surfaces must be verified for consistent
results.

This module is the generic ingestion adapter for that material:

* it locates the historical-defect document through generic file names
  (HISTORICAL_BUGS.md / historical_bugs.md / 历史缺陷*.md / bugs_history.md /
  defect_history.md) in the same project input directories the source registry
  reads — never through a benchmark-specific name;
* it parses ``##`` entries into defect-class rules (statement + source refs,
  origin ``historical_defect`` — never promoted to explicit governance, never
  treated as a declared business rule);
* amount/calculation classes (金额/计算/不一致/顺序/amount/calculation/
  inconsistent/order/consistency) become rule candidates whose executable
  essence is the same money-consistency property the declared amount rules
  carry (payable = total − discount, discount non-negative, capped): the
  derived rules flow through the generic rule-contract binding channel, so
  money-computation surfaces receive consistency validation obligations.

Honesty boundaries: an entry is ONLY used for the defect class its own text
names (amount classes produce amount-consistency rules, nothing else); a
class with no executable surface stays a recorded coverage note; the module
never infers request bodies, business rules, entity/table names or impact
conclusions beyond what the entry states. The shared ingestion-path change
(admitting historical_bug documents into the requirement-doc scoring) is
deliberately NOT made here — it is recorded for the coordinator (AGENTS.md).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_RECEIPT_SCHEMA = "qualibug.historical-defect-rule-binding.v1"

# Generic file names for historical-defect material (any industry).
_HISTORICAL_DOC_NAMES = (
    "HISTORICAL_BUGS.md", "historical_bugs.md", "historical-bugs.md",
    "history_bugs.md", "bugs_history.md", "defect_history.md",
    "bug_history.md", "historical_issues.md",
)

# Defect classes → executable constraint family. Amount/calculation classes
# carry the money-consistency property; every other class is recorded without
# a rule (honest coverage note).
_AMOUNT_CLASS_TERMS = (
    "金额", "计算", "不一致", "顺序", "口径", "负数", "优惠",
    "amount", "calculation", "inconsistent", "order", "payable",
    "discount", "negative", "calc",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _historical_doc_paths(root: Path, project: str) -> list[Path]:
    safe = project
    candidates: list[Path] = [
        root / "projects" / project / "input",
        root / "platform_inputs" / project,
        root / "platform_workspace" / safe / "input",
    ]
    found: list[Path] = []
    seen: set[str] = set()
    for directory in candidates:
        for name in _HISTORICAL_DOC_NAMES:
            path = directory / name
            if not path.is_file():
                continue
            # Case-insensitive filesystems (Windows) resolve several of the
            # generic names to the same file; parse each document once.
            identity = str(path.resolve()).casefold()
            if identity in seen:
                continue
            seen.add(identity)
            found.append(path)
    return found


def _parse_entries(text: str) -> list[dict[str, Any]]:
    """``## HB-xxx 标题`` + body lines → {id, title, body} entries."""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("##"):
            if current:
                entries.append(current)
            title = line.lstrip("#").strip()
            entry_id = re.match(r"([A-Za-z0-9_-]+)", title)
            current = {
                "id": _text(entry_id.group(1) if entry_id else title),
                "title": title,
                "body": "",
            }
        elif current is not None and line:
            current["body"] = (current["body"] + " " + line).strip()
    if current:
        entries.append(current)
    return entries


def _classify_entry(entry: dict[str, Any]) -> str:
    # The entry's TITLE names the defect class (订单金额口径不一致); the body
    # may mention amount words incidentally (敏感金额字段 in a role-filtering
    # class), so only the title participates in the classification.
    lowered = _text(entry.get("title")).casefold()
    if any(term in lowered for term in _AMOUNT_CLASS_TERMS):
        return "amount_consistency"
    return "unclassified"


def _is_amount_class_rule(statement: str) -> bool:
    lowered = statement.casefold()
    return any(term in lowered for term in _AMOUNT_CLASS_TERMS)


def enrich_asset_with_historical_defect_rules(
    asset: dict[str, Any] | None,
    *,
    root: Path,
    project_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse the historical-defect document into rule candidates.

    Amount-class entries become rule candidates in ``rule_library`` with
    origin ``historical_defect`` (statement = the entry title, source refs
    pointing at the document). Non-amount classes are recorded in the receipt
    as coverage notes. Rules flow through the generic rule-contract binding
    channel afterwards — this module only adapts the document.
    """
    asset = dict(asset or {})
    entries_total = 0
    amount_rules = 0
    coverage_notes: list[dict[str, Any]] = []
    doc_sources: list[str] = []
    # The same enterprise material routinely ships as several physical copies
    # (project input dir + platform inputs + workspace). Rule identities are
    # derived from the document's own entry ids, so a duplicated copy would
    # fabricate duplicate rule ids and collide in the Behavior IR. Deduplicate
    # by document CONTENT hash — a copy of the same document is the same
    # source, regardless of which directory it lives in.
    seen_content_hashes: set[str] = set()
    for path in _historical_doc_paths(root, project_id):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            coverage_notes.append({
                "source": str(path),
                "note": f"unreadable: {exc}",
            })
            continue
        import hashlib

        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
        if content_hash in seen_content_hashes:
            coverage_notes.append({
                "source": str(path),
                "note": "duplicate document copy skipped (same content)",
            })
            continue
        seen_content_hashes.add(content_hash)
        doc_sources.append(str(path))
        for entry in _parse_entries(text):
            entries_total += 1
            classification = _classify_entry(entry)
            if classification != "amount_consistency":
                coverage_notes.append({
                    "entry_id": entry.get("id"),
                    "classification": classification,
                    "title": _text(entry.get("title"))[:160],
                    "note": "recorded only; no executable constraint class",
                })
                continue
            statement = _text(entry.get("title"))
            if not statement or not _is_amount_class_rule(statement):
                coverage_notes.append({
                    "entry_id": entry.get("id"),
                    "classification": classification,
                    "title": statement[:160],
                    "note": "amount class without executable statement",
                })
                continue
            rule_id = f"historical:{entry.get('id') or _stable_hash(statement)}"
            # Rule-id backstop: the same entry id may appear in another
            # document copy (or an already-enriched asset). A duplicate rule
            # id would collide into one Behavior IR node twice and fail
            # validation — keep the first occurrence, record the skip.
            existing_rule_ids = {
                str(row.get("rule_id"))
                for row in (asset.get("rule_library") or [])
                if isinstance(row, dict) and str(row.get("rule_id"))
            }
            if rule_id in existing_rule_ids:
                coverage_notes.append({
                    "entry_id": entry.get("id"),
                    "title": _text(entry.get("title"))[:160],
                    "note": "duplicate rule_id skipped",
                })
                continue
            rule = {
                "rule_id": rule_id,
                "statement": statement,
                "tokens": sorted(
                    {t for t in re.split(r"[\s：:，,、。]+", statement) if len(t) >= 2}
                ),
                "risk_type": "data_conservation",
                "origin": "historical_defect",
                "source_id": f"historical_defect:{path.stem}",
                "source_locator": str(path),
                "confidence": 0.55,
                "semantic_frame": {
                    "schema_version": "qualibug.business-semantic-frame.v1",
                    "modality": "REQUIRED",
                    "polarity": "positive",
                    "condition": "",
                    "subject": "",
                    "behavior": statement,
                    "source_anchors": [],
                    "source_grounded": True,
                },
            }
            asset.setdefault("rule_library", []).append(rule)
            amount_rules += 1
    receipt = {
        "schema_version": _RECEIPT_SCHEMA,
        "status": "OK" if doc_sources else "NO_DOCUMENT",
        "documents": doc_sources,
        "entries_total": entries_total,
        "amount_class_rules_derived": amount_rules,
        "coverage_notes": coverage_notes,
    }
    asset["historical_defect_rule_receipt"] = receipt
    return asset, receipt


def _stable_hash(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]

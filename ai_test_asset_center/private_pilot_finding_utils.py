"""Finding dedupe / evidence helper utilities for PrivatePilotHandler."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import db_persistence as db_persist
from .private_pilot_json_io import _read_json_object
from .real_project_onboarding import _safe_project_id

class FindingUtilsMixin:
    @staticmethod
    def _dedupe_risks(risks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for item in risks:
            key = "|".join([
                str(item.get("risk_id") or ""),
                str(item.get("title") or "")[:160],
                str(item.get("_api_method") or (item.get("evidence") or {}).get("method") or ""),
                str(item.get("_api_path") or (item.get("evidence") or {}).get("path") or ""),
            ]).lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _evidence_trust_score(self, risks: list[dict[str, Any]]) -> float:
        if not risks:
            return 0.0
        total = 0
        for item in risks:
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            score = 0
            if self._first_text(evidence.get("path"), item.get("_api_path")):
                score += 18
            if self._first_text(item.get("expected"), item.get("suggested_action"), evidence.get("expected")):
                score += 18
            if self._first_text(item.get("actual"), item.get("summary"), evidence.get("actual")):
                score += 18
            if self._first_text(evidence.get("status_code"), evidence.get("response_status"), evidence.get("error")):
                score += 16
            if self._first_text(evidence.get("source_file"), item.get("evidence_hint")):
                score += 12
            if str(item.get("status") or "").lower() in {"confirmed", "validated", "reproduced"}:
                score += 18
            total += min(100, score)
        return round(total / max(1, len(risks)) / 100, 2)

    @staticmethod
    def _dedupe_docs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for row in rows:
            key = str(row.get("source_id") or row.get("display_name") or "").lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out

    def _extract_module(self, title: str, description: str) -> str:
        """Extract a meaningful module name from title/description content."""
        import re
        text = (title + " " + description).lower()
        # Known business modules
        for mod, keywords in [
            ("orders", ["order", "订单"]),
            ("payments", ["pay", "支付"]),
            ("users", ["user", "用户", "role", "admin", "register", "注册"]),
            ("products", ["product", "产品"]),
            ("inventory", ["inventory", "库存"]),
            ("permissions", ["permission", "权限", "auth", "认证"]),
            ("refunds", ["refund", "退款"]),
            ("notifications", ["notif", "通知"]),
        ]:
            if any(kw in text for kw in keywords):
                return mod
        return "system"

    def _match_docs_for_finding(self, title: str, docs: list[dict]) -> list[dict]:
        """Match enterprise documents to a finding by keyword overlap.

        通用方案：从 finding 标题动态提取关键词（2字以上的中文词、英文单词），
        与文档名/摘要做交集匹配。不硬编码任何业务关键词。
        """
        if not docs: return []
        import re as _re
        title_lower = title.lower()
        # 从标题动态提取关键词（通用：2字以上中文、3字母以上英文）
        cn_words = set(_re.findall(r'[\u4e00-\u9fff]{2,}', title_lower))
        en_words = set(w for w in _re.findall(r'[a-z]{3,}', title_lower) if w not in ('the', 'and', 'for', 'with', 'from'))
        keywords = cn_words | en_words
        if not keywords:
            return []
        matched = []
        for doc in docs:
            doc_text = f"{doc.get('display_name','')} {doc.get('excerpt','')} {doc.get('type','')}".lower()
            score = sum(1 for kw in keywords if kw in title_lower and kw in doc_text)
            if score > 0:
                matched.append({**doc, "relevance": score})
        return sorted(matched, key=lambda m: -m.get("relevance", 0))[:3]

    @staticmethod
    def _build_test_task_board(report: Any) -> dict | None:
        """主链 8: 测试任务看板 — 从 v12 报告原样透传任务生命周期看板。

        数据全部来自 v12 report，前端零变换渲染：
        - ledger: 行为切片账本（含主链 4 的 slice_status 任务状态）
        - slices: 行为切片列表（含每个任务的 status）
        - execution.production_data_blocked: 主链 5/6 生产数据安全边界拦截计数
        - evidence_chains_saved: 主链 7 已落地证据链计数
        无任务数据（既无 ledger 也无 slices）时返回 None，前端显示空态。
        """
        if not isinstance(report, dict):
            return None
        ledger = report.get("behavior_slice_ledger")
        ledger = ledger if isinstance(ledger, dict) else {}
        slices = report.get("behavior_slices")
        slices = slices if isinstance(slices, list) else []
        if not ledger and not slices:
            return None
        phases = report.get("phases") if isinstance(report.get("phases"), dict) else {}
        execution = phases.get("execution") if isinstance(phases.get("execution"), dict) else {}
        oracle = phases.get("oracle") if isinstance(phases.get("oracle"), dict) else {}

        def _int(value: Any) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        return {
            "ledger": dict(ledger),
            "slices": [dict(item) for item in slices if isinstance(item, dict)],
            "execution": {"production_data_blocked": _int(execution.get("production_data_blocked"))},
            "evidence_chains_saved": _int(oracle.get("evidence_chains_saved")),
        }

    def _scan_counter(self, project_id: str, root: Path) -> dict:
        """Track how many times V12 scan has run for this project."""
        import time
        counter_path = root / "platform_outputs" / project_id / "scan_counter.json"
        if counter_path.exists():
            return _read_json_object(counter_path)
        return {"count": 1, "first_scan_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}

    def _previous_finding_titles(self, project_id: str, root: Path) -> set:
        """Read previous scan findings from DB for convergence tracking."""
        try:
            db_persist.init_db(root)
            import sqlite3
            db_path = root / db_persist.DB_FILENAME
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            # Try both the project_id and a locale-agnostic normalized form
            # (strip common company-suffix tokens in any language, not only 科技).
            _project_aliases = {
                str(project_id),
                re.sub(r"(科技|技术|软件|信息|集团|有限公司|股份|inc|ltd|llc|corp|co)$", "", str(project_id), flags=re.I).strip("-_ "),
            }
            _project_aliases = {item for item in _project_aliases if item}
            _placeholders = ",".join("?" for _ in range(len(_project_aliases) or 1))
            rows = conn.execute(
                f"SELECT title FROM findings WHERE tenant_id IN (?, ?) AND project_id IN ({_placeholders}) ORDER BY created_at",
                (self._request_tenant(), "default", *sorted(_project_aliases)),
            ).fetchall()
            conn.close()
            return {r["title"][:120].lower() for r in rows}
        except Exception:
            return set()

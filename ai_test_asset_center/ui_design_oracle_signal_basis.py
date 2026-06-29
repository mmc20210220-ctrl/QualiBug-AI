from __future__ import annotations

from typing import Any


UI_DESIGN_ORACLE_SIGNAL_BASIS_BUCKETS: tuple[str, ...] = ("testid", "keyword", "role", "token", "none", "other")


def normalize_ui_design_oracle_signal_basis(confidence_basis: Any) -> str:
    raw = str(confidence_basis or "").strip().lower()
    if raw.startswith("testid"):
        return "testid"
    if raw.startswith("keyword"):
        return "keyword"
    if raw.startswith("role"):
        return "role"
    if raw.startswith("token"):
        return "token"
    if not raw:
        return "none"
    return "other"


def build_ui_design_oracle_signal_basis_legend() -> dict[str, dict[str, Any]]:
    return {
        "testid": {
            "label": "TestId 强信号",
            "meaning": "命中稳定的 data-testid / selector 标识，跨环境稳定，可自动化回归。",
            "recommended_actions": ["补齐/规范 data-testid", "将关键组件入口写入 match_hints.testids"],
        },
        "keyword": {
            "label": "关键词中信号",
            "meaning": "主要依赖文案/关键词匹配，容易受文案改动/多语言影响。",
            "recommended_actions": ["补齐稳定关键词（match_hints.keywords）", "优先补 data-testid 降低文案漂移风险"],
        },
        "role": {
            "label": "ARIA/Role 弱信号",
            "meaning": "主要依赖 role / aria-label / name 等结构化可访问性语义，强度取决于前端可访问性实现质量。",
            "recommended_actions": ["补齐 role/aria-label/name", "为关键 CTA 补 data-testid 形成强信号"],
        },
        "token": {
            "label": "Token 兜底信号",
            "meaning": "仅命中 token 文本兜底，误报风险更高，难以定位具体组件。",
            "recommended_actions": ["将 token 升级为 keywords/testids", "补齐结构化标识（data-testid/aria-label）"],
        },
        "none": {
            "label": "不可解释信号",
            "meaning": "缺少 confidence_basis 或 evidence 字段不完整，属于可观测性问题。",
            "recommended_actions": ["排查 element_map/evidence 产出链路", "补齐 issue evidence.confidence_basis"],
        },
        "other": {
            "label": "未知信号",
            "meaning": "出现了未纳入枚举的 basis，需抽样确认来源并决定是否升级枚举。",
            "recommended_actions": ["抽样检查 evidence.confidence_basis", "必要时扩展 normalize 规则或补齐上游字段"],
        },
    }


def recommend_ui_design_oracle_next_actions(
    distribution: dict[str, int] | None,
    legend: dict[str, dict[str, Any]] | None = None,
    *,
    limit: int = 5,
) -> list[str]:
    if not isinstance(distribution, dict):
        return []
    legend = legend if isinstance(legend, dict) else build_ui_design_oracle_signal_basis_legend()
    ranked = sorted(
        [(str(bucket), int(count or 0)) for bucket, count in distribution.items()],
        key=lambda item: (-item[1], item[0]),
    )
    actions: list[str] = []
    seen: set[str] = set()
    for bucket, count in ranked:
        if count <= 0:
            continue
        if bucket == "testid":
            continue
        meta = legend.get(bucket) if isinstance(legend.get(bucket), dict) else {}
        candidates = meta.get("recommended_actions") if isinstance(meta.get("recommended_actions"), list) else []
        for candidate in candidates:
            action = str(candidate).strip()
            if not action or action in seen:
                continue
            seen.add(action)
            actions.append(action)
            if len(actions) >= max(1, int(limit or 5)):
                return actions
    return actions


def build_ui_design_oracle_action_reasons(
    distribution: dict[str, int] | None,
    legend: dict[str, dict[str, Any]] | None = None,
    *,
    limit: int = 5,
) -> dict[str, Any]:
    distribution = distribution if isinstance(distribution, dict) else {}
    legend = legend if isinstance(legend, dict) else build_ui_design_oracle_signal_basis_legend()
    ranked = sorted(
        [(str(bucket), int(count or 0)) for bucket, count in distribution.items()],
        key=lambda item: (-item[1], item[0]),
    )
    top_buckets: list[dict[str, Any]] = []
    for bucket, count in ranked:
        if count <= 0:
            continue
        meta = legend.get(bucket) if isinstance(legend.get(bucket), dict) else {}
        top_buckets.append(
            {
                "bucket": bucket,
                "count": count,
                "label": meta.get("label") or bucket,
                "meaning": meta.get("meaning") or "",
            }
        )
        if len(top_buckets) >= 3:
            break
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bucket, count in ranked:
        if count <= 0:
            continue
        if bucket == "testid":
            continue
        meta = legend.get(bucket) if isinstance(legend.get(bucket), dict) else {}
        candidates = meta.get("recommended_actions") if isinstance(meta.get("recommended_actions"), list) else []
        for candidate in candidates:
            action = str(candidate).strip()
            if not action:
                continue
            if action in seen:
                for existing in actions:
                    if isinstance(existing, dict) and existing.get("action") == action:
                        triggered = existing.get("triggered_by") if isinstance(existing.get("triggered_by"), list) else []
                        triggered.append({"bucket": bucket, "count": count})
                        existing["triggered_by"] = triggered
                        break
                continue
            seen.add(action)
            actions.append(
                {
                    "action": action,
                    "triggered_by": [{"bucket": bucket, "count": count}],
                }
            )
            if len(actions) >= max(1, int(limit or 5)):
                return {"top_buckets": top_buckets, "action_reasons": actions}
    return {"top_buckets": top_buckets, "action_reasons": actions}

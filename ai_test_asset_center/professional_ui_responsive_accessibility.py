"""Responsive and deterministic accessibility extensions for formal UI tests.

This module extends the existing professional read-only UI authority with:

* source-declared viewport changes inside one browser plan;
* source-declared color-scheme / reduced-motion emulation;
* a bounded DOM accessibility-basics audit.

The accessibility audit is a deterministic defect check, not a full standards
certification and not an AI usability opinion. A contract explicitly selects
which rules are authoritative. Only counts and fingerprints enter receipts;
page text, labels and DOM fragments do not.
"""
from __future__ import annotations

from typing import Any

from . import formal_ui_surface as _formal
from . import formal_ui_surface_guard as _guard
from . import professional_ui_readonly as _professional
from . import scan_ui_contract_overlay as _overlay
from . import source_ui_contract_binding as _source_binding

_INSTALL_MARKER = "_qualibug_professional_ui_responsive_accessibility_installed"
_ORIGINAL_VALIDATE = "_qualibug_professional_validator_before_responsive"
_ORIGINAL_EXECUTE = "_qualibug_professional_executor_before_responsive"
_ORIGINAL_PLAN_VALIDATE = (
    "_qualibug_professional_plan_validator_before_responsive"
)

CONFIG_ACTIONS = frozenset({"set_viewport", "set_media"})
ACCESSIBILITY_ACTION = "expect_accessibility_basics"
ACCESSIBILITY_RULES = frozenset({
    "document_title",
    "html_lang",
    "unique_ids",
    "images_have_alt",
    "buttons_have_name",
    "links_have_name",
    "form_controls_have_name",
    "heading_order",
    "no_positive_tabindex",
})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _positive_int(value: Any, *, minimum: int, maximum: int, code: str) -> int:
    if isinstance(value, bool):
        raise _professional._browser.BrowserExecutionError(code)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise _professional._browser.BrowserExecutionError(code) from exc
    if not minimum <= number <= maximum:
        raise _professional._browser.BrowserExecutionError(code)
    return number


def _validate_extension_step(raw: dict[str, Any], action: str) -> None:
    if action == "set_viewport":
        raw["width"] = _positive_int(
            raw.get("width"),
            minimum=240,
            maximum=7680,
            code="browser_viewport_width_invalid",
        )
        raw["height"] = _positive_int(
            raw.get("height"),
            minimum=240,
            maximum=4320,
            code="browser_viewport_height_invalid",
        )
        return
    if action == "set_media":
        color = _text(raw.get("color_scheme") or "no-preference").lower()
        motion = _text(raw.get("reduced_motion") or "no-preference").lower()
        if color not in {"light", "dark", "no-preference"}:
            raise _professional._browser.BrowserExecutionError(
                "browser_color_scheme_invalid"
            )
        if motion not in {"reduce", "no-preference"}:
            raise _professional._browser.BrowserExecutionError(
                "browser_reduced_motion_invalid"
            )
        raw["color_scheme"] = color
        raw["reduced_motion"] = motion
        return
    if action == ACCESSIBILITY_ACTION:
        rules = [_text(value).lower() for value in _list(raw.get("rules"))]
        if not rules:
            raise _professional._browser.BrowserExecutionError(
                "browser_accessibility_rules_missing"
            )
        unknown = sorted(set(rules) - ACCESSIBILITY_RULES)
        if unknown:
            raise _professional._browser.BrowserExecutionError(
                "browser_accessibility_rules_unsupported:" + ",".join(unknown)
            )
        raw["rules"] = list(dict.fromkeys(rules))
        raw["max_violations"] = _positive_int(
            raw.get("max_violations", 0),
            minimum=0,
            maximum=1000,
            code="browser_accessibility_violation_budget_invalid",
        )


def _accessibility_audit(page: Any, rules: list[str]) -> dict[str, Any]:
    return _dict(page.evaluate(
        r"""rules => {
          const selected = new Set(rules || []);
          const findings = [];
          const push = (rule, el, extra='') => {
            if (!selected.has(rule)) return;
            findings.push({
              rule,
              tag: (el?.tagName || '').toLowerCase(),
              id: el?.id || '',
              type: el?.getAttribute?.('type') || '',
              extra: String(extra || '').slice(0, 80)
            });
          };
          const nameOf = el => {
            const ids = (el.getAttribute('aria-labelledby') || '')
              .split(/\s+/).filter(Boolean);
            const labelled = ids.map(id => document.getElementById(id)?.textContent || '')
              .join(' ').trim();
            const labels = el.labels ? Array.from(el.labels)
              .map(label => label.textContent || '').join(' ').trim() : '';
            return (el.getAttribute('aria-label') || labelled || labels ||
              el.getAttribute('alt') || el.getAttribute('title') ||
              el.textContent || '').trim();
          };
          if (selected.has('document_title') && !(document.title || '').trim()) {
            push('document_title', document.documentElement);
          }
          if (selected.has('html_lang') && !(document.documentElement.lang || '').trim()) {
            push('html_lang', document.documentElement);
          }
          if (selected.has('unique_ids')) {
            const seen = new Map();
            document.querySelectorAll('[id]').forEach(el => {
              const id = el.id;
              seen.set(id, (seen.get(id) || 0) + 1);
            });
            seen.forEach((count, id) => {
              if (count > 1) push('unique_ids', document.getElementById(id), count);
            });
          }
          if (selected.has('images_have_alt')) {
            document.querySelectorAll('img').forEach(el => {
              const decorative = el.getAttribute('role') === 'presentation' ||
                el.getAttribute('aria-hidden') === 'true';
              if (!decorative && !el.hasAttribute('alt')) push('images_have_alt', el);
            });
          }
          if (selected.has('buttons_have_name')) {
            document.querySelectorAll('button,input[type=button],input[type=submit],input[type=reset]')
              .forEach(el => { if (!nameOf(el)) push('buttons_have_name', el); });
          }
          if (selected.has('links_have_name')) {
            document.querySelectorAll('a[href]')
              .forEach(el => { if (!nameOf(el)) push('links_have_name', el); });
          }
          if (selected.has('form_controls_have_name')) {
            document.querySelectorAll('input:not([type=hidden]),select,textarea')
              .forEach(el => { if (!nameOf(el)) push('form_controls_have_name', el); });
          }
          if (selected.has('heading_order')) {
            let previous = 0;
            document.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(el => {
              const level = Number(el.tagName.slice(1));
              if (previous && level > previous + 1) push('heading_order', el, `${previous}->${level}`);
              previous = level;
            });
          }
          if (selected.has('no_positive_tabindex')) {
            document.querySelectorAll('[tabindex]').forEach(el => {
              const value = Number(el.getAttribute('tabindex'));
              if (Number.isFinite(value) && value > 0) push('no_positive_tabindex', el, value);
            });
          }
          const counts = {};
          findings.forEach(row => { counts[row.rule] = (counts[row.rule] || 0) + 1; });
          return {counts, findings: findings.slice(0, 100), truncated: findings.length > 100};
        }""",
        rules,
    ))


def install_professional_ui_responsive_accessibility() -> None:
    if getattr(_professional, _INSTALL_MARKER, False):
        return
    original_validate = getattr(
        _professional,
        _ORIGINAL_VALIDATE,
        _professional._validate_professional_step,
    )
    original_execute = getattr(
        _professional,
        _ORIGINAL_EXECUTE,
        _professional._execute_expectation,
    )
    original_plan_validate = getattr(
        _professional,
        _ORIGINAL_PLAN_VALIDATE,
        _professional.validate_professional_browser_plan,
    )
    setattr(_professional, _ORIGINAL_VALIDATE, original_validate)
    setattr(_professional, _ORIGINAL_EXECUTE, original_execute)
    setattr(_professional, _ORIGINAL_PLAN_VALIDATE, original_plan_validate)

    def validate_with_responsive_accessibility(
        raw: dict[str, Any],
        action: str,
    ) -> None:
        if action in CONFIG_ACTIONS or action == ACCESSIBILITY_ACTION:
            _validate_extension_step(raw, action)
            return
        original_validate(raw, action)

    def validate_plan_with_responsive_configuration(
        plan: dict[str, Any],
        runtime_contract: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = original_plan_validate(plan, runtime_contract)
        for step in _list(normalized.get("steps")):
            if not isinstance(step, dict):
                continue
            action = _text(step.get("action")).lower()
            if action in CONFIG_ACTIONS:
                _validate_extension_step(step, action)
        return normalized

    def execute_with_responsive_accessibility(
        *,
        page: Any,
        step: dict[str, Any],
        console: list[dict[str, Any]],
        network: list[dict[str, Any]],
    ) -> dict[str, Any]:
        action = _text(step.get("action")).lower()
        if action == "set_viewport":
            page.set_viewport_size({
                "width": int(step["width"]),
                "height": int(step["height"]),
            })
            return {
                "configuration": "viewport",
                "width": int(step["width"]),
                "height": int(step["height"]),
                "raw_observed_value_included": False,
            }
        if action == "set_media":
            page.emulate_media(
                color_scheme=_text(step.get("color_scheme")),
                reduced_motion=_text(step.get("reduced_motion")),
            )
            return {
                "configuration": "media",
                "color_scheme": _text(step.get("color_scheme")),
                "reduced_motion": _text(step.get("reduced_motion")),
                "raw_observed_value_included": False,
            }
        if action == ACCESSIBILITY_ACTION:
            rules = [_text(value).lower() for value in _list(step.get("rules"))]
            audit = _accessibility_audit(page, rules)
            counts = {
                _text(key): int(value or 0)
                for key, value in _dict(audit.get("counts")).items()
                if _text(key)
            }
            violation_count = sum(counts.values())
            fingerprints = [
                _professional._fingerprint(row)
                for row in _list(audit.get("findings"))
                if isinstance(row, dict)
            ]
            receipt = {
                "expectation": ACCESSIBILITY_ACTION,
                "rules": rules,
                "violation_counts": dict(sorted(counts.items())),
                "violation_count": violation_count,
                "violation_fingerprints": fingerprints,
                "finding_list_truncated": audit.get("truncated") is True,
                "max_violations": int(step.get("max_violations") or 0),
                "raw_dom_included": False,
                "raw_page_text_included": False,
            }
            if violation_count > int(step.get("max_violations") or 0):
                failed_rules = ",".join(sorted(counts))[:160]
                raise _professional.ProfessionalUIExpectationError(
                    ACCESSIBILITY_ACTION,
                    f"violation_budget_exceeded_{violation_count}_{failed_rules}",
                )
            return receipt
        return original_execute(
            page=page,
            step=step,
            console=console,
            network=network,
        )

    _professional.PROFESSIONAL_EXPECTATIONS = frozenset({
        *_professional.PROFESSIONAL_EXPECTATIONS,
        ACCESSIBILITY_ACTION,
    })
    _professional.READ_ONLY_ACTIONS = frozenset({
        *_professional.READ_ONLY_ACTIONS,
        *CONFIG_ACTIONS,
        ACCESSIBILITY_ACTION,
    })
    _professional._validate_professional_step = (
        validate_with_responsive_accessibility
    )
    _professional.validate_professional_browser_plan = (
        validate_plan_with_responsive_configuration
    )
    _professional._browser.validate_browser_plan = (
        validate_plan_with_responsive_configuration
    )
    _professional._execute_expectation = execute_with_responsive_accessibility
    _formal._SUPPORTED_EXPECTATIONS = _professional.PROFESSIONAL_EXPECTATIONS
    _guard._READ_ONLY_ACTIONS = _professional.READ_ONLY_ACTIONS
    _overlay._EXPECTATION_ACTIONS = _professional.PROFESSIONAL_EXPECTATIONS
    _source_binding._EXPECTATION_ACTIONS = _professional.PROFESSIONAL_EXPECTATIONS
    setattr(_professional, _INSTALL_MARKER, True)


__all__ = [
    "ACCESSIBILITY_ACTION",
    "ACCESSIBILITY_RULES",
    "CONFIG_ACTIONS",
    "install_professional_ui_responsive_accessibility",
]

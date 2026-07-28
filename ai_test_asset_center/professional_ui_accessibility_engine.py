"""Deterministic WCAG-oriented accessibility rules for formal UI execution.

This module extends the existing source-declared UI assertion authority with
``expect_accessibility_rules``.  It is intentionally not a certification claim:
only rules that can be evaluated deterministically from the rendered DOM,
computed style and bounded keyboard traversal are included.  Complex or
truncated observations remain INDETERMINATE rather than being promoted to a
passing Oracle result.

No raw page text, accessible names or DOM fragments are persisted.  Formal
receipts contain rule identifiers, WCAG references, counts and stable element
fingerprints only.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from typing import Any

from . import formal_ui_surface as _formal
from . import formal_ui_surface_guard as _guard
from . import professional_ui_readonly as _professional
from . import scan_ui_contract_overlay as _overlay
from . import source_ui_contract_binding as _source_binding
from .enterprise_knowledge_center import _formal_ui_contracts as _contracts

ACTION = "expect_accessibility_rules"
STANDARD = "wcag22-aa-deterministic"
SCHEMA_VERSION = "qualibug.ui-accessibility-rules.v1"
WCAG_VERSION = "2.2"
_INSTALL_MARKER = "_qualibug_professional_ui_accessibility_engine_installed"
_ORIGINAL_VALIDATE = "_qualibug_professional_validator_before_accessibility_engine"
_ORIGINAL_EXECUTE = "_qualibug_professional_executor_before_accessibility_engine"
_ORIGINAL_DESCRIPTOR = "_qualibug_professional_descriptor_before_accessibility_engine"
_ORIGINAL_STRUCTURE_GAPS = "_qualibug_ui_structure_gaps_before_accessibility_engine"

IMPACTS = frozenset({"critical", "serious", "moderate", "minor"})

RULE_CATALOG: dict[str, dict[str, str]] = {
    "document_title": {"wcag": "2.4.2", "level": "A", "impact": "serious"},
    "html_lang": {"wcag": "3.1.1", "level": "A", "impact": "serious"},
    "images_have_alt": {"wcag": "1.1.1", "level": "A", "impact": "critical"},
    "buttons_have_name": {"wcag": "4.1.2", "level": "A", "impact": "critical"},
    "links_have_name": {"wcag": "2.4.4", "level": "A", "impact": "serious"},
    "form_controls_have_name": {"wcag": "4.1.2", "level": "A", "impact": "critical"},
    "iframe_has_title": {"wcag": "4.1.2", "level": "A", "impact": "serious"},
    "svg_has_name": {"wcag": "1.1.1", "level": "A", "impact": "serious"},
    "aria_hidden_focusable": {"wcag": "4.1.2", "level": "A", "impact": "serious"},
    "aria_reference_valid": {"wcag": "4.1.2", "level": "A", "impact": "serious"},
    "main_landmark_single": {"wcag": "1.3.1", "level": "A", "impact": "moderate"},
    "table_headers": {"wcag": "1.3.1", "level": "A", "impact": "serious"},
    "fieldset_legend": {"wcag": "1.3.1", "level": "A", "impact": "moderate"},
    "empty_heading": {"wcag": "2.4.6", "level": "AA", "impact": "moderate"},
    "heading_order": {"wcag": "1.3.1", "level": "A", "impact": "moderate"},
    "no_positive_tabindex": {"wcag": "2.4.3", "level": "A", "impact": "serious"},
    "nested_interactive": {"wcag": "4.1.2", "level": "A", "impact": "serious"},
    "label_in_name": {"wcag": "2.5.3", "level": "A", "impact": "serious"},
    "target_size_minimum": {"wcag": "2.5.8", "level": "AA", "impact": "serious"},
    "text_contrast_minimum": {"wcag": "1.4.3", "level": "AA", "impact": "serious"},
    "focus_visible": {"wcag": "2.4.7", "level": "AA", "impact": "serious"},
    "focus_not_obscured": {"wcag": "2.4.11", "level": "AA", "impact": "serious"},
    "skip_link_present": {"wcag": "2.4.1", "level": "A", "impact": "serious"},
}

STANDARD_RULES = tuple(RULE_CATALOG)
_DOM_RULES = frozenset(set(STANDARD_RULES) - {"focus_visible", "focus_not_obscured"})
_FOCUS_RULES = frozenset({"focus_visible", "focus_not_obscured"})
_MAX_RULES = len(RULE_CATALOG)
_MAX_EXCLUDE_SELECTORS = 50
_MAX_SELECTOR_LENGTH = 500


class AccessibilityObservationError(RuntimeError):
    """The selected accessibility property could not be observed completely."""

    def __init__(self, code: str) -> None:
        super().__init__(f"UI_ACCESSIBILITY_OBSERVATION_INCOMPLETE:{code}")
        self.code = code


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _fingerprint(value: Any) -> str:
    blob = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _bounded_int(
    value: Any,
    *,
    minimum: int,
    maximum: int,
    code: str,
) -> int:
    if isinstance(value, bool):
        raise _professional._browser.BrowserExecutionError(code)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise _professional._browser.BrowserExecutionError(code) from exc
    if not minimum <= number <= maximum:
        raise _professional._browser.BrowserExecutionError(code)
    return number


def _normalize_rules(step: dict[str, Any]) -> list[str]:
    standard = _text(step.get("standard"), limit=80).lower()
    supplied = [_text(value, limit=100).lower() for value in _list(step.get("rules"))]
    if standard and standard != STANDARD:
        raise _professional._browser.BrowserExecutionError(
            "browser_accessibility_standard_unsupported"
        )
    rules = list(STANDARD_RULES) if standard == STANDARD and not supplied else supplied
    if not rules:
        raise _professional._browser.BrowserExecutionError(
            "browser_accessibility_rules_missing"
        )
    rules = list(dict.fromkeys(value for value in rules if value))
    if len(rules) > _MAX_RULES:
        raise _professional._browser.BrowserExecutionError(
            "browser_accessibility_rule_count_invalid"
        )
    unknown = sorted(set(rules) - set(RULE_CATALOG))
    if unknown:
        raise _professional._browser.BrowserExecutionError(
            "browser_accessibility_rules_unsupported:" + ",".join(unknown)
        )
    return rules


def _normalize_impact_budgets(value: Any) -> dict[str, int]:
    raw = _dict(value)
    unknown = sorted(set(raw) - IMPACTS)
    if unknown:
        raise _professional._browser.BrowserExecutionError(
            "browser_accessibility_impact_budget_unsupported:" + ",".join(unknown)
        )
    return {
        impact: _bounded_int(
            raw.get(impact, 0),
            minimum=0,
            maximum=10_000,
            code=f"browser_accessibility_{impact}_budget_invalid",
        )
        for impact in sorted(IMPACTS)
    }


def _validate_step(raw: dict[str, Any]) -> None:
    raw["standard"] = _text(raw.get("standard") or STANDARD, limit=80).lower()
    raw["rules"] = _normalize_rules(raw)
    raw["max_violations"] = _bounded_int(
        raw.get("max_violations", 0),
        minimum=0,
        maximum=10_000,
        code="browser_accessibility_violation_budget_invalid",
    )
    raw["impact_budgets"] = _normalize_impact_budgets(raw.get("impact_budgets"))
    raw["max_nodes"] = _bounded_int(
        raw.get("max_nodes", 2_000),
        minimum=50,
        maximum=10_000,
        code="browser_accessibility_max_nodes_invalid",
    )
    raw["max_focus_checks"] = _bounded_int(
        raw.get("max_focus_checks", 100),
        minimum=1,
        maximum=500,
        code="browser_accessibility_max_focus_checks_invalid",
    )
    if not isinstance(raw.get("require_complete_scan", True), bool):
        raise _professional._browser.BrowserExecutionError(
            "browser_accessibility_require_complete_scan_invalid"
        )
    raw["require_complete_scan"] = raw.get("require_complete_scan", True)
    allowed_untestable = [
        _text(value, limit=100).lower()
        for value in _list(raw.get("allowed_untestable_rules"))
    ]
    unknown_untestable = sorted(set(allowed_untestable) - set(raw["rules"]))
    if unknown_untestable:
        raise _professional._browser.BrowserExecutionError(
            "browser_accessibility_allowed_untestable_rule_invalid:"
            + ",".join(unknown_untestable)
        )
    raw["allowed_untestable_rules"] = list(dict.fromkeys(allowed_untestable))
    selectors = [_text(value, limit=_MAX_SELECTOR_LENGTH) for value in _list(raw.get("exclude_selectors"))]
    if len(selectors) > _MAX_EXCLUDE_SELECTORS or any(not value for value in selectors):
        raise _professional._browser.BrowserExecutionError(
            "browser_accessibility_exclude_selectors_invalid"
        )
    raw["exclude_selectors"] = list(dict.fromkeys(selectors))


_DOM_AUDIT_SCRIPT = r"""
config => {
  const selected = new Set(config.rules || []);
  const findings = [];
  const untestable = [];
  let visited = 0;
  let truncated = false;
  const maxNodes = Number(config.max_nodes || 2000);
  const excluded = (config.exclude_selectors || []).flatMap(selector => {
    try { return Array.from(document.querySelectorAll(selector)); } catch (_) { return []; }
  });
  const isExcluded = el => excluded.some(root => root === el || root.contains(el));
  const visible = el => {
    if (!el || !(el instanceof Element) || isExcluded(el)) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0;
  };
  const norm = value => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const node = el => ({
    tag: (el?.tagName || '').toLowerCase(),
    id: el?.id || '',
    role: el?.getAttribute?.('role') || '',
    type: el?.getAttribute?.('type') || '',
    nameSource: el?.hasAttribute?.('aria-label') ? 'aria-label' :
      el?.hasAttribute?.('aria-labelledby') ? 'aria-labelledby' :
      el?.hasAttribute?.('alt') ? 'alt' : el?.labels?.length ? 'label' : 'content',
    path: (() => {
      if (!el || !(el instanceof Element)) return '';
      const parts = [];
      let current = el;
      while (current && current !== document.documentElement && parts.length < 6) {
        const tag = current.tagName.toLowerCase();
        const parent = current.parentElement;
        const index = parent ? Array.from(parent.children).indexOf(current) + 1 : 1;
        parts.unshift(`${tag}:nth-child(${index})`);
        current = parent;
      }
      return parts.join('>');
    })()
  });
  const push = (rule, el, detail='') => {
    if (!selected.has(rule)) return;
    findings.push({rule, node: node(el), detail: String(detail || '').slice(0, 120)});
  };
  const unsure = (rule, reason, count=1) => {
    if (!selected.has(rule)) return;
    untestable.push({rule, reason: String(reason || '').slice(0, 120), count: Number(count || 1)});
  };
  const nameOf = el => {
    const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    const labelled = ids.map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
    const labels = el.labels ? Array.from(el.labels).map(label => label.textContent || '').join(' ').trim() : '';
    return (el.getAttribute('aria-label') || labelled || labels || el.getAttribute('alt') ||
      el.getAttribute('title') || el.textContent || '').trim();
  };
  const focusable = el => {
    if (!visible(el) || el.matches('[disabled],[inert],[aria-disabled="true"]')) return false;
    const tabindex = el.getAttribute('tabindex');
    if (tabindex !== null && Number(tabindex) < 0) return false;
    return el.matches('a[href],button,input:not([type=hidden]),select,textarea,summary,[contenteditable="true"],[tabindex]');
  };
  const interactiveSelector = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[contenteditable="true"],[role="button"],[role="link"],[role="checkbox"],[role="radio"],[role="switch"],[role="tab"],[tabindex]';

  if (selected.has('document_title') && !(document.title || '').trim()) push('document_title', document.documentElement);
  if (selected.has('html_lang') && !(document.documentElement.lang || '').trim()) push('html_lang', document.documentElement);

  const all = Array.from(document.querySelectorAll('*'));
  if (all.length > maxNodes) truncated = true;
  const nodes = all.slice(0, maxNodes);
  visited = nodes.length;

  if (selected.has('images_have_alt')) {
    nodes.filter(el => el.tagName === 'IMG' && visible(el)).forEach(el => {
      const decorative = el.getAttribute('role') === 'presentation' || el.getAttribute('aria-hidden') === 'true';
      if (!decorative && !el.hasAttribute('alt')) push('images_have_alt', el);
    });
  }
  if (selected.has('buttons_have_name')) {
    nodes.filter(el => el.matches('button,input[type=button],input[type=submit],input[type=reset],[role="button"]') && visible(el))
      .forEach(el => { if (!nameOf(el)) push('buttons_have_name', el); });
  }
  if (selected.has('links_have_name')) {
    nodes.filter(el => el.matches('a[href],[role="link"]') && visible(el))
      .forEach(el => { if (!nameOf(el)) push('links_have_name', el); });
  }
  if (selected.has('form_controls_have_name')) {
    nodes.filter(el => el.matches('input:not([type=hidden]),select,textarea') && visible(el))
      .forEach(el => { if (!nameOf(el)) push('form_controls_have_name', el); });
  }
  if (selected.has('iframe_has_title')) {
    nodes.filter(el => el.tagName === 'IFRAME' && visible(el))
      .forEach(el => { if (!norm(el.getAttribute('title'))) push('iframe_has_title', el); });
  }
  if (selected.has('svg_has_name')) {
    nodes.filter(el => el.tagName.toLowerCase() === 'svg' && visible(el) && el.getAttribute('aria-hidden') !== 'true')
      .forEach(el => { if (!nameOf(el)) push('svg_has_name', el); });
  }
  if (selected.has('aria_hidden_focusable')) {
    nodes.filter(el => el.getAttribute('aria-hidden') === 'true').forEach(root => {
      const candidates = [root, ...Array.from(root.querySelectorAll(interactiveSelector))];
      candidates.forEach(el => { if (focusable(el)) push('aria_hidden_focusable', el); });
    });
  }
  if (selected.has('aria_reference_valid')) {
    const attrs = ['aria-labelledby','aria-describedby','aria-controls','aria-owns','aria-details','aria-errormessage'];
    nodes.forEach(el => attrs.forEach(attr => {
      const ids = (el.getAttribute(attr) || '').split(/\s+/).filter(Boolean);
      ids.forEach(id => { if (!document.getElementById(id)) push('aria_reference_valid', el, attr); });
    }));
  }
  if (selected.has('main_landmark_single')) {
    const mains = nodes.filter(el => el.matches('main,[role="main"]') && visible(el));
    if (mains.length !== 1) push('main_landmark_single', mains[0] || document.documentElement, String(mains.length));
  }
  if (selected.has('table_headers')) {
    nodes.filter(el => el.tagName === 'TABLE' && visible(el) && !el.matches('[role="presentation"],[role="none"]'))
      .forEach(table => {
        const rows = table.querySelectorAll('tr').length;
        const cells = table.querySelectorAll('td').length;
        if (rows > 1 && cells > 1 && table.querySelectorAll('th,[role="columnheader"],[role="rowheader"]').length === 0) {
          push('table_headers', table);
        }
      });
  }
  if (selected.has('fieldset_legend')) {
    nodes.filter(el => el.tagName === 'FIELDSET' && visible(el)).forEach(fieldset => {
      if (fieldset.querySelector('input[type=radio],input[type=checkbox]')) {
        const legend = fieldset.querySelector(':scope > legend');
        if (!legend || !norm(legend.textContent)) push('fieldset_legend', fieldset);
      }
    });
  }
  if (selected.has('empty_heading')) {
    nodes.filter(el => /^H[1-6]$/.test(el.tagName) && visible(el))
      .forEach(el => { if (!nameOf(el)) push('empty_heading', el); });
  }
  if (selected.has('heading_order')) {
    let previous = 0;
    nodes.filter(el => /^H[1-6]$/.test(el.tagName) && visible(el)).forEach(el => {
      const level = Number(el.tagName.slice(1));
      if (previous && level > previous + 1) push('heading_order', el, `${previous}->${level}`);
      previous = level;
    });
  }
  if (selected.has('no_positive_tabindex')) {
    nodes.filter(el => el.hasAttribute('tabindex')).forEach(el => {
      const value = Number(el.getAttribute('tabindex'));
      if (Number.isFinite(value) && value > 0) push('no_positive_tabindex', el, String(value));
    });
  }
  if (selected.has('nested_interactive')) {
    nodes.filter(el => el.matches(interactiveSelector) && visible(el)).forEach(el => {
      const child = el.querySelector(interactiveSelector);
      if (child && visible(child)) push('nested_interactive', child);
    });
  }
  if (selected.has('label_in_name')) {
    nodes.filter(el => el.matches('button,a[href],[role="button"],[role="link"],input[type=button],input[type=submit]') && visible(el))
      .forEach(el => {
        const visibleLabel = norm(el.innerText || el.value || '');
        const accessible = norm(nameOf(el));
        if (visibleLabel && accessible && !accessible.includes(visibleLabel)) push('label_in_name', el);
      });
  }
  if (selected.has('target_size_minimum')) {
    const targets = nodes.filter(el => el.matches(interactiveSelector) && visible(el)).map(el => ({el, rect: el.getBoundingClientRect()}));
    targets.forEach(({el, rect}, index) => {
      if (rect.width >= 24 && rect.height >= 24) return;
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      const tooClose = targets.some((other, otherIndex) => {
        if (otherIndex === index) return false;
        const ocx = other.rect.left + other.rect.width / 2;
        const ocy = other.rect.top + other.rect.height / 2;
        return Math.abs(cx - ocx) < 24 && Math.abs(cy - ocy) < 24;
      });
      if (tooClose) push('target_size_minimum', el, `${Math.round(rect.width)}x${Math.round(rect.height)}`);
    });
  }
  if (selected.has('skip_link_present')) {
    const mains = nodes.filter(el => el.matches('main,[role="main"]') && visible(el));
    const links = nodes.filter(el => el.matches('a[href^="#"]') && visible(el));
    const valid = links.some(link => {
      const id = decodeURIComponent((link.getAttribute('href') || '').slice(1));
      const target = id ? document.getElementById(id) : null;
      return target && mains.some(main => target === main || main.contains(target));
    });
    if (!valid) push('skip_link_present', document.documentElement);
  }

  if (selected.has('text_contrast_minimum')) {
    const parse = value => {
      const match = String(value || '').match(/rgba?\(([^)]+)\)/i);
      if (!match) return null;
      const parts = match[1].split(',').map(value => Number(value.trim()));
      if (parts.length < 3 || parts.slice(0,3).some(Number.isNaN)) return null;
      return {r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1};
    };
    const blend = (fg, bg) => ({
      r: fg.r * fg.a + bg.r * (1 - fg.a),
      g: fg.g * fg.a + bg.g * (1 - fg.a),
      b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1
    });
    const luminance = color => {
      const channel = value => {
        const n = value / 255;
        return n <= 0.03928 ? n / 12.92 : Math.pow((n + 0.055) / 1.055, 2.4);
      };
      return 0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b);
    };
    const ratio = (a, b) => {
      const l1 = luminance(a), l2 = luminance(b);
      return (Math.max(l1,l2) + 0.05) / (Math.min(l1,l2) + 0.05);
    };
    let complex = 0;
    nodes.filter(el => visible(el) && Array.from(el.childNodes).some(n => n.nodeType === Node.TEXT_NODE && norm(n.textContent)))
      .forEach(el => {
        const style = getComputedStyle(el);
        let current = el;
        let background = {r:255,g:255,b:255,a:1};
        let found = false;
        while (current && current instanceof Element) {
          const cs = getComputedStyle(current);
          if (cs.backgroundImage && cs.backgroundImage !== 'none') { complex += 1; return; }
          const candidate = parse(cs.backgroundColor);
          if (candidate && candidate.a > 0) {
            background = candidate.a < 1 ? blend(candidate, background) : candidate;
            found = true;
            if (candidate.a >= 1) break;
          }
          current = current.parentElement;
        }
        const foreground = parse(style.color);
        if (!foreground || !found || Number(style.opacity || 1) < 1) { complex += 1; return; }
        const fg = foreground.a < 1 ? blend(foreground, background) : foreground;
        const size = Number.parseFloat(style.fontSize || '0');
        const weight = Number.parseInt(style.fontWeight || '400', 10) || 400;
        const threshold = size >= 24 || (size >= 18.66 && weight >= 700) ? 3 : 4.5;
        const actual = ratio(fg, background);
        if (actual + 0.001 < threshold) push('text_contrast_minimum', el, `${actual.toFixed(2)}/${threshold}`);
      });
    if (complex) unsure('text_contrast_minimum', 'complex_or_translucent_background', complex);
  }

  return {findings, untestable, visited, total: all.length, truncated};
}
"""


_FOCUS_CANDIDATE_SCRIPT = r"""
config => {
  const excluded = (config.exclude_selectors || []).flatMap(selector => {
    try { return Array.from(document.querySelectorAll(selector)); } catch (_) { return []; }
  });
  const isExcluded = el => excluded.some(root => root === el || root.contains(el));
  const visible = el => {
    if (!el || !(el instanceof Element) || isExcluded(el)) return false;
    const style = getComputedStyle(el), rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0;
  };
  return Array.from(document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,summary,[contenteditable="true"],[tabindex]'))
    .filter(el => visible(el) && !el.matches('[disabled],[inert],[aria-disabled="true"]') && Number(el.getAttribute('tabindex') || 0) >= 0)
    .length;
}
"""


_FOCUS_OBSERVATION_SCRIPT = r"""
() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return {active:false};
  const style = getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  const outlineWidth = Number.parseFloat(style.outlineWidth || '0') || 0;
  const borderWidth = ['Top','Right','Bottom','Left'].reduce((sum, side) => sum + (Number.parseFloat(style[`border${side}Width`] || '0') || 0), 0);
  const focusVisible = (() => { try { return el.matches(':focus-visible'); } catch (_) { return false; } })();
  const hasIndicator = focusVisible || (style.outlineStyle !== 'none' && outlineWidth > 0) ||
    (style.boxShadow && style.boxShadow !== 'none') || borderWidth > 0;
  const points = [
    [rect.left + rect.width / 2, rect.top + rect.height / 2],
    [rect.left + 1, rect.top + 1],
    [rect.right - 1, rect.bottom - 1]
  ].filter(([x,y]) => x >= 0 && y >= 0 && x < innerWidth && y < innerHeight);
  const exposed = points.some(([x,y]) => {
    const top = document.elementFromPoint(x,y);
    return top === el || !!(top && el.contains(top));
  });
  const path = (() => {
    const parts=[]; let current=el;
    while (current && current !== document.documentElement && parts.length < 6) {
      const parent=current.parentElement;
      const index=parent ? Array.from(parent.children).indexOf(current)+1 : 1;
      parts.unshift(`${current.tagName.toLowerCase()}:nth-child(${index})`);
      current=parent;
    }
    return parts.join('>');
  })();
  return {
    active:true,
    node:{tag:el.tagName.toLowerCase(),id:el.id||'',role:el.getAttribute('role')||'',type:el.getAttribute('type')||'',path},
    focusVisible:hasIndicator,
    exposed,
    scrollX:window.scrollX,
    scrollY:window.scrollY
  };
}
"""


def _dom_audit(page: Any, step: dict[str, Any]) -> dict[str, Any]:
    return _dict(page.evaluate(
        _DOM_AUDIT_SCRIPT,
        {
            "rules": [rule for rule in step["rules"] if rule in _DOM_RULES],
            "max_nodes": int(step["max_nodes"]),
            "exclude_selectors": list(step["exclude_selectors"]),
        },
    ))


def _focus_audit(page: Any, step: dict[str, Any]) -> dict[str, Any]:
    selected = set(step["rules"]) & _FOCUS_RULES
    if not selected:
        return {"findings": [], "untestable": [], "checked": 0, "truncated": False}
    start = _dict(page.evaluate("() => ({x: window.scrollX, y: window.scrollY})"))
    candidate_count = int(page.evaluate(
        _FOCUS_CANDIDATE_SCRIPT,
        {"exclude_selectors": list(step["exclude_selectors"])},
    ) or 0)
    limit = min(candidate_count, int(step["max_focus_checks"]))
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()
    checked = 0
    try:
        page.evaluate("() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); }")
        for _index in range(limit):
            page.keyboard.press("Tab")
            observation = _dict(page.evaluate(_FOCUS_OBSERVATION_SCRIPT))
            if observation.get("active") is not True:
                break
            node = _dict(observation.get("node"))
            identity = _fingerprint(node)
            if identity in seen:
                break
            seen.add(identity)
            checked += 1
            if "focus_visible" in selected and observation.get("focusVisible") is not True:
                findings.append({"rule": "focus_visible", "node": node, "detail": "indicator_missing"})
            if "focus_not_obscured" in selected and observation.get("exposed") is not True:
                findings.append({"rule": "focus_not_obscured", "node": node, "detail": "focused_element_obscured"})
    finally:
        page.evaluate(
            "point => { window.scrollTo(point.x || 0, point.y || 0); if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); }",
            {"x": float(start.get("x") or 0), "y": float(start.get("y") or 0)},
        )
    truncated = candidate_count > limit
    untestable = []
    if candidate_count == 0:
        untestable.extend(
            {"rule": rule, "reason": "no_keyboard_focus_candidates", "count": 1}
            for rule in sorted(selected)
        )
    elif truncated:
        untestable.extend(
            {"rule": rule, "reason": "focus_candidate_limit_exceeded", "count": candidate_count - limit}
            for rule in sorted(selected)
        )
    return {
        "findings": findings,
        "untestable": untestable,
        "checked": checked,
        "candidate_count": candidate_count,
        "truncated": truncated,
    }


def _finding_receipt(row: dict[str, Any]) -> dict[str, Any]:
    rule = _text(row.get("rule"), limit=100).lower()
    catalog = RULE_CATALOG[rule]
    return {
        "rule": rule,
        "wcag": catalog["wcag"],
        "level": catalog["level"],
        "impact": catalog["impact"],
        "node_fingerprint": _fingerprint(_dict(row.get("node"))),
        "detail_code": _text(row.get("detail"), limit=120),
    }


def _execute_engine(page: Any, step: dict[str, Any]) -> dict[str, Any]:
    dom = _dom_audit(page, step)
    focus = _focus_audit(page, step)
    findings = [
        _finding_receipt(row)
        for row in [*_list(dom.get("findings")), *_list(focus.get("findings"))]
        if isinstance(row, dict) and _text(row.get("rule")) in RULE_CATALOG
    ]
    untestable_rows = [
        dict(row)
        for row in [*_list(dom.get("untestable")), *_list(focus.get("untestable"))]
        if isinstance(row, dict) and _text(row.get("rule")) in RULE_CATALOG
    ]
    rule_counts = Counter(row["rule"] for row in findings)
    impact_counts = Counter(row["impact"] for row in findings)
    wcag_counts = Counter(row["wcag"] for row in findings)
    untestable_counts: Counter[str] = Counter()
    untestable_reason_counts: Counter[str] = Counter()
    for row in untestable_rows:
        count = max(1, int(row.get("count") or 1))
        untestable_counts[_text(row.get("rule"), limit=100)] += count
        untestable_reason_counts[_text(row.get("reason"), limit=120)] += count

    allowed_untestable = set(step["allowed_untestable_rules"])
    disallowed_untestable = {
        rule: count
        for rule, count in untestable_counts.items()
        if rule not in allowed_untestable
    }
    complete = not bool(dom.get("truncated")) and not bool(focus.get("truncated")) and not disallowed_untestable
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "expectation": ACTION,
        "standard": step["standard"],
        "wcag_version": WCAG_VERSION,
        "rules": list(step["rules"]),
        "rule_count": len(step["rules"]),
        "violation_count": len(findings),
        "violation_counts_by_rule": dict(sorted(rule_counts.items())),
        "violation_counts_by_impact": dict(sorted(impact_counts.items())),
        "violation_counts_by_wcag": dict(sorted(wcag_counts.items())),
        "violation_fingerprints": [_fingerprint(row) for row in findings[:500]],
        "finding_list_truncated": len(findings) > 500,
        "untestable_counts_by_rule": dict(sorted(untestable_counts.items())),
        "untestable_reason_counts": dict(sorted(untestable_reason_counts.items())),
        "allowed_untestable_rules": sorted(allowed_untestable),
        "complete_observation": complete,
        "dom_node_count": int(dom.get("total") or 0),
        "dom_nodes_evaluated": int(dom.get("visited") or 0),
        "keyboard_candidate_count": int(focus.get("candidate_count") or 0),
        "keyboard_candidates_evaluated": int(focus.get("checked") or 0),
        "max_violations": int(step["max_violations"]),
        "impact_budgets": copy.deepcopy(step["impact_budgets"]),
        "exclude_selector_fingerprints": [
            _fingerprint(value) for value in step["exclude_selectors"]
        ],
        "raw_dom_included": False,
        "raw_page_text_included": False,
        "raw_accessible_names_included": False,
        "ai_accessibility_judgement_used": False,
        "full_wcag_certification_claimed": False,
    }
    if step["require_complete_scan"] and not complete:
        blocked = ",".join(sorted(disallowed_untestable))[:160]
        code = "scan_truncated" if dom.get("truncated") or focus.get("truncated") else f"untestable_{blocked}"
        raise AccessibilityObservationError(code)
    exceeded_impacts = [
        impact
        for impact, count in impact_counts.items()
        if count > int(step["impact_budgets"].get(impact, 0))
    ]
    if len(findings) > int(step["max_violations"]) or exceeded_impacts:
        detail = "_".join(sorted(exceeded_impacts))[:100] or "total"
        raise _professional.ProfessionalUIExpectationError(
            ACTION,
            f"violation_budget_exceeded_{len(findings)}_{detail}",
        )
    return receipt


def _structure_gaps(expectations: list[dict[str, Any]]) -> list[str]:
    original = getattr(_contracts, _ORIGINAL_STRUCTURE_GAPS)
    missing = list(original(expectations))
    for index, step in enumerate(expectations, start=1):
        if _text(step.get("action"), limit=100).lower() != ACTION:
            continue
        prefix = f"{ACTION}[{index}]"
        standard = _text(step.get("standard"), limit=80).lower()
        rules = [_text(value, limit=100).lower() for value in _list(step.get("rules"))]
        if not standard and not rules:
            missing.append(f"{prefix}.standard_or_rules")
        if standard and standard != STANDARD:
            missing.append(f"{prefix}.standard={STANDARD}")
        unknown = sorted(set(rules) - set(RULE_CATALOG))
        if unknown:
            missing.append(f"{prefix}.supported_rules:" + ",".join(unknown))
        if "impact_budgets" in step and not isinstance(step.get("impact_budgets"), dict):
            missing.append(f"{prefix}.impact_budgets_object")
    return list(dict.fromkeys(missing))


def install_professional_ui_accessibility_engine() -> None:
    """Install one additive action on the existing formal UI authority."""
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
    original_descriptor = getattr(
        _professional,
        _ORIGINAL_DESCRIPTOR,
        _professional._descriptor,
    )
    original_structure = getattr(
        _contracts,
        _ORIGINAL_STRUCTURE_GAPS,
        _contracts._expectation_structure_gaps,
    )
    setattr(_professional, _ORIGINAL_VALIDATE, original_validate)
    setattr(_professional, _ORIGINAL_EXECUTE, original_execute)
    setattr(_professional, _ORIGINAL_DESCRIPTOR, original_descriptor)
    setattr(_contracts, _ORIGINAL_STRUCTURE_GAPS, original_structure)

    def validate_with_accessibility_engine(raw: dict[str, Any], action: str) -> None:
        if action == ACTION:
            _validate_step(raw)
            return
        original_validate(raw, action)

    def execute_with_accessibility_engine(
        *,
        page: Any,
        step: dict[str, Any],
        console: list[dict[str, Any]],
        network: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if _text(step.get("action"), limit=100).lower() == ACTION:
            return _execute_engine(page, step)
        return original_execute(page=page, step=step, console=console, network=network)

    def descriptor_with_accessibility_engine(step: dict[str, Any]) -> dict[str, Any]:
        if _text(step.get("action"), limit=100).lower() != ACTION:
            return original_descriptor(step)
        return {
            "action": ACTION,
            "standard": _text(step.get("standard"), limit=80),
            "rule_ids": [_text(value, limit=100) for value in _list(step.get("rules"))],
            "max_violations": int(step.get("max_violations") or 0),
            "impact_budgets": copy.deepcopy(_dict(step.get("impact_budgets"))),
            "require_complete_scan": step.get("require_complete_scan", True) is True,
            "exclude_selector_fingerprints": [
                _fingerprint(value) for value in _list(step.get("exclude_selectors"))
            ],
        }

    _professional.PROFESSIONAL_EXPECTATIONS = frozenset({
        *_professional.PROFESSIONAL_EXPECTATIONS,
        ACTION,
    })
    _professional.READ_ONLY_ACTIONS = frozenset({
        *_professional.READ_ONLY_ACTIONS,
        ACTION,
    })
    _professional._validate_professional_step = validate_with_accessibility_engine
    _professional._execute_expectation = execute_with_accessibility_engine
    _professional._descriptor = descriptor_with_accessibility_engine
    _formal._SUPPORTED_EXPECTATIONS = _professional.PROFESSIONAL_EXPECTATIONS
    _formal._expectation_descriptor = descriptor_with_accessibility_engine
    _guard._READ_ONLY_ACTIONS = _professional.READ_ONLY_ACTIONS
    _overlay._EXPECTATION_ACTIONS = _professional.PROFESSIONAL_EXPECTATIONS
    _source_binding._EXPECTATION_ACTIONS = _professional.PROFESSIONAL_EXPECTATIONS
    _contracts._EXPECTATION_ACTIONS = frozenset({*_contracts._EXPECTATION_ACTIONS, ACTION})
    _contracts._ALLOWED_ACTIONS = frozenset({*_contracts._ALLOWED_ACTIONS, ACTION})
    _contracts._expectation_structure_gaps = _structure_gaps
    setattr(_professional, _INSTALL_MARKER, True)


__all__ = [
    "ACTION",
    "AccessibilityObservationError",
    "RULE_CATALOG",
    "SCHEMA_VERSION",
    "STANDARD",
    "STANDARD_RULES",
    "WCAG_VERSION",
    "install_professional_ui_accessibility_engine",
]

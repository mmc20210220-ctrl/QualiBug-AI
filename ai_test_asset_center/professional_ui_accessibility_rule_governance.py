"""Conservative governance for deterministic accessibility rules.

The catalog contains every deterministic rule the runtime can execute. The default
``wcag22-aa-deterministic`` standard is a narrower high-confidence subset. Rules
with material WCAG exceptions or organisation-specific policy choices remain
available only through an explicit source-declared custom rule set.
"""
from __future__ import annotations

from . import professional_ui_accessibility_engine as _engine

_INSTALL_MARKER = "_qualibug_accessibility_rule_governance_installed"

_REMOVED_RULES = {
    "svg_has_name",
    "main_landmark_single",
    "table_headers",
    "heading_order",
    "skip_link_present",
}

_ADDED_RULES = {
    "role_img_has_name": {"wcag": "1.1.1", "level": "A", "impact": "serious"},
    "main_landmark_present": {"wcag": "1.3.1", "level": "A", "impact": "moderate"},
    "multiple_main_landmarks_named": {"wcag": "1.3.1", "level": "A", "impact": "moderate"},
    "explicit_data_table_headers": {"wcag": "1.3.1", "level": "A", "impact": "serious"},
    "bypass_blocks_mechanism": {"wcag": "2.4.1", "level": "A", "impact": "serious"},
}

CUSTOM_ONLY_RULES = frozenset({
    "main_landmark_present",
    "bypass_blocks_mechanism",
    "no_positive_tabindex",
    "target_size_minimum",
})

_SVG_OLD = """  if (selected.has('svg_has_name')) {
    nodes.filter(el => el.tagName.toLowerCase() === 'svg' && visible(el) && el.getAttribute('aria-hidden') !== 'true')
      .forEach(el => { if (!nameOf(el)) push('svg_has_name', el); });
  }
"""
_SVG_NEW = """  if (selected.has('role_img_has_name')) {
    nodes.filter(el => el.matches('svg[role="img"]') && visible(el) && el.getAttribute('aria-hidden') !== 'true')
      .forEach(el => { if (!nameOf(el)) push('role_img_has_name', el); });
  }
"""

_MAIN_OLD = """  if (selected.has('main_landmark_single')) {
    const mains = nodes.filter(el => el.matches('main,[role="main"]') && visible(el));
    if (mains.length !== 1) push('main_landmark_single', mains[0] || document.documentElement, String(mains.length));
  }
"""
_MAIN_NEW = """  if (selected.has('main_landmark_present') || selected.has('multiple_main_landmarks_named')) {
    const mains = nodes.filter(el => el.matches('main,[role="main"]') && visible(el));
    if (selected.has('main_landmark_present') && mains.length === 0) {
      push('main_landmark_present', document.documentElement, '0');
    }
    if (selected.has('multiple_main_landmarks_named') && mains.length > 1) {
      const labels = new Map();
      mains.forEach(main => {
        const name = norm(main.getAttribute('aria-label') ||
          (main.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean)
            .map(id => document.getElementById(id)?.textContent || '').join(' '));
        if (!name) {
          push('multiple_main_landmarks_named', main, 'name_missing');
          return;
        }
        labels.set(name, (labels.get(name) || 0) + 1);
      });
      labels.forEach((count, name) => {
        if (count > 1) {
          const duplicate = mains.find(main => norm(main.getAttribute('aria-label') ||
            (main.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean)
              .map(id => document.getElementById(id)?.textContent || '').join(' ')) === name);
          push('multiple_main_landmarks_named', duplicate || mains[0], 'name_duplicate');
        }
      });
    }
  }
"""

_TABLE_OLD = """  if (selected.has('table_headers')) {
    nodes.filter(el => el.tagName === 'TABLE' && visible(el) && !el.matches('[role="presentation"],[role="none"]'))
      .forEach(table => {
        const rows = table.querySelectorAll('tr').length;
        const cells = table.querySelectorAll('td').length;
        if (rows > 1 && cells > 1 && table.querySelectorAll('th,[role="columnheader"],[role="rowheader"]').length === 0) {
          push('table_headers', table);
        }
      });
  }
"""
_TABLE_NEW = """  if (selected.has('explicit_data_table_headers')) {
    nodes.filter(el => el.tagName === 'TABLE' && visible(el) && !el.matches('[role="presentation"],[role="none"]'))
      .filter(table => table.matches('[role="table"],[role="grid"],[data-table="true"],[aria-rowcount]') ||
        !!table.querySelector(':scope > caption,:scope > thead'))
      .forEach(table => {
        const rows = table.querySelectorAll('tr,[role="row"]').length;
        const cells = table.querySelectorAll('td,[role="cell"],[role="gridcell"]').length;
        if (rows > 1 && cells > 1 && table.querySelectorAll('th,[role="columnheader"],[role="rowheader"]').length === 0) {
          push('explicit_data_table_headers', table);
        }
      });
  }
"""

_HEADING_OLD = """  if (selected.has('heading_order')) {
    let previous = 0;
    nodes.filter(el => /^H[1-6]$/.test(el.tagName) && visible(el)).forEach(el => {
      const level = Number(el.tagName.slice(1));
      if (previous && level > previous + 1) push('heading_order', el, `${previous}->${level}`);
      previous = level;
    });
  }
"""

_BYPASS_OLD = """  if (selected.has('skip_link_present')) {
    const mains = nodes.filter(el => el.matches('main,[role="main"]') && visible(el));
    const links = nodes.filter(el => el.matches('a[href^="#"]') && visible(el));
    const valid = links.some(link => {
      const id = decodeURIComponent((link.getAttribute('href') || '').slice(1));
      const target = id ? document.getElementById(id) : null;
      return target && mains.some(main => target === main || main.contains(target));
    });
    if (!valid) push('skip_link_present', document.documentElement);
  }
"""
_BYPASS_NEW = """  if (selected.has('bypass_blocks_mechanism')) {
    const mains = nodes.filter(el => el.matches('main,[role="main"]') && visible(el));
    const links = nodes.filter(el => el.matches('a[href^="#"]') && visible(el));
    const skipLink = links.some(link => {
      const id = decodeURIComponent((link.getAttribute('href') || '').slice(1));
      const target = id ? document.getElementById(id) : null;
      return target && mains.some(main => target === main || main.contains(target));
    });
    if (!skipLink && mains.length === 0) {
      push('bypass_blocks_mechanism', document.documentElement);
    }
  }
"""

_TARGET_OLD = """    targets.forEach(({el, rect}, index) => {
      if (rect.width >= 24 && rect.height >= 24) return;
      const cx = rect.left + rect.width / 2;
"""
_TARGET_NEW = """    targets.forEach(({el, rect}, index) => {
      if (rect.width >= 24 && rect.height >= 24) return;
      const style = getComputedStyle(el);
      if (style.display === 'inline' && norm(el.textContent || '')) return;
      if (el.matches('input:not([type="button"]):not([type="submit"]):not([type="reset"]),select,textarea')) {
        unsure('target_size_minimum', 'native_user_agent_control_size', 1);
        return;
      }
      const cx = rect.left + rect.width / 2;
"""


def _replace_required(script: str, old: str, new: str, code: str) -> str:
    if old not in script:
        raise RuntimeError(f"accessibility_rule_governance_patch_missing:{code}")
    return script.replace(old, new, 1)


def install_professional_ui_accessibility_rule_governance() -> None:
    if getattr(_engine, _INSTALL_MARKER, False):
        return
    for rule in _REMOVED_RULES:
        _engine.RULE_CATALOG.pop(rule, None)
    _engine.RULE_CATALOG.update(_ADDED_RULES)
    _engine.CUSTOM_ONLY_RULES = CUSTOM_ONLY_RULES
    _engine.STANDARD_RULES = tuple(
        rule
        for rule in _engine.RULE_CATALOG
        if rule not in CUSTOM_ONLY_RULES
    )
    # Custom source contracts may execute every catalogued rule, even when a rule
    # is intentionally excluded from the default high-confidence standard.
    _engine._DOM_RULES = frozenset(
        set(_engine.RULE_CATALOG) - set(_engine._FOCUS_RULES)
    )
    _engine._MAX_RULES = len(_engine.RULE_CATALOG)

    script = _engine._DOM_AUDIT_SCRIPT
    script = _replace_required(script, _SVG_OLD, _SVG_NEW, "svg")
    script = _replace_required(script, _MAIN_OLD, _MAIN_NEW, "main")
    script = _replace_required(script, _TABLE_OLD, _TABLE_NEW, "table")
    script = _replace_required(script, _HEADING_OLD, "", "heading")
    script = _replace_required(script, _BYPASS_OLD, _BYPASS_NEW, "bypass")
    script = _replace_required(script, _TARGET_OLD, _TARGET_NEW, "target")
    _engine._DOM_AUDIT_SCRIPT = script
    setattr(_engine, _INSTALL_MARKER, True)


__all__ = [
    "CUSTOM_ONLY_RULES",
    "install_professional_ui_accessibility_rule_governance",
]

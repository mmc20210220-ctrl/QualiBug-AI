"""Add high-confidence deterministic ARIA semantics checks.

The rules are limited to rendered facts that can be established without an AI
opinion or an accessibility-tree heuristic. They extend the default deterministic
standard and therefore keep zero defect budget and complete-observation semantics.
"""
from __future__ import annotations

from . import professional_ui_accessibility_engine as _engine

_INSTALL_MARKER = "_qualibug_accessibility_aria_guard_installed"

ARIA_RULES = {
    "aria_reference_unique": {
        "wcag": "4.1.2",
        "level": "A",
        "impact": "serious",
    },
    "aria_state_value_valid": {
        "wcag": "4.1.2",
        "level": "A",
        "impact": "serious",
    },
    "aria_required_state_present": {
        "wcag": "4.1.2",
        "level": "A",
        "impact": "critical",
    },
    "interactive_role_focusable": {
        "wcag": "2.1.1",
        "level": "A",
        "impact": "critical",
    },
}

_INSERT_BEFORE = """  return {findings, untestable, visited, total: all.length, truncated};
}
"""

_ARIA_SCRIPT = r"""
  if (selected.has('aria_reference_unique')) {
    const referenced = new Set();
    const referenceAttrs = ['aria-labelledby','aria-describedby','aria-controls','aria-owns','aria-details','aria-errormessage'];
    nodes.forEach(el => referenceAttrs.forEach(attr => {
      (el.getAttribute(attr) || '').split(/\s+/).filter(Boolean).forEach(id => referenced.add(id));
    }));
    const idCounts = new Map();
    all.forEach(el => {
      if (el.id && referenced.has(el.id)) idCounts.set(el.id, (idCounts.get(el.id) || 0) + 1);
    });
    nodes.forEach(el => referenceAttrs.forEach(attr => {
      (el.getAttribute(attr) || '').split(/\s+/).filter(Boolean).forEach(id => {
        if ((idCounts.get(id) || 0) > 1) push('aria_reference_unique', el, attr);
      });
    }));
  }

  if (selected.has('aria_state_value_valid')) {
    const values = {
      'aria-hidden': new Set(['true','false']),
      'aria-expanded': new Set(['true','false','undefined']),
      'aria-selected': new Set(['true','false','undefined']),
      'aria-checked': new Set(['true','false','mixed','undefined']),
      'aria-pressed': new Set(['true','false','mixed','undefined']),
      'aria-current': new Set(['page','step','location','date','time','true','false']),
      'aria-live': new Set(['off','polite','assertive']),
      'aria-atomic': new Set(['true','false']),
      'aria-busy': new Set(['true','false']),
      'aria-invalid': new Set(['false','true','grammar','spelling']),
      'aria-modal': new Set(['true','false']),
      'aria-required': new Set(['true','false']),
      'aria-readonly': new Set(['true','false']),
      'aria-multiline': new Set(['true','false']),
      'aria-multiselectable': new Set(['true','false']),
      'aria-disabled': new Set(['true','false']),
      'aria-haspopup': new Set(['false','true','menu','listbox','tree','grid','dialog']),
      'aria-sort': new Set(['none','ascending','descending','other'])
    };
    nodes.forEach(el => {
      Object.entries(values).forEach(([attr, allowed]) => {
        if (!el.hasAttribute(attr)) return;
        const value = norm(el.getAttribute(attr)).toLowerCase();
        if (!allowed.has(value)) push('aria_state_value_valid', el, attr);
      });
      ['aria-level','aria-posinset','aria-colindex','aria-rowindex'].forEach(attr => {
        if (!el.hasAttribute(attr)) return;
        const value = Number(el.getAttribute(attr));
        if (!Number.isInteger(value) || value < 1) push('aria_state_value_valid', el, attr);
      });
      ['aria-setsize','aria-colcount','aria-rowcount'].forEach(attr => {
        if (!el.hasAttribute(attr)) return;
        const value = Number(el.getAttribute(attr));
        if (!Number.isInteger(value) || (value !== -1 && value < 1)) {
          push('aria_state_value_valid', el, attr);
        }
      });
      ['aria-valuenow','aria-valuemin','aria-valuemax'].forEach(attr => {
        if (!el.hasAttribute(attr)) return;
        const value = Number(el.getAttribute(attr));
        if (!Number.isFinite(value)) push('aria_state_value_valid', el, attr);
      });
    });
  }

  if (selected.has('aria_required_state_present')) {
    const required = new Map([
      ['checkbox', ['aria-checked']],
      ['radio', ['aria-checked']],
      ['switch', ['aria-checked']],
      ['slider', ['aria-valuenow']],
      ['spinbutton', ['aria-valuenow']],
      ['heading', ['aria-level']],
      ['combobox', ['aria-expanded']]
    ]);
    const nativeProvidesState = (el, role) => {
      if (role === 'checkbox' && el.matches('input[type="checkbox"]')) return true;
      if (role === 'radio' && el.matches('input[type="radio"]')) return true;
      if (role === 'spinbutton' && el.matches('input[type="number"]')) return true;
      if (role === 'heading' && /^H[1-6]$/.test(el.tagName)) return true;
      if (role === 'combobox' && el.matches('select,input[list]')) return true;
      return false;
    };
    nodes.filter(el => visible(el) && el.hasAttribute('role')).forEach(el => {
      const role = norm(el.getAttribute('role')).toLowerCase().split(/\s+/)[0];
      if (nativeProvidesState(el, role)) return;
      const attrs = required.get(role) || [];
      attrs.forEach(attr => {
        if (!el.hasAttribute(attr) || !norm(el.getAttribute(attr))) {
          push('aria_required_state_present', el, `${role}:${attr}`);
        }
      });
    });
  }

  if (selected.has('interactive_role_focusable')) {
    const roles = new Set(['button','link','checkbox','radio','switch']);
    nodes.filter(el => visible(el) && el.hasAttribute('role')).forEach(el => {
      const role = norm(el.getAttribute('role')).toLowerCase().split(/\s+/)[0];
      if (!roles.has(role) || el.matches('[disabled],[inert],[aria-disabled="true"]')) return;
      if (!focusable(el)) push('interactive_role_focusable', el, role);
    });
  }

"""


def install_professional_ui_accessibility_aria_guard() -> None:
    if getattr(_engine, _INSTALL_MARKER, False):
        return
    if _INSERT_BEFORE not in _engine._DOM_AUDIT_SCRIPT:
        raise RuntimeError("accessibility_aria_patch_anchor_missing")
    _engine.RULE_CATALOG.update(ARIA_RULES)
    _engine.STANDARD_RULES = tuple([
        *list(_engine.STANDARD_RULES),
        *(rule for rule in ARIA_RULES if rule not in _engine.STANDARD_RULES),
    ])
    _engine._DOM_RULES = frozenset({
        *_engine._DOM_RULES,
        *ARIA_RULES,
    })
    _engine._MAX_RULES = len(_engine.RULE_CATALOG)
    _engine._DOM_AUDIT_SCRIPT = _engine._DOM_AUDIT_SCRIPT.replace(
        _INSERT_BEFORE,
        _ARIA_SCRIPT + _INSERT_BEFORE,
        1,
    )
    setattr(_engine, _INSTALL_MARKER, True)


__all__ = [
    "ARIA_RULES",
    "install_professional_ui_accessibility_aria_guard",
]

export const ACCESSIBILITY_ACTION = 'expect_accessibility_rules' as const;
export const ACCESSIBILITY_STANDARD = 'wcag22-aa-deterministic' as const;

export type AccessibilityRuleId =
  | 'document_title'
  | 'html_lang'
  | 'images_have_alt'
  | 'buttons_have_name'
  | 'links_have_name'
  | 'form_controls_have_name'
  | 'iframe_has_title'
  | 'role_img_has_name'
  | 'aria_hidden_focusable'
  | 'aria_reference_valid'
  | 'multiple_main_landmarks_named'
  | 'explicit_data_table_headers'
  | 'fieldset_legend'
  | 'empty_heading'
  | 'nested_interactive'
  | 'label_in_name'
  | 'text_contrast_minimum'
  | 'focus_visible'
  | 'focus_not_obscured'
  | 'main_landmark_present'
  | 'bypass_blocks_mechanism'
  | 'no_positive_tabindex'
  | 'target_size_minimum';

export type AccessibilityRulePreset = {
  rule: AccessibilityRuleId;
  label: string;
  note: string;
  wcag: string;
  customOnly: boolean;
};

export type AccessibilityContractStep = {
  action: typeof ACCESSIBILITY_ACTION;
  standard?: typeof ACCESSIBILITY_STANDARD;
  rules?: AccessibilityRuleId[];
  require_complete_scan?: true;
  max_violations?: 0;
  impact_budgets?: {
    critical: 0;
    serious: 0;
    moderate: 0;
    minor: 0;
  };
  allowed_untestable_rules?: [];
  exclude_selectors?: [];
};

export const ACCESSIBILITY_RULE_PRESETS: AccessibilityRulePreset[] = [
  { rule: 'document_title', label: '文档标题', note: '页面必须具备非空标题', wcag: '2.4.2 A', customOnly: false },
  { rule: 'html_lang', label: '页面语言', note: '根元素必须声明语言', wcag: '3.1.1 A', customOnly: false },
  { rule: 'images_have_alt', label: '图片替代文本', note: '非装饰图片必须声明 alt', wcag: '1.1.1 A', customOnly: false },
  { rule: 'buttons_have_name', label: '按钮名称', note: '按钮必须具有可访问名称', wcag: '4.1.2 A', customOnly: false },
  { rule: 'links_have_name', label: '链接名称', note: '链接必须具有可访问名称', wcag: '2.4.4 A', customOnly: false },
  { rule: 'form_controls_have_name', label: '表单控件名称', note: '输入控件必须可被命名', wcag: '4.1.2 A', customOnly: false },
  { rule: 'iframe_has_title', label: 'iframe 标题', note: '可见 iframe 必须声明 title', wcag: '4.1.2 A', customOnly: false },
  { rule: 'role_img_has_name', label: '图像角色名称', note: 'role=img 的 SVG 必须命名', wcag: '1.1.1 A', customOnly: false },
  { rule: 'aria_hidden_focusable', label: '隐藏区焦点', note: 'aria-hidden 区域不能包含可聚焦对象', wcag: '4.1.2 A', customOnly: false },
  { rule: 'aria_reference_valid', label: 'ARIA 引用', note: 'ARIA ID 引用必须真实存在', wcag: '4.1.2 A', customOnly: false },
  { rule: 'multiple_main_landmarks_named', label: '多主区域命名', note: '多个 main landmark 必须唯一命名', wcag: '1.3.1 A', customOnly: false },
  { rule: 'explicit_data_table_headers', label: '数据表头', note: '明确的数据表必须具备表头语义', wcag: '1.3.1 A', customOnly: false },
  { rule: 'fieldset_legend', label: '分组图例', note: '单选/复选分组必须具备 legend', wcag: '1.3.1 A', customOnly: false },
  { rule: 'empty_heading', label: '空标题', note: '可见标题不能没有名称', wcag: '2.4.6 AA', customOnly: false },
  { rule: 'nested_interactive', label: '交互嵌套', note: '交互控件不能嵌套另一交互控件', wcag: '4.1.2 A', customOnly: false },
  { rule: 'label_in_name', label: '标签包含于名称', note: '可见标签必须包含在可访问名称中', wcag: '2.5.3 A', customOnly: false },
  { rule: 'text_contrast_minimum', label: '文本对比度', note: '可计算背景下验证 AA 对比度', wcag: '1.4.3 AA', customOnly: false },
  { rule: 'focus_visible', label: '焦点可见', note: '键盘焦点必须产生确定性样式变化', wcag: '2.4.7 AA', customOnly: false },
  { rule: 'focus_not_obscured', label: '焦点不被遮挡', note: '聚焦元素至少部分可见', wcag: '2.4.11 AA', customOnly: false },
  { rule: 'main_landmark_present', label: '主区域存在', note: '仅在来源明确要求 main landmark 时启用', wcag: '1.3.1 A', customOnly: true },
  { rule: 'bypass_blocks_mechanism', label: '绕过重复区块', note: '仅在来源明确存在重复区块时启用', wcag: '2.4.1 A', customOnly: true },
  { rule: 'no_positive_tabindex', label: '禁止正 tabindex', note: '属于组织键盘顺序政策，需要来源授权', wcag: '2.4.3 A', customOnly: true },
  { rule: 'target_size_minimum', label: '目标尺寸', note: '存在 WCAG 例外，需要来源明确启用', wcag: '2.5.8 AA', customOnly: true },
];

export const DEFAULT_CUSTOM_ACCESSIBILITY_RULES: AccessibilityRuleId[] = [
  'buttons_have_name',
  'links_have_name',
  'form_controls_have_name',
  'text_contrast_minimum',
  'focus_visible',
  'focus_not_obscured',
];

export function buildAccessibilityContractStep(
  mode: 'standard' | 'custom',
  selectedRules: AccessibilityRuleId[],
): AccessibilityContractStep {
  if (mode === 'standard') {
    return {
      action: ACCESSIBILITY_ACTION,
      standard: ACCESSIBILITY_STANDARD,
    };
  }

  const supported = new Set(ACCESSIBILITY_RULE_PRESETS.map((item) => item.rule));
  const rules = Array.from(new Set(selectedRules)).filter((rule) => supported.has(rule));
  if (rules.length === 0) {
    throw new Error('自定义无障碍合同至少需要一条规则。');
  }
  return {
    action: ACCESSIBILITY_ACTION,
    rules,
    require_complete_scan: true,
    max_violations: 0,
    impact_budgets: {
      critical: 0,
      serious: 0,
      moderate: 0,
      minor: 0,
    },
    allowed_untestable_rules: [],
    exclude_selectors: [],
  };
}

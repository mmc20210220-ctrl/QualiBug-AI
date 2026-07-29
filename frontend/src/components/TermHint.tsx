interface TermHintProps {
  label: string;
  hint: string;
}

/**
 * 行内术语解释：虚线下划线 + 悬停 / 聚焦气泡。
 * 用文字解释专业口径，不依赖颜色传达含义。
 */
export function TermHint({ label, hint }: TermHintProps) {
  return (
    <span className="term-hint" tabIndex={0} data-hint={hint} aria-label={`${label}：${hint}`}>
      {label}
    </span>
  );
}

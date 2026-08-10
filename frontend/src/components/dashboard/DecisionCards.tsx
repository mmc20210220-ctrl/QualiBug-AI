import { useMemo, useState } from 'react';
import { OPEN_TECHNICAL_DIAGNOSTICS_EVENT } from '../TechnicalDiagnostics';

interface DecisionCard {
  role: string;
  title: string;
  value: string;
  detail: string;
}

interface DecisionCardsProps {
  cards: DecisionCard[];
}

type RoleView = 'management' | 'quality' | 'technical';

const VIEW_META: Record<RoleView, { label: string; note: string }> = {
  management: { label: '管理视图', note: '发布风险、阻断等级和项目决策' },
  quality: { label: '测试视图', note: '证据、验收和回归闭环' },
  technical: { label: '技术视图', note: '执行链路、治理状态和技术诊断' },
};

function initialRoleView(): RoleView {
  try {
    const saved = window.sessionStorage.getItem('qualibug:dashboard-role-view');
    if (saved === 'management' || saved === 'quality' || saved === 'technical') return saved;
  } catch {
    // Role lens persistence is a convenience only.
  }
  return 'management';
}

function isQualityCard(card: DecisionCard): boolean {
  return /测试|质量|证据|验收/.test(`${card.role} ${card.title}`);
}

export function DecisionCards({ cards }: DecisionCardsProps) {
  const [view, setView] = useState<RoleView>(initialRoleView);

  const visibleCards = useMemo(() => {
    if (view === 'quality') return cards.filter(isQualityCard);
    if (view === 'management') return cards.filter((card) => !isQualityCard(card));
    return [];
  }, [cards, view]);

  if (!cards.length) return null;

  const chooseView = (next: RoleView) => {
    setView(next);
    try {
      window.sessionStorage.setItem('qualibug:dashboard-role-view', next);
    } catch {
      // Preference persistence must not block dashboard use.
    }
    if (next === 'technical') {
      window.dispatchEvent(new Event(OPEN_TECHNICAL_DIAGNOSTICS_EVENT));
    }
  };

  return (
    <section className="mb-4" aria-label="角色视图">
      <div className="focus-section-head">
        <div>
          <span className="panel-kicker">角色视图</span>
          <h2>按你当前关心的问题阅读同一份结果</h2>
        </div>
        <div className="settings-actions" role="tablist" aria-label="切换结果阅读视图">
          {(Object.keys(VIEW_META) as RoleView[]).map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={view === key}
              className={`btn ${view === key ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => chooseView(key)}
            >
              {VIEW_META[key].label}
            </button>
          ))}
        </div>
      </div>

      <p className="muted">{VIEW_META[view].note}。视图只改变信息优先级，不改变底层结果、风险口径或证据。</p>

      {view === 'technical' ? (
        <div className="decision-cards">
          <article className="decision-card">
            <span className="decision-card-role">研发 / 技术负责人</span>
            <h3>技术诊断已展开</h3>
            <strong>查看真实执行链路</strong>
            <p>下方技术诊断区继续使用同一轮真实数据，包含主链合同、检测治理、链路健康、耗时与证据可靠度，不复制第二套结论。</p>
            <button
              type="button"
              className="btn btn-secondary settings-btn-mini"
              onClick={() => window.dispatchEvent(new Event(OPEN_TECHNICAL_DIAGNOSTICS_EVENT))}
            >
              定位到技术诊断
            </button>
          </article>
        </div>
      ) : (
        <div className="decision-cards">
          {visibleCards.map((card) => (
            <article key={card.role} className="decision-card">
              <span className="decision-card-role">{card.role}</span>
              <h3>{card.title}</h3>
              <strong>{card.value}</strong>
              <p>{card.detail}</p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

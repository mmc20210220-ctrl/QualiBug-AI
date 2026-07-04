import { usePageTitle } from '../lib/page-title';

const tiers = [
  {
    name: '系统检测版',
    shortLabel: '检测',
    tag: '一次性体检',
    tone: 'indigo',
    scenario: '快速系统扫描，自动生成缺陷报告',
    cycle: '按次计费',
    price: '0.5 - 2 万/次',
    desc: '适合项目验收前的快速自检，无需部署，上传 API 文档即出报告。',
    features: ['单次全量扫描', '7天数据留存', '静态规范分析', '基础缺陷分类', 'HTML 报告导出'],
    missing: ['API 实时探测', 'DB 验证', '行业知识库', '持续监控', '集成对接'],
    highlight: false,
  },
  {
    name: 'SaaS 集成版',
    shortLabel: '集成',
    tag: '上线验收专用',
    tone: 'emerald',
    scenario: '集成商配合甲方做全栈行为验证，交付权威验收报告',
    cycle: '1 - 4 周',
    price: '3 - 8 万/次',
    desc: '覆盖 API / 数据库 / 业务规则 / 服务可用性全栈检测。合同执行期内完成行为空间建模+缺陷发现+证据链，直接交付甲方可审计报告。',
    features: ['全量 API 实时探测', '静态 + 运行时验证', '30天数据留存', '3 人协作', '验收报告导出', '基础模式库（14个）'],
    missing: ['DB 直连验证', '行业专属知识库', 'Jira/飞书集成', '持续定时监控'],
    highlight: true,
  },
  {
    name: 'SaaS 持续版',
    shortLabel: '持续',
    tag: '长期质量监控',
    tone: 'amber',
    scenario: '企业长期行为风险监控',
    cycle: '年度订阅',
    price: '15 - 50 万/年',
    desc: '全栈行为空间持续监控，每次发版自动扫描 API/数据库/业务规则，发现回归缺陷，积累行业缺陷模式。适合有独立 QA 团队的企业。',
    features: ['持续定时扫描', 'DB 直连验证', '永久数据留存', '不限用户', '完整模式库（23+）', 'Jira/飞书/企微集成', '行业专属知识库', '历史趋势对比', 'P0 实时告警'],
    missing: ['数据不出内网'],
    highlight: false,
  },
  {
    name: '私有部署版',
    shortLabel: '私有',
    tag: '内网高安全',
    tone: 'violet',
    scenario: '金融/政务/军工等高安全企业',
    cycle: '永久授权',
    price: '80 - 200 万',
    desc: '整套系统部署在客户内网，数据不外传。支持定制行业知识库和对接客户内部系统。',
    features: ['内网私有部署', '数据完全自控', '定制行业知识库', 'SSO/LDAP 集成', '对接内部工单系统', '完整模式库', 'API + DB + UI 全栈检测', '专属运维支持'],
    missing: [],
    highlight: false,
  },
];

const comparisonRows = [
  { label: '扫描方式', keys: ['单次快照', '单次全量', '持续定时', '持续定时'] },
  { label: 'API 实时探测', keys: ['—', '✅', '✅', '✅'] },
  { label: 'DB 直连验证', keys: ['—', '—', '✅', '✅'] },
  { label: 'UI 行为检测', keys: ['—', '—', '✅', '✅'] },
  { label: '行业知识库', keys: ['—', '通用', '行业专属', '定制专属'] },
  { label: '缺陷模式库', keys: ['—', '14个', '23+ 持续增长', '23+ 持续增长'] },
  { label: '数据留存', keys: ['7天', '30天', '永久云端', '客户自控'] },
  { label: '用户数', keys: ['1人', '3人', '不限', '不限'] },
  { label: '历史趋势', keys: ['—', '—', '✅', '✅'] },
  { label: 'P0 实时告警', keys: ['—', '—', '✅', '✅'] },
  { label: '工单集成', keys: ['—', '—', 'Jira/飞书/企微', '客户系统'] },
  { label: 'SSO / LDAP', keys: ['—', '—', '✅', '✅'] },
  { label: '部署方式', keys: ['云端共享', '云端独享', '云端独享', '客户内网'] },
  { label: '定价', keys: ['0.5-2万/次', '3-8万/次', '15-50万/年', '80-200万'] },
];

const customerPaths = [
  {
    title: '路径一：上线验收',
    route: '系统检测版 → SaaS 集成版',
    desc: '先用检测版快速自检，发现问题修复；上线前用集成版跑全栈验收，覆盖 API、数据库和业务规则，直接交付可审计报告。',
    tone: 'emerald',
  },
  {
    title: '路径二：持续质量',
    route: 'SaaS 集成版 → SaaS 持续版',
    desc: '验收完成后切换到持续版，围绕每次发版持续监控，沉淀历史趋势与行业缺陷模式。',
    tone: 'amber',
  },
  {
    title: '路径三：高安全合规',
    route: 'SaaS 持续版 → 私有部署版',
    desc: '先在云端验证效果，确认价值后整体迁移到客户内网，满足高安全、数据不出域的治理要求。',
    tone: 'violet',
  },
];

function capabilityTone(value: string) {
  if (value === '✅') return 'is-available';
  if (value === '—') return 'is-unavailable';
  return 'is-neutral';
}

export function Products() {
  usePageTitle('产品矩阵');
  const recommendedTier = tiers.find((tier) => tier.highlight) || tiers[0];
  const privateTier = tiers.find((tier) => tier.name.includes('私有'));
  const annualTier = tiers.find((tier) => tier.cycle.includes('年度'));
  const longestRetentionTier = tiers.find((tier) => tier.features.some((feature) => feature.includes('永久数据留存')));

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="panel-kicker">版本策略</span>
          <h1>产品矩阵</h1>
          <p>围绕验收、持续治理与高安全部署三个阶段，给出从单次验证到企业级风险治理的清晰产品路径。</p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">当前共 {tiers.length} 个版本</span>
            <span className="summary-pill">推荐方案 {recommendedTier.name}</span>
            <span className="summary-pill">高安全方案 {privateTier?.name || '私有部署版'}</span>
            <span className="summary-pill">长期治理 {annualTier?.name || 'SaaS 持续版'}</span>
          </div>
        </div>
      </div>

      <section className="products-hero mb-4">
        <div className="products-hero-main">
          <span className="products-hero-kicker">选型结论</span>
          <h2>默认推荐从 {recommendedTier.name} 起步，再按治理深度逐步升级</h2>
          <p>
            如果目标是验收交付，优先选择 {recommendedTier.name}；
            如果目标是长期质量治理，则向 {annualTier?.name || 'SaaS 持续版'} 演进；
            如需满足强监管或数据不出域，再升级到 {privateTier?.name || '私有部署版'}。
          </p>
        </div>
        <div className="products-hero-stats">
          <div className="products-hero-stat">
            <span>版本层级</span>
            <strong>{tiers.length}</strong>
            <small>覆盖验收到长期治理</small>
          </div>
          <div className="products-hero-stat">
            <span>推荐起点</span>
            <strong>{recommendedTier.shortLabel}</strong>
            <small>{recommendedTier.tag}</small>
          </div>
          <div className="products-hero-stat">
            <span>最长留存</span>
            <strong>{longestRetentionTier ? '永久' : '30天'}</strong>
            <small>{longestRetentionTier?.name || 'SaaS 集成版'}</small>
          </div>
          <div className="products-hero-stat">
            <span>最高安全</span>
            <strong>内网</strong>
            <small>{privateTier?.name || '私有部署版'}</small>
          </div>
        </div>
      </section>

      <section className="products-tier-grid mb-4">
        {tiers.map((tier) => (
          <article key={tier.name} className={`product-tier-card tone-${tier.tone}${tier.highlight ? ' is-highlight' : ''}`}>
            {tier.highlight && <span className="product-tier-ribbon">推荐</span>}
            <div className="product-tier-head">
              <span className="product-tier-emblem">{tier.shortLabel}</span>
              <div>
                <h2>{tier.name}</h2>
                <p>{tier.scenario}</p>
              </div>
            </div>
            <div className="product-tier-meta">
              <span className="product-tier-tag">{tier.tag}</span>
              <span className="product-tier-cycle">{tier.cycle}</span>
            </div>
            <p className="product-tier-desc">{tier.desc}</p>
            <div className="product-tier-panel">
              <div className="product-tier-panel-title">包含能力</div>
              <div className="product-tier-list">
                {tier.features.map((feature) => (
                  <div key={feature} className="product-tier-item">
                    <span className="product-tier-bullet">+</span>
                    <span>{feature}</span>
                  </div>
                ))}
              </div>
              {tier.missing.length > 0 && (
                <div className="product-tier-gap">
                  <div className="product-tier-panel-title subdued">暂不包含</div>
                  <div className="product-tier-muted-list">
                    {tier.missing.map((feature) => (
                      <div key={feature} className="product-tier-muted-item">{feature}</div>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div className="product-tier-price">
              <strong>{tier.price}</strong>
              <span>{tier.cycle}</span>
            </div>
          </article>
        ))}
      </section>

      <section className="section-card product-comparison-card">
        <div className="product-section-head">
          <div>
            <span className="panel-kicker">能力横比</span>
            <h2>功能对比</h2>
            <p>对照扫描深度、数据治理和集成能力，快速判断当前阶段适合哪一版。</p>
          </div>
          <span className="product-scroll-tip">左右滑动查看完整版本矩阵</span>
        </div>
        <div className="product-table-wrap">
          <table className="data-table product-data-table">
            <thead>
              <tr>
                <th className="product-capability-head">能力项</th>
                {tiers.map((tier) => (
                  <th key={tier.name} className={`product-column-head tone-${tier.tone}`}>
                    <span>{tier.shortLabel}</span>
                    <strong>{tier.name}</strong>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {comparisonRows.map((row) => (
                <tr key={row.label}>
                  <td className="product-row-label">{row.label}</td>
                  {row.keys.map((value, index) => (
                    <td key={index} className="product-cell">
                      <span className={`product-cell-badge ${capabilityTone(value)}`}>
                        {value === '✅' ? '支持' : value === '—' ? '未覆盖' : value}
                      </span>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="section-card">
        <div className="product-section-head">
          <div>
            <span className="panel-kicker">客户路径</span>
            <h2>升级路线</h2>
            <p>把一次性体检、上线验收、持续治理和高安全部署串成可执行的客户成长路线。</p>
          </div>
        </div>
        <div className="product-path-grid">
          {customerPaths.map((path) => (
            <article key={path.title} className={`product-path-card tone-${path.tone}`}>
              <span className="product-path-kicker">{path.title}</span>
              <strong>{path.route}</strong>
              <p>{path.desc}</p>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

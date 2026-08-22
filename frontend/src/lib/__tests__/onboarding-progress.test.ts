import { describe, expect, it } from 'vitest';
import { buildOnboardingSteps, type OnboardingFacts } from '../onboarding-progress';

function facts(overrides: Partial<OnboardingFacts> = {}): OnboardingFacts {
  return {
    enabledServiceCount: 0,
    authCount: 0,
    materialActiveCount: 0,
    knowledgeConnectorCount: 0,
    firstScanMaterialized: false,
    ...overrides,
  };
}

describe('buildOnboardingSteps 单一进度口径', () => {
  it('空事实：四步全待完成，第一步为当前步，不可运行', () => {
    const steps = buildOnboardingSteps(facts());
    expect(steps.map((s) => s.key)).toEqual(['system', 'auth', 'materials', 'first_scan']);
    expect(steps.every((s) => !s.done)).toBe(true);
    expect(steps[0].index).toBe(1);
    // 完成判定只来自事实，不因「在线连接器已连接」提前标记资料完成
    expect(steps[2].done).toBe(false);
  });

  it('系统+账号+资料就绪：前三步完成、readyToRun 成立、当前步=首次检测', () => {
    const steps = buildOnboardingSteps(facts({ enabledServiceCount: 1, authCount: 2, materialActiveCount: 3 }));
    expect(steps.filter((s) => s.done).map((s) => s.key)).toEqual(['system', 'auth', 'materials']);
    expect(steps[3].done).toBe(false);
  });

  it('首次检测完成：全部 done', () => {
    const steps = buildOnboardingSteps(facts({
      enabledServiceCount: 1, authCount: 1, materialActiveCount: 1, firstScanMaterialized: true,
    }));
    expect(steps.every((s) => s.done)).toBe(true);
  });

  it('在线连接器已连接但资料未物化：资料步仍不算完成（Connection Ready ≠ Material Ready）', () => {
    const steps = buildOnboardingSteps(facts({ knowledgeConnectorCount: 2 }));
    expect(steps[2].done).toBe(false);
    expect(steps[2].value).toContain('等待首次同步');
  });

  it('步骤值如实反映计数而非布尔装饰', () => {
    const steps = buildOnboardingSteps(facts({ enabledServiceCount: 3, authCount: 2 }));
    expect(steps[0].value).toBe('3 个服务可用');
    expect(steps[1].value).toBe('2 组可复用');
    expect(steps[3].value).toBe('尚未运行');
  });
});

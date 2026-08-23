/**
 * 单一 onboarding 进度口径（P1-4）。
 *
 * 全部页面的「首次接入走到哪了」都从这里取：四个主线步骤（系统地址 → 测试账号 →
 * 企业资料 → 首次检测）的完成判定只来自后端真实状态，前端不发明完成条件、
 * 不按页面各自定义步骤数。JourneyStrip 等消费方只渲染，不另行计算。
 */
import { useEffect, useReducer } from 'react';
import {
  getKnowledgeAsset,
  getServiceCredentials,
} from '../api/client';
import { listKnowledgeConnectors } from '../api/knowledge-connectors';
import { hasMaterializedFindingData, usePipelineSnapshot } from '../api/data';
import {
  extractMaterialCounts,
  extractServiceConfigs,
  hasConfiguredAuthMaterial,
  type SavedServiceConfig,
} from './settings-utils';

export type OnboardingStepKey = 'system' | 'auth' | 'materials' | 'first_scan';

export type OnboardingStep = {
  key: OnboardingStepKey;
  index: number;
  title: string;
  description: string;
  path: string;
  actionLabel: string;
  done: boolean;
  value: string;
};

export type OnboardingProgress = {
  steps: OnboardingStep[];
  completedCount: number;
  total: number;
  currentStep: OnboardingStep | null;
  /** 系统地址 + 测试账号 + 企业资料 全部就绪：可以进入运行前检查 */
  readyToRun: boolean;
  loading: boolean;
  warning: string;
};

export type OnboardingFacts = {
  enabledServiceCount: number;
  authCount: number;
  materialActiveCount: number;
  knowledgeConnectorCount: number;
  firstScanMaterialized: boolean;
};

function stepValue(facts: OnboardingFacts, key: OnboardingStepKey): string {
  switch (key) {
    case 'system':
      return facts.enabledServiceCount > 0 ? `${facts.enabledServiceCount} 个服务可用` : '待接入';
    case 'auth':
      return facts.authCount > 0 ? `${facts.authCount} 组可复用` : '待配置';
    case 'materials':
      return facts.materialActiveCount > 0
        ? facts.knowledgeConnectorCount > 0
          ? `${facts.materialActiveCount} 份资料（含在线来源）`
          : `${facts.materialActiveCount} 份资料已接入`
        : facts.knowledgeConnectorCount > 0
          ? '在线来源已连接 · 等待首次同步'
          : '待连接';
    case 'first_scan':
      return facts.firstScanMaterialized ? '已有真实结果' : '尚未运行';
    default:
      return '';
  }
}

/** 规范步骤构建器：完成判定与文案的唯一出处。 */
export function buildOnboardingSteps(facts: OnboardingFacts): OnboardingStep[] {
  const definitions: Array<Omit<OnboardingStep, 'index' | 'done' | 'value'>> = [
    {
      key: 'system',
      title: '接入被测系统',
      description: '至少接入一个测试环境地址，真实执行才能开始',
      path: '/settings',
      actionLabel: '前往系统与环境',
    },
    {
      key: 'auth',
      title: '补充测试身份',
      description: '账号、Token 或 API Key 用于真实登录业务链路',
      path: '/settings',
      actionLabel: '前往系统与环境',
    },
    {
      key: 'materials',
      title: '连接企业资料',
      description: '优先连接企业在线文档持续同步，缺失资料再用文件上传补充',
      path: '/materials',
      actionLabel: '连接资料源',
    },
    {
      key: 'first_scan',
      title: '运行前检查并检测',
      description: '先核对真实运行条件，通过后再执行标准扫描；阻断状态不会被绕过',
      path: '/campaigns',
      actionLabel: '检查并运行',
    },
  ];
  const doneByKey: Record<OnboardingStepKey, boolean> = {
    system: facts.enabledServiceCount > 0,
    auth: facts.authCount > 0,
    materials: facts.materialActiveCount > 0,
    first_scan: facts.firstScanMaterialized,
  };
  return definitions.map((definition, index) => ({
    ...definition,
    index: index + 1,
    done: doneByKey[definition.key],
    value: stepValue(facts, definition.key),
  }));
}

// ── 接入事实共享存储：每项目一个轮询循环，全部挂载点共用同一份快照 ─────────

type TrioEntry = {
  services: SavedServiceConfig[];
  materialActiveCount: number;
  knowledgeConnectorCount: number;
  failedReads: number;
  loaded: boolean;
  loading: boolean;
  fetching: boolean;
  timer: number | null;
  intervalMs: number;
  requests: Map<() => void, number>;
};

const TRIO_MIN_INTERVAL_MS = 10_000;
const TRIO_DEFAULT_INTERVAL_MS = 30_000;
const trioEntries = new Map<string, TrioEntry>();

async function fetchTrio(project: string, entry: TrioEntry): Promise<void> {
  if (entry.fetching || !project) return;
  entry.fetching = true;
  const [servicesResult, materialsResult, knowledgeConnectorsResult] = await Promise.allSettled([
    getServiceCredentials(project),
    getKnowledgeAsset(project),
    listKnowledgeConnectors(project),
  ]);
  // 单项读取失败保留上次成功值并计入 failedReads：不把读取失败解释成「未接入」。
  if (servicesResult.status === 'fulfilled') {
    entry.services = extractServiceConfigs(servicesResult.value);
  }
  if (materialsResult.status === 'fulfilled') {
    entry.materialActiveCount = extractMaterialCounts(materialsResult.value).materialCount;
  }
  if (knowledgeConnectorsResult.status === 'fulfilled') {
    entry.knowledgeConnectorCount = knowledgeConnectorsResult.value.connectors.length;
  }
  entry.failedReads = [servicesResult, materialsResult, knowledgeConnectorsResult]
    .filter((result) => result.status === 'rejected').length;
  entry.loaded = true;
  entry.loading = false;
  entry.fetching = false;
  entry.requests.forEach((_, notify) => notify());
}

function effectiveTrioIntervalMs(entry: TrioEntry): number {
  let min = Number.POSITIVE_INFINITY;
  entry.requests.forEach((ms) => { if (ms < min) min = ms; });
  return Number.isFinite(min) ? Math.max(TRIO_MIN_INTERVAL_MS, min) : TRIO_DEFAULT_INTERVAL_MS;
}

function scheduleTrioLoop(project: string, entry: TrioEntry): void {
  const nextInterval = effectiveTrioIntervalMs(entry);
  if (!entry.requests.size) {
    if (entry.timer !== null) { window.clearInterval(entry.timer); entry.timer = null; }
    entry.intervalMs = nextInterval;
    return;
  }
  if (entry.timer !== null && entry.intervalMs === nextInterval) return;
  if (entry.timer !== null) window.clearInterval(entry.timer);
  entry.intervalMs = nextInterval;
  entry.timer = window.setInterval(() => { void fetchTrio(project, entry); }, entry.intervalMs);
}

function createTrioEntry(): TrioEntry {
  return {
    services: [],
    materialActiveCount: 0,
    knowledgeConnectorCount: 0,
    failedReads: 0,
    loaded: false,
    loading: true,
    fetching: false,
    timer: null,
    intervalMs: TRIO_DEFAULT_INTERVAL_MS,
    requests: new Map(),
  };
}

function subscribeTrio(project: string, requestedIntervalMs: number, notify: () => void): () => void {
  let entry = trioEntries.get(project);
  if (!entry) {
    entry = createTrioEntry();
    trioEntries.set(project, entry);
  }
  entry.requests.set(notify, requestedIntervalMs);
  scheduleTrioLoop(project, entry);
  void fetchTrio(project, entry);
  return () => {
    entry.requests.delete(notify);
    scheduleTrioLoop(project, entry);
  };
}

/**
 * 全局进度主线。多页面同时挂载时只产生每项目一个接入事实轮询循环；
 * 「首次检测」事实复用 command-center 共享快照，零额外请求。
 */
export function useOnboardingProgress(project: string, requestedIntervalMs = 20_000): OnboardingProgress {
  const [, forceUpdate] = useReducer((count: number) => count + 1, 0);
  useEffect(() => {
    if (!project) return undefined;
    return subscribeTrio(project, requestedIntervalMs, forceUpdate);
  }, [project, requestedIntervalMs, forceUpdate]);

  const { raw } = usePipelineSnapshot(project, 60_000);
  const firstScanMaterialized = Boolean(project && raw && hasMaterializedFindingData(raw));

  const entry = project ? trioEntries.get(project) : undefined;
  // 步骤①「接入被测系统」的口径必须与接入页/运行前检查一致：
  // 被测服务凭据清单（base_url 非空且未停用），而不是企业资料连接器清单。
  const enabledServiceCount = (entry?.services ?? []).filter(
    (service) => service.enabled !== false && String(service.base_url || '').trim(),
  ).length;
  const facts: OnboardingFacts = {
    enabledServiceCount,
    authCount: (entry?.services ?? []).filter((service) => hasConfiguredAuthMaterial(service)).length,
    materialActiveCount: entry?.materialActiveCount ?? 0,
    knowledgeConnectorCount: entry?.knowledgeConnectorCount ?? 0,
    firstScanMaterialized,
  };
  const steps = buildOnboardingSteps(facts);
  const completedCount = steps.filter((step) => step.done).length;
  const currentStep = steps.find((step) => !step.done) ?? null;
  const readyToRun = steps.filter((step) => step.key !== 'first_scan').every((step) => step.done);
  const warning = (entry?.failedReads ?? 0) > 0
    ? '部分接入状态暂时无法读取，请重新核对后再判断是否已经完成接入。'
    : '';

  return {
    steps,
    completedCount,
    total: steps.length,
    currentStep,
    readyToRun,
    loading: Boolean(project) && !(entry?.loaded ?? false),
    warning,
  };
}

import type { ScanPreflight } from '../api/client';

export type RunPreflightTone = 'success' | 'warning' | 'danger' | 'neutral';
export type RunPreflightPrimaryAction = 'run' | 'refresh' | 'review' | 'wait';

export type RunPreflightFact = {
  label: string;
  value: string;
  detail: string;
  tone: RunPreflightTone;
};

export type RunPreflightPresentation = {
  tone: RunPreflightTone;
  headline: string;
  summary: string;
  authorityLabel: string;
  blockerLabel: string;
  blockerDetail: string;
  blockerCount: number;
  primaryAction: RunPreflightPrimaryAction;
  primaryActionLabel: string;
  submissionAllowed: boolean;
  facts: RunPreflightFact[];
};

type Input = {
  preflight: ScanPreflight | null;
  loadingPreflight: boolean;
  preflightError: string;
  enabledServiceCount: number;
  serviceError: string;
  configuredAuthCount: number;
  activeSourceCount: number;
  totalSourceCount: number;
  sourceError: string;
  forceReadOnly: boolean;
  scenarioLoading: boolean;
  scenarioError: string;
  scenarioCount: number;
};

function auxiliaryFact(
  label: string,
  error: string,
  positive: boolean,
  positiveValue: string,
  positiveDetail: string,
  emptyValue: string,
  emptyDetail: string,
): RunPreflightFact {
  if (error) {
    return {
      label,
      value: '无法确认',
      detail: `辅助状态读取失败：${error}`,
      tone: 'neutral',
    };
  }
  return positive
    ? { label, value: positiveValue, detail: positiveDetail, tone: 'success' }
    : { label, value: emptyValue, detail: emptyDetail, tone: 'warning' };
}

export function deriveRunPreflightPresentation(input: Input): RunPreflightPresentation {
  const blockers = input.preflight?.reasons || [];
  const preflightReady = Boolean(input.preflight?.ready);
  const scenarioBlocked = !input.forceReadOnly && (input.scenarioLoading || Boolean(input.scenarioError));
  const submissionAllowed = preflightReady && !scenarioBlocked && !input.loadingPreflight && !input.preflightError;

  const systemFact = auxiliaryFact(
    '目标系统接入',
    input.serviceError,
    input.enabledServiceCount > 0,
    `${input.enabledServiceCount} 个已启用`,
    '这里只说明已登记可用测试地址；真实连通性仍由后端运行前检查与实际执行确认。',
    '未观察到启用目标',
    '这里不会仅凭前端配置缺失断言后端一定不可运行，最终仍看 Preflight。',
  );

  const credentialFact = auxiliaryFact(
    '测试凭据配置',
    input.serviceError,
    input.configuredAuthCount > 0,
    `${input.configuredAuthCount} 组已配置`,
    '配置存在不等于登录已经验证通过；凭据是否可用于本次执行仍由后端 Preflight 决定。',
    '未观察到',
    '有些验证不需要登录；前端不会把“没有凭据”自行升级成运行阻断。',
  );

  const materialFact = auxiliaryFact(
    '企业资料输入',
    input.sourceError,
    input.activeSourceCount > 0,
    `${input.activeSourceCount} 份 active`,
    `${input.totalSourceCount} 份非删除资料中已有真实 active 输入；资料类型不限，由后端业务理解链路消费。`,
    input.totalSourceCount > 0 ? '暂无 active 资料' : '未观察到资料',
    input.totalSourceCount > 0
      ? '已存在资料记录，但当前没有 active source；是否构成运行阻断只以 Preflight 为准。'
      : '文件或在线资料是否为本次检测必需，只以 Preflight 的真实结论为准。',
  );

  const scenarioFact: RunPreflightFact = input.forceReadOnly
    ? {
        label: '审批写场景',
        value: '本次只读跳过',
        detail: '强制只读熔断已开启，本次不会提交审批上传写场景。',
        tone: 'neutral',
      }
    : input.scenarioLoading
      ? {
          label: '审批写场景',
          value: '可信同步中',
          detail: '前端暂不提交扫描请求，避免写场景身份尚未同步完成时产生错误绑定。',
          tone: 'neutral',
        }
      : input.scenarioError
        ? {
            label: '审批写场景',
            value: '同步失败',
            detail: input.scenarioError,
            tone: 'danger',
          }
        : {
            label: '审批写场景',
            value: input.scenarioCount > 0 ? `${input.scenarioCount} 个已同步` : '当前无活动场景',
            detail: input.scenarioCount > 0
              ? '已审批写场景将由运行合同再次确认；前端同步状态本身不代表执行成功。'
              : '普通接口、页面与只读验证不依赖审批上传场景。',
            tone: input.scenarioCount > 0 ? 'warning' : 'neutral',
          };

  const preflightFact: RunPreflightFact = input.loadingPreflight
    ? {
        label: '后端 Preflight',
        value: '检查中',
        detail: '正在读取后端唯一运行就绪结论。',
        tone: 'neutral',
      }
    : input.preflightError
      ? {
          label: '后端 Preflight',
          value: '无法确认',
          detail: input.preflightError,
          tone: 'danger',
        }
      : preflightReady
        ? {
            label: '后端 Preflight',
            value: '已通过',
            detail: '这是本页唯一可以解释为“后端运行条件通过”的权威状态。',
            tone: 'success',
          }
        : {
            label: '后端 Preflight',
            value: blockers.length > 0 ? `${blockers.length} 项阻断` : '未通过',
            detail: blockers.length > 0
              ? '后端已返回真实阻断原因，扫描请求不会提交。'
              : '后端未给出 ready=true；即使辅助事实看起来齐全，前端也不会放行。',
            tone: 'danger',
          };

  const facts = [systemFact, materialFact, credentialFact, scenarioFact, preflightFact];

  if (input.loadingPreflight) {
    return {
      tone: 'neutral',
      headline: '正在确认是否可以开始检测',
      summary: '系统接入、资料与凭据只作为辅助解释；是否可以运行正在等待后端 Preflight。',
      authorityLabel: '等待后端 Preflight',
      blockerLabel: '尚未形成运行结论',
      blockerDetail: '检查完成前不会提交扫描请求。',
      blockerCount: 0,
      primaryAction: 'wait',
      primaryActionLabel: '正在检查运行条件…',
      submissionAllowed: false,
      facts,
    };
  }

  if (input.preflightError) {
    return {
      tone: 'danger',
      headline: '当前无法确认是否可以开始检测',
      summary: '运行前检查读取失败不能解释为“条件已通过”，前端保持 fail-closed。',
      authorityLabel: 'Preflight 状态不可用',
      blockerLabel: '运行就绪状态读取失败',
      blockerDetail: input.preflightError,
      blockerCount: 0,
      primaryAction: 'refresh',
      primaryActionLabel: '重新检查运行条件',
      submissionAllowed: false,
      facts,
    };
  }

  if (!preflightReady) {
    const first = blockers[0];
    return {
      tone: 'danger',
      headline: '运行前检查未通过，暂不启动检测',
      summary: blockers.length > 0
        ? `后端报告 ${blockers.length} 项真实阻断；辅助接入状态不会覆盖这个结论。`
        : '后端没有返回 ready=true，前端不会因为资料、系统或凭据看起来齐全就自行放行。',
      authorityLabel: 'Preflight 未通过',
      blockerLabel: first?.message || '后端尚未确认可运行',
      blockerDetail: first
        ? `首个上报阻断代码：${first.code || '未提供'}${blockers.length > 1 ? `；另有 ${blockers.length - 1} 项阻断` : ''}`
        : '当前没有可展示的阻断明细，请重新检查或查看接入配置。',
      blockerCount: blockers.length,
      primaryAction: blockers.length > 0 ? 'review' : 'refresh',
      primaryActionLabel: blockers.length > 0 ? '查看运行阻断' : '重新检查运行条件',
      submissionAllowed: false,
      facts,
    };
  }

  if (!input.forceReadOnly && input.scenarioError) {
    return {
      tone: 'warning',
      headline: '后端运行条件已通过，但审批场景同步失败',
      summary: 'Preflight 已通过；当前仅因前端无法可信确认审批写场景身份而暂停提交，避免错绑写操作。',
      authorityLabel: 'Preflight 已通过',
      blockerLabel: '审批写场景可信同步失败',
      blockerDetail: input.scenarioError,
      blockerCount: 1,
      primaryAction: 'review',
      primaryActionLabel: '查看安全熔断选项',
      submissionAllowed: false,
      facts,
    };
  }

  if (!input.forceReadOnly && input.scenarioLoading) {
    return {
      tone: 'neutral',
      headline: '后端运行条件已通过，正在同步审批场景',
      summary: 'Preflight 已通过，但前端会等审批写场景可信同步完成后再提交扫描请求。',
      authorityLabel: 'Preflight 已通过',
      blockerLabel: '等待审批写场景同步',
      blockerDetail: '这不是新的后端 Preflight 结论，只是提交前的写场景身份一致性保护。',
      blockerCount: 0,
      primaryAction: 'wait',
      primaryActionLabel: '正在同步审批场景…',
      submissionAllowed: false,
      facts,
    };
  }

  return {
    tone: 'success',
    headline: '运行前检查已通过，可以开始检测',
    summary: '后端 Preflight 已明确 ready=true；辅助接入事实仅用于解释本次上下文，不参与替代运行权威。',
    authorityLabel: 'Preflight 已通过',
    blockerLabel: '当前无运行阻断',
    blockerDetail: input.forceReadOnly
      ? '本次启用了只读熔断，将跳过审批写场景。'
      : '扫描提交后仍可能由后端安全门禁产生阻断、仅计划或部分覆盖，结果会如实展示。',
    blockerCount: 0,
    primaryAction: 'run',
    primaryActionLabel: '执行标准扫描',
    submissionAllowed,
    facts,
  };
}

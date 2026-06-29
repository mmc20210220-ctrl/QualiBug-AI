"use server";

import "server-only";
import {
  getBusinessModel,
  getCommandCenterSnapshot,
  getEnvironmentReadiness,
  getExecutiveReport,
  getLiveMap,
  getOnboarding,
  getRiskDetail,
  getTestRun,
  getValueMetrics,
  listRisks,
} from "@/lib/api/command-center";
import { mapBehaviorSpaceVisualization } from "./mapper";
import type { BehaviorSpaceDataBundle, BehaviorSpaceVisualization } from "./types";

function pickRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function pickString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function pickBoolean(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (value === "true") return true;
  if (value === "false") return false;
  return null;
}

function unwrapData(value: unknown): unknown {
  const record = pickRecord(value);
  if (record && "success" in record && "data" in record) return record.data;
  return value;
}

async function optionalData<T>(fn: () => Promise<{ data: T }>): Promise<T | null> {
  try {
    const result = await fn();
    return result.data;
  } catch {
    return null;
  }
}

export async function getBehaviorSpaceDataBundle(projectId: string): Promise<BehaviorSpaceDataBundle> {
  const [commandCenter, valueMetrics, businessModel, environmentReadiness, risks, executiveReport, liveMap, onboarding] = await Promise.all([
    getCommandCenterSnapshot(projectId),
    optionalData(() => getValueMetrics(projectId)),
    optionalData(() => getBusinessModel(projectId)),
    optionalData(() => getEnvironmentReadiness(projectId)),
    optionalData(() => listRisks(projectId)),
    optionalData(() => getExecutiveReport(projectId)),
    optionalData(() => getLiveMap(projectId)),
    optionalData(() => getOnboarding(projectId)),
  ]);

  const liveMapRecord = pickRecord(unwrapData(liveMap)) ?? {};
  const commandCenterRecord = pickRecord(commandCenter.data) ?? {};
  const embeddedLiveMap = pickRecord(commandCenterRecord.live_map) ?? {};
  const runId = pickString(liveMapRecord.run_id) ?? pickString(embeddedLiveMap.run_id) ?? null;
  const testRun = runId ? await optionalData(() => getTestRun(projectId, runId)) : null;

  const riskItems = Array.isArray(unwrapData(risks)) ? (unwrapData(risks) as unknown[]) : [];
  const detailTargets = riskItems
    .map((item, index) => {
      const risk = pickRecord(item) ?? {};
      return {
        riskId: pickString(risk.risk_id) ?? `risk-${index + 1}`,
        launchBlocking: pickBoolean(risk.launch_blocking) === true,
      };
    })
    .sort((left, right) => Number(right.launchBlocking) - Number(left.launchBlocking))
    .slice(0, 3);

  const riskDetails = await Promise.all(
    detailTargets.map(async (target) => ({
      riskId: target.riskId,
      detail: await optionalData(() => getRiskDetail(projectId, target.riskId)),
    })),
  );

  return {
    projectId,
    commandCenter: commandCenter.data,
    valueMetrics,
    businessModel,
    environmentReadiness,
    risks,
    executiveReport,
    liveMap,
    onboarding,
    testRun,
    riskDetails: riskDetails
      .filter((item): item is { riskId: string; detail: unknown } => item.detail !== null)
      .map((item) => ({ riskId: item.riskId, detail: item.detail })),
  };
}

export async function getBehaviorSpaceVisualization(projectId: string): Promise<BehaviorSpaceVisualization> {
  const bundle = await getBehaviorSpaceDataBundle(projectId);
  return mapBehaviorSpaceVisualization(bundle);
}

import "server-only";

import { getCommandCenterSnapshot, getOnboarding, getTestRun, type ApiEnvelope } from "@/lib/api/command-center";
import { extractRunIdFromSnapshotEnvelope } from "./adapter";

export interface ExecutionPageInitialState {
  snapshotEnvelope: ApiEnvelope<unknown>;
  onboardingEnvelope: ApiEnvelope<unknown>;
  runEnvelope: ApiEnvelope<unknown> | null;
}

export async function getExecutionPageInitialState(projectId: string): Promise<ExecutionPageInitialState> {
  const [snapshotEnvelope, onboardingEnvelope] = await Promise.all([getCommandCenterSnapshot(projectId), getOnboarding(projectId)]);
  const runId = extractRunIdFromSnapshotEnvelope(snapshotEnvelope);

  if (!runId) {
    return {
      snapshotEnvelope,
      onboardingEnvelope,
      runEnvelope: null,
    };
  }

  try {
    const runEnvelope = await getTestRun(projectId, runId);
    return {
      snapshotEnvelope,
      onboardingEnvelope,
      runEnvelope,
    };
  } catch {
    return {
      snapshotEnvelope,
      onboardingEnvelope,
      runEnvelope: null,
    };
  }
}

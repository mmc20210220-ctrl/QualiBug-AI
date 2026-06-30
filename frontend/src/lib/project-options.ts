import "server-only";
import { ApiClientError, requestJson } from "@/lib/api/client";
import { demoProjects } from "@/lib/demo-projects";
import { maskId } from "@/lib/redact";

export interface ProjectOption {
  projectId: string;
  name: string;
}

export function buildSessionProjectOptions(input: {
  allowAll: boolean;
  projectIds: readonly string[];
}): ProjectOption[] {
  if (input.allowAll) {
    return demoProjects.map((project) => ({ projectId: project.projectId, name: project.name }));
  }

  return input.projectIds.map((projectId) => {
    const matched = demoProjects.find((project) => project.projectId === projectId);
    return matched
      ? { projectId: matched.projectId, name: matched.name }
      : { projectId, name: `项目 ${maskId(projectId)}` };
  });
}

function normalizeProjectOption(row: unknown): ProjectOption | null {
  if (!row || typeof row !== "object") return null;
  const record = row as Record<string, unknown>;
  const projectId =
    typeof record.project_id === "string" ? record.project_id : typeof record.projectId === "string" ? record.projectId : null;
  if (!projectId) return null;
  const name =
    typeof record.project_name === "string"
      ? record.project_name
      : typeof record.name === "string"
        ? record.name
        : `项目 ${maskId(projectId)}`;
  return { projectId, name };
}

export async function listProjectOptionsFromApi(): Promise<ProjectOption[]> {
  const envelope = await requestJson<{ success?: boolean; data?: unknown; error?: { message?: string } | null }>({
    method: "GET",
    path: "/api/v1/projects",
    timeoutMs: 3000,
    retry: { retries: 1, baseDelayMs: 200, maxDelayMs: 800 },
  });

  if (!Array.isArray(envelope?.data)) {
    throw new ApiClientError({
      kind: "parse",
      message: "项目列表返回了不可识别的结构。",
      url: "/api/v1/projects",
      payload: envelope,
    });
  }

  return envelope.data.map(normalizeProjectOption).filter((item): item is ProjectOption => Boolean(item));
}

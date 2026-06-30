import { ProjectCommercialValueStage } from "@/components/project/ProjectCommercialValueStage";
import { getEnvironmentDiagnosticGraph } from "@/features/environment-diagnostics";

export default async function ProjectValuePage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  const graph = await getEnvironmentDiagnosticGraph(projectId);

  return <ProjectCommercialValueStage projectId={projectId} graph={graph} />;
}

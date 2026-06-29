import { requireProjectAccess } from "@/lib/auth/server";

export default async function ProjectLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  await requireProjectAccess(projectId);

  return <div className="min-w-0">{children}</div>;
}

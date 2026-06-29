import { NextResponse } from "next/server";
import { startTestRun } from "@/lib/api/command-center";

export async function POST(request: Request, context: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await context.params;
  let body: unknown = null;
  try {
    body = await request.json();
  } catch {
    body = null;
  }

  const input =
    body && typeof body === "object" && !Array.isArray(body) ? (body as { run_id?: string; findings?: Record<string, unknown>[] }) : {};

  try {
    const envelope = await startTestRun(projectId, input);
    return NextResponse.json(envelope, { status: 201 });
  } catch {
    return NextResponse.json({ success: false, data: null, error: { message: "start_failed" } }, { status: 502 });
  }
}


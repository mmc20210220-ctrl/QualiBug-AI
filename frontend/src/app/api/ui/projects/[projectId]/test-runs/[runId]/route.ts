import { NextResponse } from "next/server";
import { getTestRun } from "@/lib/api/command-center";

export async function GET(_: Request, context: { params: Promise<{ projectId: string; runId: string }> }) {
  const { projectId, runId } = await context.params;
  try {
    const envelope = await getTestRun(projectId, runId);
    return NextResponse.json(envelope, { status: 200 });
  } catch {
    return NextResponse.json({ success: false, data: null, error: { message: "run_fetch_failed" } }, { status: 502 });
  }
}


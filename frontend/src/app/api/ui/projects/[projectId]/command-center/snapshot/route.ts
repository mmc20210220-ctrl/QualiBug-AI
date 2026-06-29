import { NextResponse } from "next/server";
import { getCommandCenterSnapshot } from "@/lib/api/command-center";

export async function GET(_: Request, context: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await context.params;
  try {
    const envelope = await getCommandCenterSnapshot(projectId);
    return NextResponse.json(envelope, { status: 200 });
  } catch {
    return NextResponse.json({ success: false, data: null, error: { message: "snapshot_failed" } }, { status: 502 });
  }
}


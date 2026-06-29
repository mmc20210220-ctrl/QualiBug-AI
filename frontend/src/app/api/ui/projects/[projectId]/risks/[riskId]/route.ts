import { NextResponse } from "next/server";
import { getRiskDetail } from "@/lib/api/command-center";

export async function GET(_: Request, context: { params: Promise<{ projectId: string; riskId: string }> }) {
  const { projectId, riskId } = await context.params;
  try {
    const envelope = await getRiskDetail(projectId, riskId);
    return NextResponse.json(envelope, { status: 200 });
  } catch {
    return NextResponse.json({ success: false, data: null, error: { message: "risk_fetch_failed" } }, { status: 502 });
  }
}

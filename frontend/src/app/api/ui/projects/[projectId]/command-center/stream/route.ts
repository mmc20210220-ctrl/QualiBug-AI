import { getCommandCenterSnapshot } from "@/lib/api/command-center";

export const dynamic = "force-dynamic";

function formatSseEvent(input: { event: string; data: string }): string {
  return `event: ${input.event}\ndata: ${input.data}\n\n`;
}

async function sleep(ms: number, signal: AbortSignal): Promise<void> {
  if (ms <= 0) return;
  await new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, ms);
    const onAbort = () => {
      clearTimeout(timer);
      resolve();
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export async function GET(request: Request, context: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await context.params;
  const encoder = new TextEncoder();

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      controller.enqueue(encoder.encode(`: connected ${new Date().toISOString()}\n\n`));
      controller.enqueue(encoder.encode(formatSseEvent({ event: "hello", data: JSON.stringify({ ok: true }) })));

      while (!request.signal.aborted) {
        try {
          const envelope = await getCommandCenterSnapshot(projectId);
          controller.enqueue(encoder.encode(formatSseEvent({ event: "snapshot", data: JSON.stringify(envelope) })));
        } catch {
          controller.enqueue(
            encoder.encode(formatSseEvent({ event: "error", data: JSON.stringify({ message: "snapshot_failed" }) })),
          );
        }
        await sleep(2000, request.signal);
      }
      controller.close();
    },
    cancel() {},
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}


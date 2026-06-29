import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const ROOT = path.resolve(process.cwd());
const MANIFEST_PATH = path.join(
  ROOT,
  ".next",
  "server",
  "app",
  "(app)",
  "projects",
  "[projectId]",
  "behavior-space",
  "page_client-reference-manifest.js",
);
const ENTRY_KEY = "/(app)/projects/[projectId]/behavior-space/page";
const MODULE_KEY = "[project]/src/app/(app)/projects/[projectId]/behavior-space/page";
const MAX_ENTRY_CHUNK_COUNT = 5;
const MAX_TOTAL_JS_BYTES = 2_800_000;
const MAX_LARGEST_CHUNK_BYTES = 2_650_000;

function loadManifest() {
  if (!fs.existsSync(MANIFEST_PATH)) {
    throw new Error(`Behavior Space perf baseline missing build artifact: ${path.relative(ROOT, MANIFEST_PATH)}`);
  }
  const source = fs.readFileSync(MANIFEST_PATH, "utf8");
  const sandbox = { globalThis: {} };
  vm.runInNewContext(source, sandbox);
  return sandbox.globalThis.__RSC_MANIFEST?.[ENTRY_KEY];
}

function normalizeChunkPath(chunkPath) {
  return chunkPath.startsWith("/_next/") ? chunkPath.slice("/_next/".length) : chunkPath;
}

function statChunk(chunkPath) {
  const relativePath = normalizeChunkPath(chunkPath);
  const absolutePath = path.join(ROOT, ".next", relativePath);
  if (!fs.existsSync(absolutePath)) {
    throw new Error(`Behavior Space perf baseline missing chunk: ${relativePath}`);
  }
  return {
    relativePath,
    bytes: fs.statSync(absolutePath).size,
  };
}

function main() {
  const manifest = loadManifest();
  if (!manifest) {
    throw new Error("Behavior Space perf baseline missing route manifest entry");
  }

  const entryJsFiles = manifest.entryJSFiles?.[MODULE_KEY];
  if (!Array.isArray(entryJsFiles) || entryJsFiles.length === 0) {
    throw new Error("Behavior Space perf baseline missing route entry JS files");
  }

  const clientModules = Object.keys(manifest.clientModules ?? {});
  if (!clientModules.some((key) => key.includes("BehaviorSpaceFlow.tsx"))) {
    throw new Error("Behavior Space perf baseline missing 2D entry module");
  }
  if (!clientModules.some((key) => key.includes("BehaviorSpaceSandbox.tsx"))) {
    throw new Error("Behavior Space perf baseline missing 2.5D entry module");
  }

  const chunkStats = entryJsFiles.map(statChunk);
  const totalJsBytes = chunkStats.reduce((sum, item) => sum + item.bytes, 0);
  const largestChunkBytes = chunkStats.reduce((max, item) => Math.max(max, item.bytes), 0);

  if (chunkStats.length > MAX_ENTRY_CHUNK_COUNT) {
    throw new Error(`Behavior Space perf baseline failed: chunk count ${chunkStats.length} > ${MAX_ENTRY_CHUNK_COUNT}`);
  }
  if (totalJsBytes > MAX_TOTAL_JS_BYTES) {
    throw new Error(`Behavior Space perf baseline failed: total JS ${totalJsBytes} > ${MAX_TOTAL_JS_BYTES}`);
  }
  if (largestChunkBytes > MAX_LARGEST_CHUNK_BYTES) {
    throw new Error(`Behavior Space perf baseline failed: largest chunk ${largestChunkBytes} > ${MAX_LARGEST_CHUNK_BYTES}`);
  }

  process.stdout.write(
    [
      "Behavior Space perf baseline OK",
      `- entry chunks: ${chunkStats.length}`,
      `- total js bytes: ${totalJsBytes}`,
      `- largest chunk bytes: ${largestChunkBytes}`,
    ].join("\n") + "\n",
  );
}

main();

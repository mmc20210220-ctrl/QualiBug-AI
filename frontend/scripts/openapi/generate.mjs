import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import openapiTS, { astToString } from "openapi-typescript";

const ROOT = path.resolve(process.cwd());
const OPENAPI_RELATIVE_POSIX = "openapi/phase104_api_contract/openapi.json";
const OPENAPI_PATH = path.join(ROOT, ...OPENAPI_RELATIVE_POSIX.split("/"));
const OUT_PATH = path.join(ROOT, "src", "lib", "api", "schema.ts");

function sha256(text) {
  return createHash("sha256").update(text).digest("hex");
}

async function main() {
  const openapiText = await fs.readFile(OPENAPI_PATH, "utf8");
  // Windows-native exports may include a UTF-8 BOM.  JSON.parse rejects the
  // BOM even though it is valid at the transport/file boundary, so normalize
  // only the parser input and keep the original bytes for the integrity hash.
  const openapiJson = JSON.parse(openapiText.replace(/^\uFEFF/, ""));
  const digest = sha256(openapiText);

  const pkg = JSON.parse(await fs.readFile(path.join(ROOT, "package.json"), "utf8"));
  const generatorVersion = pkg.devDependencies?.["openapi-typescript"] ?? "unknown";

  const ast = await openapiTS(openapiJson, { exportType: true });
  const generated = astToString(ast)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .trimStart();

  const payload = [
    `export const OPENAPI_SPEC_RELATIVE_PATH = ${JSON.stringify(OPENAPI_RELATIVE_POSIX)};`,
    `export const OPENAPI_SPEC_SHA256 = ${JSON.stringify(digest)};`,
    `export const OPENAPI_GENERATOR = ${JSON.stringify(`openapi-typescript@${generatorVersion}`)};`,
    "",
    generated,
    "",
  ].join("\n");

  await fs.mkdir(path.dirname(OUT_PATH), { recursive: true });
  await fs.writeFile(OUT_PATH, payload, "utf8");
  process.stdout.write(`Generated ${path.relative(ROOT, OUT_PATH)}\n`);
}

main().catch((err) => {
  process.stderr.write(`${err?.stack ?? err}\n`);
  process.exitCode = 1;
});

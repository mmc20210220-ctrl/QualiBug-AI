import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";

const ROOT = path.resolve(process.cwd());
const OPENAPI_RELATIVE_POSIX = "openapi/phase104_api_contract/openapi.json";
const OPENAPI_PATH = path.join(ROOT, ...OPENAPI_RELATIVE_POSIX.split("/"));
const SCHEMA_PATH = path.join(ROOT, "src", "lib", "api", "schema.ts");

function sha256(text) {
  return createHash("sha256").update(text).digest("hex");
}

async function main() {
  const openapiText = await fs.readFile(OPENAPI_PATH, "utf8");
  const expected = sha256(openapiText);

  const schemaText = await fs.readFile(SCHEMA_PATH, "utf8");
  const match = schemaText.match(/export const OPENAPI_SPEC_SHA256 = "([^"]+)";/);
  if (!match) throw new Error(`Missing OPENAPI_SPEC_SHA256 in ${path.relative(ROOT, SCHEMA_PATH)}`);
  const actual = match[1];

  if (actual !== expected) {
    throw new Error(
      [
        "OpenAPI types are out of date.",
        `Spec: ${OPENAPI_RELATIVE_POSIX}`,
        `Expected: ${expected}`,
        `Actual:   ${actual}`,
        "Run: npm run openapi:gen",
      ].join("\n"),
    );
  }

  const pkg = JSON.parse(await fs.readFile(path.join(ROOT, "package.json"), "utf8"));
  const generatorVersion = pkg.devDependencies?.["openapi-typescript"] ?? "unknown";
  const genMatch = schemaText.match(/export const OPENAPI_GENERATOR = "openapi-typescript@([^"]+)";/);
  if (!genMatch) throw new Error(`Missing OPENAPI_GENERATOR in ${path.relative(ROOT, SCHEMA_PATH)}`);
  if (genMatch[1] !== generatorVersion) {
    throw new Error(
      [
        "OpenAPI generator version mismatch.",
        `package.json: ${generatorVersion}`,
        `schema.ts:    ${genMatch[1]}`,
        "Run: npm install && npm run openapi:gen",
      ].join("\n"),
    );
  }

  process.stdout.write("OK\n");
}

main().catch((err) => {
  process.stderr.write(`${err?.stack ?? err}\n`);
  process.exitCode = 1;
});

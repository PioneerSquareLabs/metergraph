import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { generateText, jsonSchema, stepCountIs, tool, wrapLanguageModel } from "ai";
import * as metergraph from "metergraph";

const ingestUrl = process.env.MG_URL ?? "http://localhost:8787";
const token = process.env.MG_TOKEN ?? "ci-token";
const routeName = "latest-sdk.smoke";
const traceId = "a1".repeat(16);
const sessionId = "latest-sdk-session";
const installedSDKVersion = JSON.parse(await readFile(
  new URL("./node_modules/metergraph/package.json", import.meta.url),
  "utf8",
)).version;

let generation = 0;
const baseModel = {
  specificationVersion: "v3",
  provider: "anthropic.messages",
  modelId: "claude-sonnet-5",
  supportedUrls: {},
  async doGenerate() {
    generation += 1;
    if (generation === 1) {
      return {
        content: [{
          type: "tool-call",
          toolCallId: "lookup-1",
          toolName: "lookup_account",
          input: JSON.stringify({ accountId: "acct-1" }),
        }],
        finishReason: { unified: "tool-calls", raw: "tool_use" },
        usage: {
          inputTokens: { total: 12, noCache: 12, cacheRead: 0, cacheWrite: 0 },
          outputTokens: { total: 4, text: 0, reasoning: 0 },
        },
        warnings: [],
        response: {
          id: "latest-sdk-tool-response",
          timestamp: new Date(),
          modelId: "claude-sonnet-5",
        },
      };
    }
    return {
      content: [{ type: "text", text: "MeterGraph smoke test passed" }],
      finishReason: { unified: "stop", raw: "end_turn" },
      usage: {
        inputTokens: { total: 18, noCache: 18, cacheRead: 0, cacheWrite: 0 },
        outputTokens: { total: 10, text: 10, reasoning: 0 },
      },
      warnings: [],
      response: {
        id: "latest-sdk-smoke-response",
        timestamp: new Date(),
        modelId: "claude-sonnet-5",
      },
    };
  },
  async doStream() {
    throw new Error("streaming is qualified separately");
  },
};

metergraph.init({
  token,
  ingestUrl,
  appRoot: process.cwd(),
  environment: "latest-sdk-e2e",
  captureText: false,
  flushMs: 10,
});

try {
  const model = wrapLanguageModel({
    model: baseModel,
    middleware: metergraph.vercelAISDKMiddleware(),
  });
  const result = await metergraph.trace("latest-sdk.workflow", async () => {
    metergraph.setSession(sessionId);
    return metergraph.route(routeName, () => generateText({
      model,
      prompt: "Run the MeterGraph smoke test.",
      tools: {
        lookup_account: tool({
          description: "Look up an account",
          inputSchema: jsonSchema({
            type: "object",
            properties: { accountId: { type: "string" } },
            required: ["accountId"],
            additionalProperties: false,
          }),
          execute: async ({ accountId }) => ({ accountId, active: true }),
        }),
      },
      stopWhen: stepCountIs(2),
    }));
  }, { traceId });
  assert.equal(result.text, "MeterGraph smoke test passed");
  assert.equal(await metergraph.flush(10_000), true);
} finally {
  await metergraph.shutdown();
}

const response = await fetch(`${ingestUrl}/v1/calls?route=${routeName}`, {
  headers: { authorization: `Bearer ${token}` },
});
const responseText = await response.text();
assert.equal(response.status, 200, responseText);
const payload = JSON.parse(responseText);
const rows = payload.items.filter((item) => item.route === routeName);
assert.equal(rows.length, 2, `expected tool and terminal calls for ${routeName}`);
assert.deepEqual(
  new Set(rows.map((row) => row.request_id)),
  new Set(["latest-sdk-tool-response", "latest-sdk-smoke-response"]),
);
for (const row of rows) {
  assert.equal(row.provider, "anthropic");
  assert.equal(row.model, "claude-sonnet-5");
  assert.ok(row.template_hash);
  assert.deepEqual(row.tool_names, ["lookup_account"]);
  assert.equal(row.environment, "latest-sdk-e2e");
  assert.equal(row.sdk, "typescript");
  assert.equal(row.sdk_version, installedSDKVersion);
  assert.equal(row.trace_id, traceId);
  assert.equal(row.session_id, sessionId);
  assert.equal(row.status_code, "unset");
  assert.equal(row.error_type, null);
  assert.equal(row.cost_status, "priced");
  assert.ok(row.cost_usd > 0);
}
const byRequestId = Object.fromEntries(rows.map((row) => [row.request_id, row]));
assert.equal(byRequestId["latest-sdk-tool-response"].status, "tool-calls");
assert.equal(byRequestId["latest-sdk-tool-response"].finish_reason, "tool-calls");
assert.equal(byRequestId["latest-sdk-tool-response"].finish_reason_raw, "tool_use");
assert.equal(byRequestId["latest-sdk-smoke-response"].status, "stop");
assert.equal(byRequestId["latest-sdk-smoke-response"].finish_reason, "stop");
assert.equal(byRequestId["latest-sdk-smoke-response"].finish_reason_raw, "end_turn");

console.log("latest SDK / latest server E2E passed");

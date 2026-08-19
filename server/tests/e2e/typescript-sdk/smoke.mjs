import assert from "node:assert/strict";

import { generateText, wrapLanguageModel } from "ai";
import * as metergraph from "metergraph";

const ingestUrl = process.env.MG_URL ?? "http://localhost:8787";
const token = process.env.MG_TOKEN ?? "ci-token";
const routeName = "latest-sdk.smoke";

const baseModel = {
  specificationVersion: "v4",
  provider: "anthropic.messages",
  modelId: "claude-sonnet-5",
  supportedUrls: {},
  async doGenerate() {
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
  const result = await metergraph.route(routeName, () =>
    generateText({ model, prompt: "Run the MeterGraph smoke test." }),
  );
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
const row = payload.items.find((item) => item.route === routeName);
assert.ok(row, `missing ${routeName} row`);
assert.equal(row.provider, "anthropic");
assert.equal(row.model, "claude-sonnet-5");
assert.equal(row.input_tokens, 18);
assert.equal(row.output_tokens, 10);
assert.equal(row.environment, "latest-sdk-e2e");
assert.equal(row.sdk, "typescript");
assert.equal(row.status, "stop");
assert.equal(row.status_code, "unset");
assert.equal(row.finish_reason, "stop");
assert.equal(row.finish_reason_raw, "end_turn");
assert.equal(row.error_type, null);
assert.equal(row.cost_status, "priced");
assert.ok(row.cost_usd > 0);

console.log("latest SDK / latest server E2E passed");

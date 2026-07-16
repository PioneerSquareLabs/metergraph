import { contextSnapshot, type CaptureContext } from "./context.js";
import { scrub, templateHash } from "./template.js";
import type { Transport } from "./transport.js";
import { SDK_VERSION } from "./version.js";

export interface RuntimeOptions {
  captureText: boolean;
  redact?: (text: string, kind: "request" | "response") => string;
  appRoot: string;
  skipFrames: string[];
  environment?: string;
  textMaxBytes: number;
}

interface Frame {
  m: string;
  f: string;
  l: number;
}

interface CallState {
  provider: string;
  endpoint: string;
  request: Record<string, unknown>;
  context: CaptureContext;
  started: number;
  ts: string;
  frames: Frame[];
  done: boolean;
}

function get(value: unknown, key: string): unknown {
  return value && typeof value === "object" ? (value as Record<string, unknown>)[key] : undefined;
}

function first(value: unknown): unknown {
  return Array.isArray(value) ? value[0] : undefined;
}

function number(value: unknown): number | undefined {
  const parsed = Number(value);
  return value == null || !Number.isFinite(parsed) ? undefined : parsed;
}

function usage(response: unknown): Record<string, number | undefined> {
  const value = get(response, "usage") ?? get(response, "usage_metadata") ?? get(response, "usageMetadata");
  const prompt = get(value, "prompt_tokens_details") ?? get(value, "input_tokens_details");
  const completion = get(value, "completion_tokens_details") ?? get(value, "output_tokens_details");
  return {
    input_tokens: number(get(value, "prompt_tokens") ?? get(value, "input_tokens") ?? get(value, "promptTokenCount")),
    output_tokens: number(get(value, "completion_tokens") ?? get(value, "output_tokens") ?? get(value, "candidatesTokenCount")),
    cache_read_tokens: number(get(value, "cache_read_input_tokens") ?? get(prompt, "cached_tokens") ?? get(value, "cachedContentTokenCount")),
    cache_write_tokens: number(get(value, "cache_creation_input_tokens")),
    reasoning_tokens: number(get(completion, "reasoning_tokens") ?? get(value, "thoughtsTokenCount")),
  };
}

function responseText(response: unknown): string | undefined {
  const direct = get(response, "output_text") ?? get(response, "text");
  if (typeof direct === "string") return direct;
  const message = get(first(get(response, "choices")), "message");
  const content = get(message, "content");
  if (typeof content === "string") return content;
  const blocks = get(response, "content");
  if (Array.isArray(blocks)) {
    const joined = blocks.map((block) => get(block, "text")).filter((item): item is string => typeof item === "string").join("");
    if (joined) return joined;
  }
  const outputs = get(response, "output");
  if (Array.isArray(outputs)) {
    const joined = outputs.flatMap((output) => {
      const content = get(output, "content");
      if (!Array.isArray(content)) return [];
      return content.map((block) => get(block, "text") ?? get(block, "output_text"));
    }).filter((item): item is string => typeof item === "string").join("");
    if (joined) return joined;
  }
  return undefined;
}

export function chunkText(chunk: unknown): string | undefined {
  const delta = get(first(get(chunk, "choices")), "delta") ?? get(chunk, "delta");
  const text = get(delta, "content") ?? get(delta, "text") ?? get(chunk, "text");
  return typeof text === "string" ? text : undefined;
}

function stopReason(response: unknown): string | undefined {
  const value = get(response, "stop_reason") ?? get(response, "status") ?? get(first(get(response, "choices")), "finish_reason");
  return value == null ? undefined : String(value);
}

function toolNames(request: Record<string, unknown>): { name: string }[] | undefined {
  if (!Array.isArray(request.tools)) return undefined;
  const names = request.tools.flatMap((tool) => {
    const name = get(get(tool, "function"), "name") ?? get(tool, "name");
    return name ? [{ name: String(name) }] : [];
  });
  return names.length ? names : undefined;
}

function frames(stack: string | undefined, appRoot: string, skip: string[]): Frame[] {
  if (!stack) return [];
  const result: Frame[] = [];
  for (const line of stack.split("\n").slice(1)) {
    const match = line.match(/^\s*at\s+(?:(.*?)\s+\()?(.+?):(\d+):\d+\)?$/);
    if (!match) continue;
    const [, fn = "<anonymous>", file, lineNo] = match;
    if (!file || !lineNo || !file.includes(appRoot)) continue;
    if (["node_modules", "node:internal", "/sdk/typescript/dist/", ...skip].some((value) => file.includes(value))) continue;
    result.push({
      m: file.slice(file.indexOf(appRoot) + appRoot.length).replace(/^\//, ""),
      f: fn,
      l: Number(lineNo),
    });
    if (result.length === 5) break;
  }
  return result;
}

export class CaptureRuntime {
  constructor(
    readonly transport: Transport,
    readonly options: RuntimeOptions,
  ) {}

  start(
    provider: string,
    endpoint: string,
    request: Record<string, unknown>,
    stack?: string,
    context: CaptureContext = contextSnapshot(),
  ): CallState {
    return {
      provider,
      endpoint,
      request,
      context,
      started: performance.now(),
      ts: new Date().toISOString(),
      frames: frames(stack, this.options.appRoot, this.options.skipFrames),
      done: false,
    };
  }

  finish(
    state: CallState,
    response?: unknown,
    extra: { error?: unknown; status?: string; stream?: boolean; ttftMs?: number; responseText?: string } = {},
  ): void {
    if (state.done) return;
    state.done = true;
    const captureText = state.context.captureText ?? this.options.captureText;
    const text = (value: string | undefined, kind: "request" | "response") => {
      if (!captureText || value == null) return { value: undefined, truncated: false };
      try {
        value = this.options.redact ? this.options.redact(value, kind) : value;
      } catch {
        return { value: "<redaction-failed>", truncated: false };
      }
      const bytes = new TextEncoder().encode(value);
      if (bytes.byteLength <= this.options.textMaxBytes) return { value, truncated: false };
      const clipped = new TextDecoder().decode(bytes.slice(0, this.options.textMaxBytes - 24));
      return { value: `${clipped}\n<metergraph:truncated>`, truncated: true };
    };
    const request = text(JSON.stringify(scrub(state.request)), "request");
    const output = text(extra.responseText ?? responseText(response), "response");
    const firstFrame = state.frames[0];
    this.transport.enqueue({
      ts: state.ts,
      route: state.context.route,
      provider: state.provider,
      model: state.request.model,
      ...usage(response),
      latency_ms: Math.round(performance.now() - state.started),
      status: extra.status ?? (extra.error ? "error" : stopReason(response) ?? "success"),
      session_id: state.context.sessionId,
      template_hash: templateHash(state.request),
      unit_name: state.context.unitName,
      unit_count: state.context.unitCount,
      tool_calls: toolNames(state.request),
      endpoint: state.endpoint,
      request_id: get(response, "_request_id") ?? get(response, "response_id") ?? get(response, "id"),
      batch: state.request.batch === true,
      batch_custom_id: state.request.batch_custom_id,
      // Explicit positive consent stamp; the worker never infers opt-in from
      // content fields that happen to be present.
      content_opted_in: captureText,
      request_json: request.value,
      response_text: output.value,
      text_truncated: request.truncated || output.truncated,
      stream: extra.stream ?? false,
      ttft_ms: extra.ttftMs,
      func: state.context.funcName
        ?? (firstFrame ? `${firstFrame.m}:${firstFrame.f}:${firstFrame.l}` : undefined),
      module: firstFrame?.m,
      frames_json: state.frames,
      tags: state.context.tags,
      environment: this.options.environment,
      error: Boolean(extra.error),
      error_type: extra.error instanceof Error ? extra.error.name : undefined,
      sdk: "js",
      sdk_version: SDK_VERSION,
      runtime: typeof process === "undefined" ? "edge" : `node-${process.versions.node}`,
    });
  }
}

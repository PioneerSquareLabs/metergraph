import { type CaptureRuntime, chunkText } from "./capture.js";
import { contextSnapshot, type CaptureContext } from "./context.js";

type AnyRecord = Record<PropertyKey, any>;

let runtime: CaptureRuntime | undefined;
const seenBatchItems = new Set<string>();

export function setCaptureRuntime(value?: CaptureRuntime): void {
  runtime = value;
}

function requestFrom(args: unknown[]): Record<string, unknown> {
  const first = args[0];
  return first && typeof first === "object" ? { ...(first as Record<string, unknown>) } : {};
}

function get(value: unknown, key: string): any {
  return value && typeof value === "object" ? (value as AnyRecord)[key] : undefined;
}

function markBatchItem(key: string): boolean {
  if (seenBatchItems.has(key)) return false;
  if (seenBatchItems.size >= 100_000) seenBatchItems.clear();
  seenBatchItems.add(key);
  return true;
}

function captureOpenAIBatchItem(
  capture: CaptureRuntime,
  item: AnyRecord,
  sourceId: string,
  context: CaptureContext,
  stack?: string,
): void {
  const response = get(item, "response");
  const error = get(item, "error");
  if (response == null && error == null) return;
  const customId = String(get(item, "custom_id") ?? "");
  const itemId = String(get(item, "id") ?? customId);
  const responseId = String(get(response, "request_id") ?? itemId);
  if (!markBatchItem(`openai:${sourceId}:${itemId}:${responseId}`)) return;
  const body = get(response, "body") ?? {};
  const normalized = body && typeof body === "object"
    ? { ...body, _request_id: responseId }
    : body;
  const request = {
    model: get(body, "model"),
    batch: true,
    service_tier: "batch",
    batch_custom_id: customId || undefined,
    batch_item_id: itemId || undefined,
  };
  const endpoint = get(body, "object") === "response"
    ? "batch.responses"
    : "batch.chat.completions";
  const state = capture.start("openai", endpoint, request, stack, context);
  const statusCode = Number(get(response, "status_code"));
  capture.finish(state, normalized, {
    status: error != null || (Number.isFinite(statusCode) && statusCode >= 400)
      ? "error"
      : undefined,
  });
}

function captureOpenAIBatchContent(
  capture: CaptureRuntime,
  content: string,
  sourceId: string,
  context: CaptureContext,
  stack?: string,
): void {
  try {
    for (const line of content.split(/\r?\n/)) {
      if (!line.trim()) continue;
      try {
        const item = JSON.parse(line);
        if (item && typeof item === "object" && "custom_id" in item
          && ("response" in item || "error" in item)) {
          captureOpenAIBatchItem(capture, item, sourceId, context, stack);
        }
      } catch { /* a non-batch JSONL line is ignored */ }
    }
  } catch { /* capture cannot break file consumption */ }
}

function wrapOpenAIFileResponse(
  response: AnyRecord,
  capture: CaptureRuntime,
  sourceId: string,
  context: CaptureContext,
  stack?: string,
): AnyRecord {
  if (!response || typeof response !== "object") return response;
  return new Proxy(response, {
    get(target, property) {
      if (property === "text" && typeof target.text === "function") {
        return async () => {
          const text = await target.text();
          captureOpenAIBatchContent(capture, text, sourceId, context, stack);
          return text;
        };
      }
      if (property === "arrayBuffer" && typeof target.arrayBuffer === "function") {
        return async () => {
          const value = await target.arrayBuffer();
          captureOpenAIBatchContent(
            capture, new TextDecoder().decode(value), sourceId, context, stack,
          );
          return value;
        };
      }
      if (property === "blob" && typeof target.blob === "function") {
        return async () => {
          const value = await target.blob();
          captureOpenAIBatchContent(capture, await value.text(), sourceId, context, stack);
          return value;
        };
      }
      const value = Reflect.get(target, property, target);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

function captureAnthropicBatchItem(
  capture: CaptureRuntime,
  item: AnyRecord,
  batchId: string,
  context: CaptureContext,
  stack?: string,
): void {
  const result = get(item, "result");
  const resultType = String(get(result, "type") ?? "");
  const customId = String(get(item, "custom_id") ?? "");
  if (!resultType || !markBatchItem(`anthropic:${batchId}:${customId}:${resultType}`)) return;
  const message = get(result, "message") ?? {};
  const state = capture.start("anthropic", "batch.messages", {
    model: get(message, "model"),
    batch: true,
    service_tier: "batch",
    batch_custom_id: customId || undefined,
    batch_id: batchId || undefined,
  }, stack, context);
  capture.finish(state, message, {
    status: resultType === "succeeded" ? undefined : "error",
  });
}

function wrapAnthropicBatchResults(
  result: AnyRecord,
  capture: CaptureRuntime,
  batchId: string,
  context: CaptureContext,
  stack?: string,
): AnyRecord {
  if (!result || typeof result[Symbol.asyncIterator] !== "function") return result;
  return new Proxy(result, {
    get(target, property, receiver) {
      if (property === Symbol.asyncIterator) {
        return async function* () {
          for await (const item of target as AsyncIterable<AnyRecord>) {
            try { captureAnthropicBatchItem(capture, item, batchId, context, stack); }
            catch { /* capture cannot break result iteration */ }
            yield item;
          }
        };
      }
      const value = Reflect.get(target, property, receiver);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

function patchOpenAIBatchContent(owner: AnyRecord | undefined, method: string): boolean {
  if (!owner || typeof owner[method] !== "function") return false;
  if (owner[method].__metergraph_batch__) return true;
  const original = owner[method];
  const wrapped = function (this: unknown, ...args: unknown[]) {
    const capture = runtime;
    if (!capture) return original.apply(owner, args);
    const sourceId = String(args[0] ?? "unknown");
    const context = contextSnapshot();
    const stack = new Error().stack;
    const result = original.apply(owner, args);
    if (result && typeof result.then === "function") {
      return result.then((response: AnyRecord) => (
        wrapOpenAIFileResponse(response, capture, sourceId, context, stack)
      ));
    }
    return wrapOpenAIFileResponse(result, capture, sourceId, context, stack);
  };
  wrapped.__metergraph_batch__ = true;
  owner[method] = wrapped;
  return true;
}

function patchAnthropicBatchResults(owner: AnyRecord | undefined): boolean {
  if (!owner || typeof owner.results !== "function") return false;
  if (owner.results.__metergraph_batch__) return true;
  const original = owner.results;
  const wrapped = function (this: unknown, ...args: unknown[]) {
    const capture = runtime;
    if (!capture) return original.apply(owner, args);
    const batchId = String(args[0] ?? "unknown");
    const context = contextSnapshot();
    const stack = new Error().stack;
    const result = original.apply(owner, args);
    if (result && typeof result.then === "function") {
      return result.then((resolved: AnyRecord) => (
        wrapAnthropicBatchResults(resolved, capture, batchId, context, stack)
      ));
    }
    return wrapAnthropicBatchResults(result, capture, batchId, context, stack);
  };
  wrapped.__metergraph_batch__ = true;
  owner.results = wrapped;
  return true;
}

function streamProxy(stream: AnyRecord, state: ReturnType<CaptureRuntime["start"]>, capture: CaptureRuntime): AnyRecord {
  let last: unknown;
  let ttftMs: number | undefined;
  const parts: string[] = [];
  const observe = (chunk: unknown) => {
    last = chunk;
    const text = chunkText(chunk);
    if (text) {
      ttftMs ??= Math.round(performance.now() - state.started);
      parts.push(text);
    }
    return chunk;
  };
  const finish = (response: unknown = last, error?: unknown, status?: string) => {
    capture.finish(state, response, {
      error,
      status,
      stream: true,
      ttftMs,
      responseText: parts.join("") || undefined,
    });
  };

  if (typeof stream.on === "function") {
    stream.on("error", (error: unknown) => finish(last, error));
  }

  return new Proxy(stream, {
    get(target, property, receiver) {
      if (property === Symbol.asyncIterator && typeof target[Symbol.asyncIterator] === "function") {
        return async function* () {
          try {
            for await (const chunk of target as AsyncIterable<unknown>) {
              const value = observe(chunk);
              const usageOnly = state.provider === "openai"
                && state.endpoint === "chat.completions"
                && Array.isArray((chunk as AnyRecord)?.choices)
                && (chunk as AnyRecord).choices.length === 0
                && (chunk as AnyRecord).usage != null;
              if (!usageOnly) yield value;
            }
            let final = last;
            if (typeof target.finalMessage === "function") {
              try { final = await target.finalMessage(); } catch { /* captured by stream error */ }
            }
            finish(final);
          } catch (error) {
            finish(last, error);
            throw error;
          }
        };
      }
      if (property === "finalMessage" && typeof target.finalMessage === "function") {
        return async (...args: unknown[]) => {
          try {
            const final = await target.finalMessage(...args);
            finish(final);
            return final;
          } catch (error) {
            finish(last, error);
            throw error;
          }
        };
      }
      if (property === "close" || property === "abort") {
        const original = Reflect.get(target, property, receiver);
        if (typeof original !== "function") return original;
        return (...args: unknown[]) => {
          try { return original.apply(target, args); }
          finally { finish(last, undefined, "abandoned"); }
        };
      }
      const value = Reflect.get(target, property, receiver);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

function patch(owner: AnyRecord | undefined, method: string, provider: string, endpoint: string): boolean {
  if (!owner || typeof owner[method] !== "function") return false;
  if (owner[method].__metergraph__) return true;
  const original = owner[method];
  const wrapped = function (this: unknown, ...args: unknown[]) {
    const capture = runtime;
    if (!capture) return original.apply(owner, args);
    const incoming = requestFrom(args);
    const patchUsage = typeof process === "undefined"
      || process.env.METERGRAPH_PATCH_STREAM_USAGE !== "0";
    if (
      provider === "openai"
      && endpoint === "chat.completions"
      && incoming.stream === true
      && incoming.stream_options == null
      && patchUsage
    ) {
      args = [{ ...incoming, stream_options: { include_usage: true } }, ...args.slice(1)];
    }
    const state = capture.start(provider, endpoint, requestFrom(args), new Error().stack);
    let result: unknown;
    try {
      result = original.apply(owner, args);
    } catch (error) {
      capture.finish(state, undefined, { error });
      throw error;
    }
    const complete = (response: any) => {
      const request = requestFrom(args);
      const streaming = endpoint.endsWith(".stream") || request.stream === true;
      if (streaming && response && (response[Symbol.asyncIterator] || response.finalMessage)) {
        return streamProxy(response, state, capture);
      }
      capture.finish(state, response);
      return response;
    };
    if (result && typeof (result as Promise<unknown>).then === "function") {
      return (result as Promise<unknown>).then(complete, (error) => {
        capture.finish(state, undefined, { error });
        throw error;
      });
    }
    return complete(result);
  };
  wrapped.__metergraph__ = true;
  owner[method] = wrapped;
  return true;
}

export function wrap<T extends AnyRecord>(client: T, provider?: "openai" | "anthropic" | "google"): T {
  const name = provider ?? (client.models?.generateContent
    ? "google"
    : client.chat || client.responses ? "openai" : "anthropic");
  let patched = 0;
  if (name === "google") {
    patched += Number(patch(client.models, "generateContent", name, "models.generate_content"));
    patched += Number(patch(client.models, "generateContentStream", name, "models.generate_content.stream"));
  }
  patched += Number(patch(client.chat?.completions, "create", name, "chat.completions"));
  patched += Number(patch(client.responses, "create", name, "responses"));
  patched += Number(patch(client.responses, "stream", name, "responses.stream"));
  patched += Number(patch(client.messages, "create", name, "messages"));
  patched += Number(patch(client.messages, "stream", name, "messages.stream"));
  if (name === "openai") {
    patched += Number(patchOpenAIBatchContent(client.files, "content"));
    patched += Number(patchOpenAIBatchContent(client.files, "retrieveContent"));
  } else if (name === "anthropic") {
    patched += Number(patchAnthropicBatchResults(client.messages?.batches));
    patched += Number(patchAnthropicBatchResults(client.beta?.messages?.batches));
  }
  if (!patched) console.warn(`Metergraph found no supported methods on ${name} client`);
  return client;
}

export const wrapClient = wrap;

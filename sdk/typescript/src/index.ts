import { CaptureRuntime } from "./capture.js";
import { ConfigPoller } from "./config.js";
import {
  contextSnapshot,
  route,
  setSession,
  setTags,
  type RouteOptions,
} from "./context.js";
import { track } from "./track.js";
import { Transport, type TransportMode, type WaitUntil } from "./transport.js";
import { setCaptureRuntime, wrap, wrapClient } from "./wrap.js";

export interface MetergraphOptions {
  token?: string;
  ingestUrl?: string;
  captureText?: boolean;
  redact?: (text: string, kind: "request" | "response") => string;
  appRoot?: string;
  skipFrames?: string[];
  environment?: string;
  disabled?: boolean;
  transport?: TransportMode;
  queueSize?: number;
  batchSize?: number;
  flushMs?: number;
  configPollMs?: number;
  configHardTtlMs?: number;
}

export interface OutcomeOptions {
  model: string;
  taskCompleted: boolean;
  sessionKey?: string;
  feedbackScore?: number;
  turnsToResolution?: number;
  escalated?: boolean;
  abandoned?: boolean;
  editDistanceRatio?: number;
  regenerationCount?: number;
  eventId?: string;
}

let initialized = false;
let transport: Transport | undefined;
let config: ConfigPoller | undefined;
export const DEFAULT_INGEST_URL = "https://d2xus7mp8zdv6t.cloudfront.net";

function env(name: string): string | undefined {
  return typeof process === "undefined" ? undefined : process.env[name];
}

function envBool(name: string, fallback: boolean): boolean {
  const value = env(name);
  return value === undefined
    ? fallback
    : !["0", "false", "no", "off"].includes(value.toLowerCase());
}

export function init(options: MetergraphOptions = {}): void {
  if (initialized) return;
  initialized = true;
  if (env("METERGRAPH_DISABLED") === "1" || options.disabled) return;
  const token = options.token ?? env("METERGRAPH_APP_TOKEN");
  const ingestUrl = options.ingestUrl ?? env("METERGRAPH_INGEST_URL") ?? DEFAULT_INGEST_URL;
  if (!token || !ingestUrl) {
    console.warn("Metergraph capture disabled: token and ingest URL are required");
    return;
  }
  try {
    transport = new Transport(token, ingestUrl, {
      queueSize: options.queueSize ?? Number(env("METERGRAPH_QUEUE_SIZE") ?? 2_000),
      batchSize: options.batchSize ?? Number(env("METERGRAPH_BATCH_SIZE") ?? 100),
      flushMs: options.flushMs ?? Number(env("METERGRAPH_FLUSH_MS") ?? 5_000),
      mode: options.transport ?? "auto",
    });
    setCaptureRuntime(new CaptureRuntime(transport, {
      captureText: options.captureText ?? envBool("METERGRAPH_CAPTURE_TEXT", false),
      redact: options.redact,
      appRoot: options.appRoot ?? (typeof process === "undefined" ? "" : process.cwd()),
      skipFrames: options.skipFrames ?? [],
      environment: options.environment ?? env("METERGRAPH_ENV"),
      textMaxBytes: Number(env("METERGRAPH_TEXT_MAX_BYTES") ?? 100_000),
    }));
    config = new ConfigPoller(
      token,
      ingestUrl,
      options.configPollMs,
      options.configHardTtlMs,
    );
  } catch {
    transport = undefined;
    config = undefined;
    setCaptureRuntime();
    console.warn("Metergraph initialization failed; application is running uninstrumented");
  }
}

export function modelFor(routeName: string, fallback: string, sessionKey?: string): string {
  return config?.modelFor(routeName, fallback, sessionKey ?? contextSnapshot().sessionId) ?? fallback;
}

export function recordOutcome(routeName: string, options: OutcomeOptions): boolean {
  const routeNameClean = String(routeName).trim().slice(0, 512);
  const model = String(options.model).trim().slice(0, 512);
  const sessionKey = String(options.sessionKey ?? contextSnapshot().sessionId ?? "").trim().slice(0, 512);
  const randomId = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  const eventId = String(options.eventId ?? randomId).trim().slice(0, 128);
  const integerIn = (value: number | undefined, minimum: number): boolean => value === undefined
    || (Number.isInteger(value) && value >= minimum && value <= 1_000_000);
  if (!transport || !routeNameClean || !model || !sessionKey || !eventId
    || typeof options.taskCompleted !== "boolean"
    || (options.feedbackScore !== undefined
      && (!Number.isFinite(options.feedbackScore) || options.feedbackScore < -1 || options.feedbackScore > 1))
    || !integerIn(options.turnsToResolution, 1)
    || !integerIn(options.regenerationCount, 0)
    || (options.editDistanceRatio !== undefined
      && (!Number.isFinite(options.editDistanceRatio)
        || options.editDistanceRatio < 0 || options.editDistanceRatio > 1))
    || (options.escalated !== undefined && typeof options.escalated !== "boolean")
    || (options.abandoned !== undefined && typeof options.abandoned !== "boolean")) {
    return false;
  }
  return transport.enqueue({
    event_type: "outcome",
    event_id: eventId,
    ts: new Date().toISOString(),
    route: routeNameClean,
    session_id: sessionKey,
    model,
    task_completed: options.taskCompleted,
    feedback_score: options.feedbackScore,
    turns_to_resolution: options.turnsToResolution,
    escalated: options.escalated,
    abandoned: options.abandoned,
    edit_distance_ratio: options.editDistanceRatio,
    regeneration_count: options.regenerationCount,
  });
}

export function bindWaitUntil(value: { waitUntil(promise: Promise<unknown>): void } | WaitUntil): void {
  transport?.bindWaitUntil(value);
}

export async function flush(timeoutMs = 3_000): Promise<boolean> {
  return transport?.flush(timeoutMs) ?? true;
}

export async function shutdown(): Promise<void> {
  config?.stop();
  config = undefined;
  await transport?.shutdown();
  transport = undefined;
  setCaptureRuntime();
}

export function wrapHandler<TArgs extends unknown[], TResult>(
  handler: (...args: TArgs) => TResult | Promise<TResult>,
): (...args: TArgs) => Promise<TResult> {
  return async (...args: TArgs) => {
    try {
      return await handler(...args);
    } finally {
      await flush();
    }
  };
}

export { route, setSession, setTags, track, wrap, wrapClient };
export type { RouteOptions, TransportMode };

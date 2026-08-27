/**
 * Pure translation layer: pi agent events -> daa.trajectory.v1 events.
 * No pi imports, no I/O beyond node:crypto hashing. Everything defensive:
 * unknown payloads never throw; unmappable events return null.
 *
 * Contract daa.trajectory.v1 (structure only — digests and counts, never raw
 * content):
 *   turn_start       -> data {user_input_digest}
 *   model_input      -> data {summary: {n_messages, n_tools}}
 *   tool_call        -> data {tool, input_digest}
 *   tool_result      -> data {tool, ok, output_digest, chars}
 *   context_injection-> data {source, chars}
 *   turn_end         -> data {outcome: complete|error|interrupted}
 * digest(text) = sha256 hex first 12 chars + ":" + UTF-8 byte length.
 */

import { createHash } from "node:crypto";

export const TRAJECTORY_CONTRACT = "daa.trajectory.v1";

export type TrajectoryEventName =
  | "turn_start"
  | "model_input"
  | "tool_call"
  | "tool_result"
  | "context_injection"
  | "turn_end";

export type TurnOutcome = "complete" | "error" | "interrupted";

export type HarnessId = "v1" | "pi" | "dsh";

/** Object-literal type (not interface) so dicts stay assignable to Record<string, unknown>. */
export type TrajectoryEventDict = {
  event: TrajectoryEventName;
  /** ISO-8601 UTC timestamp with Z suffix. */
  ts: string;
  session_id: string;
  /** Turn number, integer >= 0. */
  turn: number;
  harness: HarnessId;
  data: Record<string, unknown>;
};

export interface TrajectoryContext {
  sessionId: string;
  turn: number;
  harness: HarnessId;
}

const DIGEST_RE = /^[0-9a-f]{12}:\d+$/;

/** digest(text) = "3b5d2f07a1c9:4096" — sha256 hex prefix + UTF-8 byte length. */
export function digest(text: string): string {
  const bytes = Buffer.byteLength(text, "utf8");
  const hex = createHash("sha256").update(text, "utf8").digest("hex").slice(0, 12);
  return `${hex}:${bytes}`;
}

/** True when a value satisfies the contract's `*_digest` shape. */
export function isDigest(value: string): boolean {
  return DIGEST_RE.test(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value) ?? "";
  } catch {
    return "";
  }
}

/** Join the textual parts of a pi content array [(TextContent|ImageContent), ...]. */
export function contentText(content: unknown): string {
  if (!Array.isArray(content)) return "";
  const parts: string[] = [];
  for (const part of content) {
    if (isRecord(part) && part.type === "text" && typeof part.text === "string") {
      parts.push(part.text);
    }
  }
  return parts.join("\n");
}

/** Defensively pull a text payload out of arbitrary event shapes. */
function extractText(payload: Record<string, unknown>): string | null {
  for (const key of ["userInput", "text", "input", "message", "content"]) {
    const value = payload[key];
    if (typeof value === "string") return value;
    if (Array.isArray(value)) {
      const joined = contentText(value);
      if (joined) return joined;
    } else if (isRecord(value)) {
      const inner = extractText(value);
      if (inner) return inner;
    }
  }
  return null;
}

function extractToolName(payload: Record<string, unknown>): string | null {
  return asString(payload.toolName) ?? asString(payload.tool) ?? asString(payload.name);
}

/** tool_execution_end carries result.content; tool_result carries content directly. */
function extractOutputText(payload: Record<string, unknown>): string {
  if (isRecord(payload.result)) return contentText(payload.result.content);
  return contentText(payload.content);
}

function modelInputSummary(payload: Record<string, unknown>): { n_messages: number; n_tools: number } | null {
  const messages = payload.messages;
  if (!Array.isArray(messages)) return null;
  let nTools = 0;
  if (Array.isArray(payload.tools)) {
    nTools = payload.tools.length;
  } else {
    for (const message of messages) {
      if (isRecord(message) && Array.isArray(message.toolUse)) nTools += message.toolUse.length;
    }
  }
  return { n_messages: messages.length, n_tools: nTools };
}

function extractOutcome(payload: Record<string, unknown>): TurnOutcome {
  const message = isRecord(payload.message) ? payload.message : null;
  const stopReason = asString(message?.stopReason) ?? asString(payload.stopReason) ?? "";
  const aborted =
    payload.aborted === true || payload.cancelled === true || payload.interrupted === true;
  if (aborted || stopReason.includes("abort") || stopReason.includes("cancel")) return "interrupted";
  if (payload.error !== undefined || stopReason.includes("error")) return "error";
  const results = payload.toolResults;
  if (Array.isArray(results) && results.length > 0) {
    const allFailed = results.every(
      (result) => isRecord(result) && (result.isError === true || asString(result.type) === "error"),
    );
    if (allFailed) return "error";
  }
  return "complete";
}

/** Pull a non-negative integer turnIndex out of a pi turn event (null when absent). */
export function extractTurnIndex(payload: unknown): number | null {
  if (!isRecord(payload)) return null;
  const value = payload.turnIndex;
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/**
 * Map one pi event (kind = pi event name, payload = event object) onto a
 * daa.trajectory.v1 event dict. Returns null when the payload lacks the fields
 * the contract needs (the caller then drops the event silently).
 */
export function piEventToTrajectoryEvent(
  kind: string,
  payload: unknown,
  ctx: TrajectoryContext,
): TrajectoryEventDict | null {
  const p = isRecord(payload) ? payload : {};
  const turn = Number.isInteger(ctx.turn) && ctx.turn >= 0 ? ctx.turn : 0;
  const base = {
    ts: new Date().toISOString(),
    session_id: ctx.sessionId,
    turn,
    harness: ctx.harness,
  };
  switch (kind) {
    case "turn_start":
    case "input":
      return { ...base, event: "turn_start", data: { user_input_digest: digest(extractText(p) ?? "") } };
    case "agent_start":
    case "model_input": {
      const summary = modelInputSummary(p);
      return summary ? { ...base, event: "model_input", data: { summary } } : null;
    }
    case "tool_call":
    case "tool_execution_start": {
      const tool = extractToolName(p);
      if (!tool) return null;
      const input = p.input ?? p.args ?? p.arguments ?? "";
      const inputText = typeof input === "string" ? input : safeJson(input);
      return { ...base, event: "tool_call", data: { tool, input_digest: digest(inputText) } };
    }
    case "tool_result":
    case "tool_execution_end": {
      const tool = extractToolName(p);
      if (!tool) return null;
      const output = extractOutputText(p);
      const ok = p.isError === undefined ? true : p.isError !== true;
      return {
        ...base,
        event: "tool_result",
        data: { tool, ok, output_digest: digest(output), chars: Buffer.byteLength(output, "utf8") },
      };
    }
    case "context_injection": {
      const source = asString(p.source) ?? "unknown";
      const content = extractText(p) ?? "";
      return {
        ...base,
        event: "context_injection",
        data: { source, chars: Buffer.byteLength(content, "utf8") },
      };
    }
    case "turn_end":
      return { ...base, event: "turn_end", data: { outcome: extractOutcome(p) } };
    default:
      return null;
  }
}

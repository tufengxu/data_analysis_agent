/**
 * Pure translation from dsh `session/event` durable records to the DAA
 * `daa.trajectory.v1` contract. No dsh imports — the record arrives as
 * `unknown` and every field is extracted defensively, so a dsh upgrade that
 * changes payload shapes degrades to `null` (event skipped) instead of
 * throwing inside the host harness.
 *
 * 铁律「记结构不记数值」: data 只携带 digest(`sha256[:12]:len`)与计数,
 * 绝不落原始文本 —— 与 src/data_analysis_agent/capabilities/evolution/
 * trajectory.py 的校验规则逐条对齐(任意 `*_digest` 键必须匹配
 * /^[0-9a-f]{12}:\d+$/,超长字符串值按疑似原文拒绝)。
 *
 * Record shape verified 2026-08-26 @0.1.1-rc.2 (@deepseek-ai/dsh-session):
 *   `{type, seq, time(unix ms), data}` where
 *   turn/start      -> {turn}
 *   turn/end        -> {turn, reason: {kind: 'completed'|'aborted'|'blocked'|
 *                        'error'|'max-tokens'|'interrupted', ...}}
 *   tool/call       -> {turn, step, callId, name, arguments(raw JSON string)}
 *   tool/result     -> {turn, step, message: ToolResultMessage
 *                        {content: [ToolResultBlock], source: {callId}},
 *                        error?: {name, code}}
 *   user/message    -> UserMessage {content: ContentBlock[], source: {kind}}
 *   request/header  -> {header: {tools?: [...]}, reason}
 */

import { createHash } from "node:crypto";

/** Content-digest contract: sha256 hex[:12] + ":" + char length. */
export function digest(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex").slice(0, 12) + ":" + text.length;
}

/** JSON-friendly view of one contract event (matches TrajectoryEvent.to_dict). */
export interface TrajectoryEventDict {
  event: string;
  ts: string;
  session_id: string;
  turn: number;
  harness: "dsh";
  data: Record<string, unknown>;
}

export interface TranslateContext {
  sessionId: string;
  turn: number;
  /**
   * Optional callId -> tool-name correlation maintained by the caller from
   * earlier `tool/call` records (dsh `tool/result` carries only the callId,
   * not the tool name — verified 2026-08-26 @0.1.1-rc.2).
   */
  toolNameByCallId?: ReadonlyMap<string, string>;
}

const OUTCOME_BY_KIND: Record<string, "complete" | "error" | "interrupted"> = {
  completed: "complete",
  aborted: "interrupted",
  interrupted: "interrupted",
  blocked: "interrupted",
  error: "error",
  "max-tokens": "error",
};

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

function asNonNegInt(v: unknown): number | undefined {
  return typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : undefined;
}

function asString(v: unknown): string | undefined {
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

/** Concatenate all `text` blocks found in a dsh content array (defensive). */
function flattenText(content: unknown): string {
  if (!Array.isArray(content)) return "";
  let out = "";
  for (const block of content) {
    if (isObj(block)) {
      if (block.type === "text" && typeof block.text === "string") out += block.text;
      else if (Array.isArray(block.content)) out += flattenText(block.content);
    }
  }
  return out;
}

/** ISO-8601 UTC with Z suffix; unix-ms input, epoch fallback on garbage. */
function isoTs(time: unknown): string {
  const ms = typeof time === "number" && Number.isFinite(time) ? time : 0;
  return new Date(ms).toISOString();
}

/**
 * Map one dsh session/event record to a contract event, or `null` when the
 * record type has no counterpart (or the payload is too malformed to trust).
 * Never includes raw content — only digests, names, and counts.
 */
export function dshRecordToTrajectoryEvent(
  record: unknown,
  ctx: TranslateContext,
): TrajectoryEventDict | null {
  if (!isObj(record)) return null;
  const type = asString(record.type);
  const data = isObj(record.data) ? record.data : {};
  const ts = isoTs(record.time);
  const turn = asNonNegInt(data.turn) ?? ctx.turn;
  const base = { ts, session_id: ctx.sessionId, turn, harness: "dsh" as const };

  switch (type) {
    case "turn/start": {
      // dsh turn/start carries only {turn}; digest the structural payload as
      // the user-input stand-in until the turn's user/message is observed
      // (the plugin overrides this digest when the real user text arrives).
      const payload = JSON.stringify(data);
      return { ...base, event: "turn_start", data: { user_input_digest: digest(payload) } };
    }
    case "turn/end": {
      const kind = isObj(data.reason) ? asString(data.reason.kind) : undefined;
      const outcome = (kind && OUTCOME_BY_KIND[kind]) || "interrupted";
      return { ...base, event: "turn_end", data: { outcome } };
    }
    case "tool/call": {
      const tool = asString(data.name);
      const args = typeof data.arguments === "string" ? data.arguments : JSON.stringify(data.arguments ?? {});
      if (!tool) return null;
      return { ...base, event: "tool_call", data: { tool, input_digest: digest(args) } };
    }
    case "tool/result": {
      const message = isObj(data.message) ? data.message : {};
      const callId =
        asString((isObj(message.source) ? message.source.callId : undefined)) ??
        asString(data.callId);
      const tool =
        asString(data.name) ??
        (callId !== undefined ? ctx.toolNameByCallId?.get(callId) : undefined) ??
        "unknown";
      const text = flattenText(message.content);
      const ok = data.error === undefined && message.isError !== true;
      return {
        ...base,
        event: "tool_result",
        data: { tool, ok, output_digest: digest(text), chars: text.length },
      };
    }
    case "user/message": {
      // dsh user/message data IS the UserMessage itself ({content, source}).
      // Plugin-sourced context (skills, injected instructions, notices) maps
      // to context_injection; a direct human prompt only feeds the pending
      // turn_start digest (handled by the plugin) and yields no event here.
      const source = isObj(data.source) ? data.source : {};
      if (source.kind !== "plugin") return null;
      const text = flattenText(data.content);
      if (text.length === 0) return null;
      const origin = asString(source.plugin) ?? "plugin";
      return { ...base, event: "context_injection", data: { source: origin, chars: text.length } };
    }
    case "request/header": {
      // Model-request snapshot; dsh 0.1.1-rc.2 carries tool schemas but no
      // message count, so n_messages is reported as 0 ("unknown").
      const header = isObj(data.header) ? data.header : {};
      const tools = Array.isArray(header.tools) ? header.tools.length : 0;
      return {
        ...base,
        event: "model_input",
        data: { summary: { n_messages: 0, n_tools: tools } },
      };
    }
    default:
      return null;
  }
}

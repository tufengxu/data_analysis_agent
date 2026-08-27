/**
 * DataAnalysisAgent capability adapter for the DeepSeek Harness (dsh).
 *
 * Cordis plugin (seams verified 2026-08-26 @0.1.1-rc.2): `tools/post-execute`
 * is `(exec, result, next)` returning a `PostToolDecision` (NOT the result) —
 * oversized plain-text tool results (e.g. `mcp__daa__*` tools served by the
 * official `@deepseek-ai/dsh-mcp-client` plugin) are compacted via the shared
 * capability server, swapping only the text content. `session/event` records
 * are translated (structure only, never raw content) to `daa.trajectory.v1`
 * and flushed to `evolution_record_event` on `turn/end`. Both arms are side
 * channels: any failure degrades silently and never breaks a tool call.
 */

import { randomUUID } from "node:crypto";
import type { Context } from "@deepseek-ai/cordis";
// Type-only imports pull the Events interface augmentation for the two seams.
import type {} from "@deepseek-ai/dsh-tools";
import type {} from "@deepseek-ai/dsh-session";
import type { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { callCapability, connectCapabilityServer } from "../../shared/capability-client.ts";
import { compactToolResult, shouldCompact } from "./compaction.ts";
import { digest, dshRecordToTrajectoryEvent, type TrajectoryEventDict } from "./translate.ts";

/** Cordis plugin name used by loader diagnostics. */
export const name = "daa-capabilities";
/** Require the tool registry (its post-execute waterfall is the seam). */
export const inject = ["tools"];

interface SessionTrajectoryState {
  queue: TrajectoryEventDict[];
  /** Index of a queued turn_start still awaiting the real user-input digest. */
  pendingTurnStart: number | null;
  /** callId -> tool name correlation from earlier tool/call records. */
  toolNames: Map<string, string>;
  turn: number;
}

function triggerFromEnv(): number {
  const parsed = Number.parseInt(process.env.DAA_COMPACT_TRIGGER ?? "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 8000;
}

export function apply(ctx: Context): void {
  const triggerChars = triggerFromEnv();
  let clientPromise: Promise<Client> | null = null;
  const states = new Map<string, SessionTrajectoryState>();

  async function sharedClient(): Promise<Client | null> {
    if (clientPromise === null) {
      clientPromise = connectCapabilityServer().catch((err: unknown) => {
        clientPromise = null; // allow a later retry
        throw err;
      });
    }
    try {
      return await clientPromise;
    } catch {
      return null; // capability server unavailable — degrade silently
    }
  }

  // (b) Tool-result compaction waterfall. Verified 2026-08-26 @0.1.1-rc.2:
  // listeners receive (exec, result, next); next() yields the downstream
  // PostToolDecision; replacement = {kind:'accept', content:[...]}.
  ctx.on("tools/post-execute", async (exec, result, next) => {
    const decision = await next();
    if (process.env.DAA_DSH_DEBUG) {
      const blocks = decision.kind === "accept" ? decision.content : undefined;
      console.error(`[daa-debug] post-execute ${exec.name} len=${flattenPlainText(blocks ?? result.content)?.length}`);
    }
    try {
      if (decision.kind !== "accept" || Object.hasOwn(decision, "value") || exec.parent !== undefined) {
        return decision; // value replacements / blocks / sub-dispatches pass through
      }
      const text = flattenPlainText(decision.content ?? result.content);
      if (text === undefined) return decision;
      // mcp__daa__* tools return the DAA JSON envelope; compact the INNER
      // content so the sampler sees the real table/text, not escaped JSON.
      const inner = unwrapEnvelopeContent(text) ?? text;
      if (!shouldCompact(inner, triggerChars)) return decision;
      const client = await sharedClient();
      if (client === null) return decision;
      const replacement = await compactToolResult(client, {
        text: inner,
        toolName: exec.name,
        resultId: `dsh-${randomUUID().slice(0, 8)}`,
        pressure: 0,
      });
      if (replacement === null) return decision; // no gain or failure — keep original
      if (process.env.DAA_DSH_DEBUG) {
        console.error(`[daa-debug] compacted ${exec.name}: ${inner.length} -> ${replacement.length} chars`);
      }
      return {
        kind: "accept",
        content: [{ type: "text", text: replacement }],
        ...(decision.additionalContexts ? { additionalContexts: decision.additionalContexts } : {}),
      };
    } catch {
      return decision; // compaction must never break the tool call
    }
  });

  // (c) Trajectory translation: queue per session, flush on turn/end.
  ctx.on("session/event", (session, event) => {
    if (process.env.DAA_DSH_DEBUG) console.error(`[daa-debug] session/event ${String(session.id)} ${event.type}`);
    try {
      const sessionId = String(session.id);
      let st = states.get(sessionId);
      if (st === undefined) {
        st = { queue: [], pendingTurnStart: null, toolNames: new Map(), turn: 0 };
        states.set(sessionId, st);
      }
      const data = event.data as unknown as Record<string, unknown> | undefined;

      // Correlate callId -> tool name for later tool/result records.
      if (event.type === "tool/call" && data !== undefined &&
          typeof data.callId === "string" && typeof data.name === "string") {
        st.toolNames.set(data.callId, data.name);
      }
      if (event.type === "turn/start" && typeof data?.turn === "number") {
        st.turn = data.turn;
      }

      const translated = dshRecordToTrajectoryEvent(event, {
        sessionId,
        turn: st.turn,
        toolNameByCallId: st.toolNames,
      });
      if (translated !== null) {
        if (event.type === "turn/start") st.pendingTurnStart = st.queue.length;
        st.queue.push(translated);
      }

      // The turn's user/message arrives after turn/start: backfill the real
      // user-input digest on the queued turn_start (structure-only until now).
      if (event.type === "user/message" && st.pendingTurnStart !== null && data !== undefined) {
        const userText = flattenPlainText(data.content);
        if (userText !== undefined && userText.length > 0) {
          const pending = st.queue[st.pendingTurnStart];
          if (pending !== undefined && pending.event === "turn_start") {
            pending.data.user_input_digest = digest(userText);
          }
          st.pendingTurnStart = null;
        }
      }

      if (event.type === "turn/end") flush(sessionId);
    } catch {
      // telemetry side channel — never surface to the host harness
    }
  });

  // Serialized recorder: batches drain in order; the disposer awaits the
  // chain so a headless run's process exit cannot cut recording short.
  let recording: Promise<void> = Promise.resolve();
  function flush(sessionId: string): void {
    const st = states.get(sessionId);
    if (st === undefined || st.queue.length === 0) return;
    const batch = st.queue.splice(0, st.queue.length);
    st.pendingTurnStart = null;
    recording = recording.then(() => recordBatch(batch)).catch(() => {
      // dropped batch — trajectory recording is best-effort only
    });
  }

  async function recordBatch(batch: TrajectoryEventDict[]): Promise<void> {
    const client = await sharedClient();
    if (client === null) return;
    for (const ev of batch) {
      const env = await callCapability(client, "evolution_record_event", ev as unknown as Record<string, unknown>);
      if (!env.ok && process.env.DAA_DSH_DEBUG) {
        console.error(`[daa-debug] trajectory record rejected: ${JSON.stringify(env.error)}`);
      }
    }
  }

  ctx.effect(() => async () => {
    for (const sessionId of states.keys()) flush(sessionId);
    await recording;
    const promise = clientPromise;
    clientPromise = null;
    if (promise !== null) await promise.then((c) => c.close()).catch(() => {});
  }, "daa-capabilities");
}

/** All-text content flattened to one string, or `undefined` on any non-text block. */
function flattenPlainText(content: unknown): string | undefined {
  if (!Array.isArray(content)) return undefined;
  let text = "";
  for (const block of content) {
    if (typeof block !== "object" || block === null || (block as { type?: unknown }).type !== "text") {
      return undefined;
    }
    text += (block as { text?: unknown }).text ?? "";
  }
  return text;
}

/** If `text` is a DAA capability envelope ({ok, capability, content, ...}), return its inner content string. */
function unwrapEnvelopeContent(text: string): string | undefined {
  if (!text.startsWith("{")) return undefined;
  try {
    const parsed = JSON.parse(text) as { ok?: unknown; capability?: unknown; content?: unknown };
    if (typeof parsed.content === "string" && parsed.content.length > 0 &&
        (parsed.ok === true || typeof parsed.capability === "string")) {
      return parsed.content;
    }
    return undefined;
  } catch {
    return undefined;
  }
}

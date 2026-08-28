/**
 * Compaction seam for the dsh adapter: a thin executor over the shared
 * capability server's `sampling_compact_result` capability.
 *
 * D0 single-source-of-truth: this side does NOT mirror the server's
 * (pressure-adaptive) trigger. `shouldAsk` is only a cheap lower-bound
 * pre-filter — anything above the floor is sent to the server, whose own
 * trigger decides; a `was_compacted=false` answer keeps the original
 * byte-for-byte. The executor is fail-open-by-original: ANY error or a
 * "no gain" answer returns `null`, meaning "keep the original result
 * untouched" — compaction is a side channel and must never make a tool
 * result worse.
 *
 * Envelope contract (verified against
 * src/data_analysis_agent/capabilities/serving/registry.py):
 *   in  {content, max_chars=50000, context_pressure 0..1,
 *        fidelity_level?, result_id?, tool_name?}
 *   out {ok, content?, data?: {was_compacted, result_id?, sampling_method,
 *        fidelity_level, original_chars, compacted_chars}, error?}
 */

/**
 * Minimal structural client interface — deliberately decoupled from the MCP
 * SDK class so this module typechecks against any SDK copy (the shared
 * wrapper resolves its own node_modules).
 */
export interface ToolCallingClient {
  callTool(args: { name: string; arguments: Record<string, unknown> }): Promise<unknown>;
}

export interface CompactAsk {
  text: string;
  toolName?: string;
  resultId?: string;
  /** Context pressure 0..1 (adaptive gain gating); 0 = strictest. */
  pressure?: number;
}

/**
 * True when the text exceeds the ask floor — the cheap pre-filter below
 * which we skip the server roundtrip entirely. Keep at/below the server's
 * trigger_floor_chars so no compaction is ever missed.
 */
export function shouldAsk(text: string, floor = 2000): boolean {
  return text.length > floor;
}

/**
 * Compact one oversized tool result through the capability server.
 *
 * Returns the replacement text (sampling summary + recall hint), or `null` to
 * keep the original. When `resultId` is supplied the server persists the full
 * original in its ResultStore and the replacement carries a recall handle;
 * the English hint here is appended only if the server did not already add
 * its own (byte-identical Chinese) hint.
 */
export async function compactToolResult(
  client: ToolCallingClient,
  ask: CompactAsk,
): Promise<string | null> {
  let envelope: unknown;
  try {
    envelope = await client.callTool({
      name: "sampling_compact_result",
      arguments: {
        content: ask.text,
        max_chars: 50_000,
        context_pressure: ask.pressure ?? 0,
        ...(ask.resultId !== undefined ? { result_id: ask.resultId } : {}),
        ...(ask.toolName !== undefined ? { tool_name: ask.toolName } : {}),
      },
    });
  } catch {
    return null; // transport-level failure → keep original
  }
  const env = decodeEnvelope(envelope);
  if (!env.ok || env.content === undefined) return null;
  if (env.data?.was_compacted !== true) return null; // no gain → keep original

  const resultId =
    (typeof env.data.result_id === "string" && env.data.result_id) || ask.resultId || null;
  let replacement = env.content;
  if (resultId !== null && !replacement.includes("retrieve_result(")) {
    replacement +=
      `\n\n[full result cached; retrieve via retrieve_result(result_id="${resultId}", offset=0, limit=50)]`;
  }
  return replacement;
}

/** Extract the DAA JSON envelope text from an MCP callTool result. */
function decodeEnvelope(result: unknown): {
  ok: boolean;
  content?: string;
  data?: Record<string, unknown>;
} {
  try {
    if (typeof result !== "object" || result === null || !("content" in result)) {
      return { ok: false };
    }
    const blocks = (result as { content?: unknown }).content;
    if (!Array.isArray(blocks)) return { ok: false };
    const text = blocks.find(
      (b): b is { type: string; text?: string } =>
        typeof b === "object" && b !== null && (b as { type?: unknown }).type === "text",
    )?.text;
    if (typeof text !== "string" || text.length === 0) return { ok: false };
    const parsed = JSON.parse(text) as {
      ok?: unknown;
      content?: unknown;
      data?: unknown;
    };
    return {
      ok: parsed.ok === true,
      content: typeof parsed.content === "string" ? parsed.content : undefined,
      data:
        typeof parsed.data === "object" && parsed.data !== null
          ? (parsed.data as Record<string, unknown>)
          : undefined,
    };
  } catch {
    return { ok: false };
  }
}

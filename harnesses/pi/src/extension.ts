/**
 * Pi Agent Core extension for DataAnalysisAgent (adapter = glue only):
 *
 *  1. daa_* tool proxies  — the 19 capability tools of `data-agent-capabilities mcp`,
 *     names + JSON-Schema parameters fetched once via client.listTools() and
 *     registered with pi.registerTool(); execute() proxies to callCapability()
 *     through ONE lazily (re)connected MCP client and returns error envelopes
 *     on any failure (fail-closed, never throws).
 *  2. compaction seam     — pi.on("tool_result") rewrites oversized textual
 *     results with a sampling_compact_result summary + retrieve handle
 *     (best-effort: on any error the original result is kept).
 *  3. trajectory seam     — pi turn/tool events translated (src/translate.ts)
 *     to daa.trajectory.v1 and recorded fire-and-forget via
 *     evolution_record_event (never blocks the agent loop).
 */

import type { ExtensionAPI, ToolDefinition } from "@earendil-works/pi-coding-agent";
import {
  callCapability,
  connectCapabilityServer,
  type CapabilityEnvelope,
} from "../../shared/capability-client.ts";
import { CAPABILITY_TOOL_NAMES, registeredToolName, seamConfigFromEnv } from "./preset.ts";
import {
  contentText,
  extractTurnIndex,
  piEventToTrajectoryEvent,
  type TrajectoryEventDict,
} from "./translate.ts";

/** ONE capability client for the whole extension (type derived from the shared client). */
type CapabilityClient = Awaited<ReturnType<typeof connectCapabilityServer>>;

interface ListedTool {
  name?: unknown;
  description?: unknown;
  inputSchema?: unknown;
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Filesystem-safe recall handle derived from a pi toolCallId. */
function sanitizeId(id: string): string {
  const cleaned = id.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 64);
  return cleaned;
}

export default async function daaPiExtension(pi: ExtensionAPI): Promise<void> {
  const seam = seamConfigFromEnv();
  let clientPromise: Promise<CapabilityClient> | null = null;
  let sessionId = `pi-${Date.now()}`;
  let turn = 0;
  let lastUserInput = "";

  function getClient(): Promise<CapabilityClient> {
    if (!clientPromise) {
      // Lazily connect; a failed connect clears the slot so the next call retries.
      const pending = connectCapabilityServer().catch((err: unknown) => {
        clientPromise = null;
        throw err;
      });
      clientPromise = pending;
      return pending;
    }
    return clientPromise;
  }

  /** All capability traffic funnels through here; NEVER throws. */
  async function runCapability(name: string, args: unknown): Promise<CapabilityEnvelope> {
    try {
      const client = await getClient();
      return await callCapability(client, name, (args ?? {}) as Record<string, unknown>);
    } catch (err) {
      return {
        ok: false,
        error: { code: "adapter_error", message: err instanceof Error ? err.message : String(err) },
      };
    }
  }

  function makeProxy(
    capability: string,
    meta: ListedTool | undefined,
  ): ToolDefinition {
    const description =
      typeof meta?.description === "string" && meta.description
        ? meta.description
        : `DataAnalysisAgent capability ${capability} (schema unavailable — capability server was not reachable at load time)`;
    const schema = isJsonObject(meta?.inputSchema) ? meta.inputSchema : { type: "object", properties: {} };
    return {
      name: registeredToolName(capability),
      label: capability,
      description: `[daa capability] ${description}`,
      // The MCP inputSchema is a plain JSON Schema object; TypeBox schemas ARE
      // JSON Schema objects, so the cast is representation-safe.
      // verified 2026-08-26 @0.84.3
      parameters: schema as unknown as ToolDefinition["parameters"],
      async execute(_toolCallId: string, params: unknown) {
        const envelope = await runCapability(capability, params);
        const text = envelope.ok
          ? envelope.content ?? JSON.stringify(envelope)
          : JSON.stringify(envelope);
        return { content: [{ type: "text", text }], details: envelope };
      },
    } as unknown as ToolDefinition;
  }

  /** Fetch names+schemas once; register all 19 proxies even when listing fails. */
  async function registerProxies(): Promise<void> {
    let listed: ListedTool[] = [];
    try {
      const client = await getClient();
      const res = await client.listTools();
      listed = (res.tools ?? []) as ListedTool[];
    } catch {
      listed = []; // fail-closed: proxies still register; execute() reports error envelopes
    }
    const byName = new Map<string, ListedTool>();
    for (const tool of listed) {
      if (typeof tool.name === "string") byName.set(tool.name, tool);
    }
    for (const capability of CAPABILITY_TOOL_NAMES) {
      pi.registerTool(makeProxy(capability, byName.get(capability)));
    }
  }

  // ---- seam 2: oversized tool_result compaction (best-effort, fail-open) ----
  pi.on("tool_result", async (event) => {
    try {
      const text = contentText(event.content);
      if (text.length <= seam.compactionTriggerChars) return undefined;
      const handle = sanitizeId(event.toolCallId) || `pi-${Date.now()}`;
      const envelope = await runCapability("sampling_compact_result", {
        content: text,
        max_chars: seam.maxChars,
        context_pressure: seam.contextPressure,
        result_id: handle,
        tool_name: event.toolName,
      });
      const data = envelope.data ?? {};
      if (!envelope.ok || data.was_compacted !== true) return undefined;
      const resultId = typeof data.result_id === "string" && data.result_id ? data.result_id : handle;
      return {
        content: [
          {
            type: "text",
            text: `${envelope.content ?? ""}\n\n[full result cached; retrieve via retrieve_result(result_id=${resultId}, offset=0, limit=50)]`,
          },
        ],
      };
    } catch {
      return undefined; // compaction is best-effort: keep the original result
    }
  });

  // ---- seam 3: daa.trajectory.v1 recording (fire-and-forget) ----
  function trajectoryCtx(ctx: { sessionManager?: unknown }): {
    sessionId: string;
    turn: number;
    harness: "pi";
  } {
    const manager = ctx.sessionManager as { getSessionId?: () => unknown } | undefined;
    try {
      const id = manager?.getSessionId?.();
      if (typeof id === "string" && id) sessionId = id;
    } catch {
      /* keep the fallback session id */
    }
    return { sessionId, turn, harness: "pi" };
  }

  function record(dict: TrajectoryEventDict | null): void {
    if (!dict) return;
    void runCapability("evolution_record_event", dict); // runCapability catches all
  }

  // pi 0.84.3 turn_start carries no user text: stash it from the input event.
  // verified 2026-08-26 @0.84.3
  pi.on("input", (event) => {
    if (typeof event.text === "string") lastUserInput = event.text;
  });

  pi.on("turn_start", (event, ctx) => {
    turn = extractTurnIndex(event) ?? turn + 1;
    record(
      piEventToTrajectoryEvent(
        "turn_start",
        { ...event, userInput: lastUserInput },
        trajectoryCtx(ctx),
      ),
    );
  });

  pi.on("turn_end", (event, ctx) => {
    record(piEventToTrajectoryEvent("turn_end", event, trajectoryCtx(ctx)));
  });

  pi.on("tool_execution_end", (event, ctx) => {
    record(piEventToTrajectoryEvent("tool_execution_end", event, trajectoryCtx(ctx)));
  });

  await registerProxies();
}

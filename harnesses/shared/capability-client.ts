/**
 * Shared capability-server connection for both harness adapters, built on the
 * OFFICIAL MCP TypeScript SDK client. The spawned server process is the fixed
 * literal program `data-agent-capabilities` (resolve via PATH; put the repo's
 * .venv/bin on PATH to run from source). Process spawning happens inside the
 * SDK transport — adapters contain zero process-construction code.
 *
 * Both bases (Pi / dsh) call the SAME capability server through this one
 * wrapper — the "one capability implementation, one transport" invariant.
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

export interface CapabilityEnvelope {
  ok: boolean;
  capability?: string;
  content?: string;
  data?: Record<string, unknown>;
  artifacts?: string[];
  metadata?: Record<string, unknown>;
  error?: { code: string; message: string };
}

const SERVER_COMMAND = "data-agent-capabilities";
const SERVER_ARGS = ["mcp"];

/** Connect a fresh MCP stdio client to the capability server. */
export async function connectCapabilityServer(): Promise<Client> {
  const transport = new StdioClientTransport({
    command: SERVER_COMMAND,
    args: SERVER_ARGS,
  });
  const client = new Client(
    { name: "daa-harness-adapter", version: "0.1.0" },
    { capabilities: {} },
  );
  await client.connect(transport);
  return client;
}

/** Call one capability and decode the DAA JSON envelope from the text content. */
export async function callCapability(
  client: Client,
  name: string,
  args: Record<string, unknown>,
): Promise<CapabilityEnvelope> {
  try {
    const result = await client.callTool({ name, arguments: args });
    const content = (result.content as Array<{ type: string; text?: string }> | undefined) ?? [];
    const text = content.find((c) => c.type === "text")?.text ?? "";
    return JSON.parse(text) as CapabilityEnvelope;
  } catch (err) {
    return {
      ok: false,
      error: { code: "transport_error", message: err instanceof Error ? err.message : String(err) },
    };
  }
}

/** Convenience: one-shot connect → call → close (for smoke scripts). */
export async function withCapabilityServer<T>(
  fn: (client: Client) => Promise<T>,
): Promise<T> {
  const client = await connectCapabilityServer();
  try {
    return await fn(client);
  } finally {
    await client.close();
  }
}

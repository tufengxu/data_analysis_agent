/**
 * No-LLM smoke test for the Pi adapter (needs only the local capability server
 * subprocess — no API key, no network). Covers:
 *   - pure unit tests of src/translate.ts and src/preset.ts
 *   - capability server round-trips: listTools (19 names), tabular_read_file,
 *     causal_analyze, sampling_compact_result (table-summary + result_id),
 *     retrieve_result (paged recall), evolution_record_event (valid accepted,
 *     raw-content rejected with validation_error)
 * Prints one "[PASS]/[FAIL] <name>" line per check and a final
 * "PI SMOKE: PASS|FAIL"; exit code 0 only on full pass.
 */

import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { callCapability, withCapabilityServer } from "../../shared/capability-client.ts";
import {
  CAPABILITY_TOOL_NAMES,
  buildPresetDoc,
  seamConfigFromEnv,
} from "../src/preset.ts";
import { digest, isDigest, piEventToTrajectoryEvent } from "../src/translate.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..", ".."); // harnesses/pi/smoke -> repo root
process.env.PATH = `${REPO}/.venv/bin:${process.env.PATH ?? ""}`;

// Fresh tmp workspace. NOTE: the MCP stdio transport forwards only a whitelist
// of env vars (PATH, HOME, ...) to the server, so the DAA_CAPABILITIES_* values
// above the whitelist are reinforced by HOME=<tmp> (redirects the server's
// ~/.daa result-store / trajectory defaults) and cwd=<tmp> (redirects the
// allowed-roots / artifacts cwd defaults) — the run stays fully isolated.
const TMP = mkdtempSync(join(tmpdir(), "daa-pi-smoke-"));
process.env.DAA_CAPABILITIES_HOME = join(TMP, "results");
process.env.DAA_CAPABILITIES_ARTIFACTS = join(TMP, "artifacts");
process.env.DAA_CAPABILITIES_ALLOWED_ROOTS = TMP;
process.env.DAA_CAPABILITIES_EVOLUTION_ROOT = join(TMP, "trajectories");
process.env.HOME = TMP;
process.chdir(TMP);

let failed = 0;

function check(name: string, ok: boolean, detail?: unknown): void {
  if (ok) {
    console.log(`[PASS] ${name}`);
  } else {
    failed += 1;
    console.log(`[FAIL] ${name}${detail === undefined ? "" : ` — ${String(detail)}`}`);
  }
}

const CTX = { sessionId: "smoke-pi-session", turn: 0, harness: "pi" as const };

function unitTranslate(): void {
  check("digest: shape + byte length", /^[0-9a-f]{12}:\d+$/.test(digest("hello")) && digest("hello").endsWith(":5"));
  check("digest: UTF-8 bytes (CJK 3 bytes)", digest("你").endsWith(":3"));
  check("digest: empty text", digest("").endsWith(":0") && isDigest(digest("")));

  const ts = piEventToTrajectoryEvent(
    "turn_start",
    { type: "turn_start", turnIndex: 0, timestamp: 1756000000000, userInput: "分析一下销量" },
    CTX,
  );
  check(
    "translate: turn_start -> {user_input_digest}",
    ts !== null && ts.event === "turn_start" && ts.harness === "pi" && ts.ts.endsWith("Z") && isDigest(String(ts.data.user_input_digest)),
  );

  const tr = piEventToTrajectoryEvent(
    "tool_execution_end",
    { type: "tool_execution_end", toolCallId: "t1", toolName: "daa_tabular_read_file", result: { content: [{ type: "text", text: "a,b\n1,2" }] }, isError: false },
    CTX,
  );
  check(
    "translate: tool_execution_end -> tool_result",
    tr !== null && tr.event === "tool_result" && tr.data.tool === "daa_tabular_read_file" && tr.data.ok === true && isDigest(String(tr.data.output_digest)) && tr.data.chars === 7,
  );

  const trErr = piEventToTrajectoryEvent(
    "tool_execution_end",
    { type: "tool_execution_end", toolCallId: "t2", toolName: "daa_tabular_read_file", result: { content: [] }, isError: true },
    CTX,
  );
  check("translate: tool_result ok=false on isError", trErr !== null && trErr.data.ok === false);

  const teOk = piEventToTrajectoryEvent("turn_end", { type: "turn_end", turnIndex: 0, message: { role: "assistant", stopReason: "stop" }, toolResults: [] }, CTX);
  check("translate: turn_end complete", teOk !== null && teOk.event === "turn_end" && teOk.data.outcome === "complete");
  const teErr = piEventToTrajectoryEvent("turn_end", { type: "turn_end", turnIndex: 0, message: { role: "assistant", stopReason: "error" }, toolResults: [] }, CTX);
  check("translate: turn_end error", teErr !== null && teErr.data.outcome === "error");
  const tc = piEventToTrajectoryEvent("tool_call", { type: "tool_call", toolCallId: "t3", toolName: "bash", input: { command: "ls" } }, CTX);
  check("translate: tool_call -> {tool, input_digest}", tc !== null && tc.event === "tool_call" && tc.data.tool === "bash" && isDigest(String(tc.data.input_digest)));
  check("translate: unknown kind -> null", piEventToTrajectoryEvent("mystery", {}, CTX) === null);
  check("translate: no tool name -> null", piEventToTrajectoryEvent("tool_execution_end", { type: "tool_execution_end" }, CTX) === null);
}

function unitPreset(): void {
  const preset = buildPresetDoc({ DAA_COMPACT_TRIGGER: "1234", DAA_PI_PRESSURE: "0.9" } as NodeJS.ProcessEnv);
  check(
    "preset: manifest json-able, 19 tools, prompt mentions daa_*",
    JSON.stringify(preset).length > 0 && preset.tools.length === CAPABILITY_TOOL_NAMES.length && preset.system_prompt.includes("daa_"),
  );
  check("preset: seam env parsing", preset.seam.compaction.trigger_chars === 1234 && preset.seam.compaction.context_pressure === 0.9);
  const defaults = seamConfigFromEnv({} as NodeJS.ProcessEnv);
  check(
    "preset: seam defaults (8000 / 0.5 / 50000)",
    defaults.compactionTriggerChars === 8000 && defaults.contextPressure === 0.5 && defaults.maxChars === 50000,
  );
}

async function capabilityRoundTrips(): Promise<void> {
  const smallCsv = join(TMP, "sales_small.csv");
  writeFileSync(smallCsv, "discount,sales,region\n0.2,120,east\n0.35,90,south\n0,140,east\n");

  const bigLines = ["region,discount,sales,revenue"];
  for (let i = 0; i < 2000; i += 1) {
    const discount = (i % 50) / 100;
    const sales = (i * 3) % 997;
    bigLines.push(`r${i % 7},${discount.toFixed(2)},${sales},${(sales * (1 - discount)).toFixed(2)}`);
  }
  const bigTable = bigLines.join("\n");

  await withCapabilityServer(async (client) => {
    const listed = await client.listTools();
    const names = new Set((listed.tools ?? []).map((tool) => tool.name));
    check("listTools: >= 19 tools", (listed.tools ?? []).length >= 19, `got ${names.size}`);
    const missing = CAPABILITY_TOOL_NAMES.filter((name) => !names.has(name));
    check("listTools: exact expected names", missing.length === 0, `missing: ${missing.join(", ")}`);

    const read = await callCapability(client, "tabular_read_file", { file_path: smallCsv });
    check("tabular_read_file: ok + header", read.ok === true && (read.content ?? "").includes("discount,sales,region"));

    const causal = await callCapability(client, "causal_analyze", {
      question: "打折是否导致销量上升?",
      context: { columns: ["discount", "sales", "region"], n_rows: 1200 },
    });
    check("causal_analyze: ok true", causal.ok === true, JSON.stringify(causal.error ?? null));

    const compact = await callCapability(client, "sampling_compact_result", {
      content: bigTable,
      max_chars: 50000,
      context_pressure: 0.9,
      result_id: "smoke-pi-1",
      tool_name: "tabular_read_file",
    });
    const data = compact.data ?? {};
    check("sampling_compact_result: was_compacted", compact.ok === true && data.was_compacted === true);
    check("sampling_compact_result: sampling_method table-summary", data.sampling_method === "table-summary", `method=${String(data.sampling_method)}`);
    check("sampling_compact_result: result_id returned", data.result_id === "smoke-pi-1");

    const page = await callCapability(client, "retrieve_result", { result_id: "smoke-pi-1", offset: 0, limit: 50 });
    const pageLines = (page.content ?? "").split("\n");
    check(
      "retrieve_result: page 1 recalls original first line",
      page.ok === true &&
        pageLines[0].startsWith("[result_id=smoke-pi-1") &&
        pageLines[1] === "region,discount,sales,revenue",
      `first lines: ${JSON.stringify(pageLines.slice(0, 2))}`,
    );

    const event = piEventToTrajectoryEvent(
      "turn_start",
      { type: "turn_start", turnIndex: 0, timestamp: 1756000000000, userInput: "冒烟:分析销量" },
      CTX,
    );
    check("trajectory: event dict built", event !== null);
    const good = await callCapability(client, "evolution_record_event", event as NonNullable<typeof event>);
    check("evolution_record_event: valid accepted", good.ok === true, JSON.stringify(good.error ?? null));

    const invalid = { ...(event as NonNullable<typeof event>), data: { user_input_digest: "打折是否导致销量上升" } };
    const bad = await callCapability(client, "evolution_record_event", invalid);
    check(
      "evolution_record_event: raw content rejected with validation_error",
      bad.ok === false && bad.error?.code === "validation_error",
      JSON.stringify(bad.error ?? null),
    );
  });
}

async function main(): Promise<void> {
  unitTranslate();
  unitPreset();
  await capabilityRoundTrips();
}

main()
  .then(() => {
    console.log(failed === 0 ? "PI SMOKE: PASS" : "PI SMOKE: FAIL");
    process.exit(failed === 0 ? 0 : 1);
  })
  .catch((err: unknown) => {
    console.log("PI SMOKE: FAIL");
    console.error(err);
    process.exit(1);
  });

/**
 * No-LLM smoke test for the dsh adapter: exercises the shared capability
 * client end-to-end plus the pure units. Prints `[PASS]/[FAIL]` per check and
 * a final `DSH SMOKE: PASS|FAIL`; the exit code mirrors the verdict.
 *
 * Env isolation note (verified 2026-08-26 @modelcontextprotocol/sdk 1.30.0):
 * the SDK stdio transport passes only a SAFE-LIST of env vars to the child
 * (HOME/PATH/USER/...). Custom `DAA_CAPABILITIES_*` vars do NOT propagate, so
 * isolation is achieved through the two levers that DO reach the child:
 *   - HOME override  → ~/.daa store + trajectory roots land in the tmp dir
 *   - process.chdir  → cwd-based defaults (allowed roots, artifact root)
 */

import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { connectCapabilityServer, callCapability } from "../../shared/capability-client.ts";
import { compactToolResult, shouldAsk } from "../src/compaction.ts";
import { digest, dshRecordToTrajectoryEvent } from "../src/translate.ts";

// smoke/ -> deepseek -> harnesses -> repo root (fileURLToPath decodes %20 etc).
const REPO_ROOT = fileURLToPath(new URL("../../../", import.meta.url));
const TMP = mkdtempSync(join(tmpdir(), "daa-dsh-smoke-"));

// Must run BEFORE the first connect: the child inherits these at spawn time.
process.env.PATH = `${REPO_ROOT}.venv/bin:${process.env.PATH ?? ""}`;
process.env.HOME = TMP;
process.env.DAA_CAPABILITIES_HOME = join(TMP, "store");
process.env.DAA_CAPABILITIES_ARTIFACTS = join(TMP, "artifacts");
process.env.DAA_CAPABILITIES_ALLOWED_ROOTS = TMP;
process.env.DAA_CAPABILITIES_EVOLUTION_ROOT = join(TMP, "trajectories");
process.chdir(TMP);

const EXPECTED_TOOLS = new Set([
  "causal_analyze", "causal_estimate", "causal_report",
  "evolution_record_event", "evolution_verify_trajectory",
  "reporting_render_chart", "reporting_render_html", "reporting_report_context",
  "reporting_report_contract", "reporting_report_need",
  "retrieve_result", "sampling_compact_result",
  "tabular_data_profile", "tabular_data_quality", "tabular_join_plan",
  "tabular_metric_contract", "tabular_nl_query", "tabular_python_exec",
  "tabular_read_file",
]);

const DIGEST_RE = /^[0-9a-f]{12}:\d+$/;
const TS_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;

let failed = 0;
function check(name: string, ok: boolean, detail = ""): void {
  console.log(`${ok ? "[PASS]" : "[FAIL]"} ${name}${ok || detail === "" ? "" : ` :: ${detail}`}`);
  if (!ok) failed += 1;
}

function unitTests(): void {
  // shouldAsk boundary: cheap pre-filter floor (server owns the real trigger).
  check("shouldAsk: == floor stays", shouldAsk("x".repeat(2000), 2000) === false);
  check("shouldAsk: floor+1 asks", shouldAsk("x".repeat(2001), 2000) === true);
  check("shouldAsk: default floor 2000", shouldAsk("x".repeat(2001)) === true);

  // digest shape.
  check("digest: sha256[:12]:len", DIGEST_RE.test(digest("hello")) && digest("hello").endsWith(":5"));

  const ctx = { sessionId: "smoke-session", turn: 0 };
  const time = Date.parse("2026-08-26T12:00:00Z");

  const ts = dshRecordToTrajectoryEvent({ type: "turn/start", seq: 1, time, data: { turn: 0 } }, ctx);
  check(
    "translate: turn/start -> turn_start",
    ts !== null && ts.event === "turn_start" && ts.harness === "dsh" &&
      ts.session_id === "smoke-session" && ts.turn === 0 && TS_RE.test(ts.ts) &&
      DIGEST_RE.test(String(ts.data.user_input_digest)),
    JSON.stringify(ts),
  );

  const tc = dshRecordToTrajectoryEvent(
    { type: "tool/call", seq: 2, time, data: { turn: 0, step: 0, callId: "call_1", name: "mcp__daa__tabular_read_file", arguments: '{"file_path":"/tmp/a.csv"}' } },
    ctx,
  );
  check(
    "translate: tool/call -> tool_call",
    tc !== null && tc.event === "tool_call" && tc.data.tool === "mcp__daa__tabular_read_file" &&
      DIGEST_RE.test(String(tc.data.input_digest)) &&
      String(tc.data.input_digest) === digest('{"file_path":"/tmp/a.csv"}'),
    JSON.stringify(tc),
  );

  const tr = dshRecordToTrajectoryEvent(
    {
      type: "tool/result", seq: 3, time,
      data: {
        turn: 0, step: 0,
        message: {
          id: "m1", role: "user",
          content: [{ type: "tool-result", toolCallId: "call_1", content: [{ type: "text", text: "a,b,c\n1,2,3" }] }],
          source: { kind: "tool", callId: "call_1" },
        },
      },
    },
    { ...ctx, toolNameByCallId: new Map([["call_1", "mcp__daa__tabular_read_file"]]) },
  );
  check(
    "translate: tool/result -> tool_result (correlated tool name)",
    tr !== null && tr.event === "tool_result" && tr.data.tool === "mcp__daa__tabular_read_file" &&
      tr.data.ok === true && tr.data.chars === 11 &&
      String(tr.data.output_digest) === digest("a,b,c\n1,2,3"),
    JSON.stringify(tr),
  );

  const endOf = (reason: unknown): string | undefined =>
    dshRecordToTrajectoryEvent({ type: "turn/end", seq: 4, time, data: { turn: 0, reason } }, ctx)?.data.outcome as string | undefined;
  check("translate: turn/end completed -> complete", endOf({ kind: "completed" }) === "complete");
  check("translate: turn/end aborted -> interrupted", endOf({ kind: "aborted", reason: { kind: "user" } }) === "interrupted");
  check("translate: turn/end error -> error", endOf({ kind: "error", error: { message: "x", code: "UNKNOWN" } }) === "error");
  check("translate: unknown record -> null", dshRecordToTrajectoryEvent({ type: "todo/write", data: { todos: [] } }, ctx) === null);
  check("translate: garbage -> null", dshRecordToTrajectoryEvent(42, ctx) === null);
}

async function capabilityTests(): Promise<void> {
  const smallCsv = join(TMP, "small.csv");
  writeFileSync(smallCsv, "id,name,value\n1,alpha,10\n2,beta,20\n3,gamma,30\n");
  const bigCsv = join(TMP, "big.csv");
  const bigLines = ["id,name,value"];
  for (let i = 0; i < 2000; i += 1) bigLines.push(`${i},name-${i},${(i * 7) % 101}`);
  const bigText = bigLines.join("\n");
  writeFileSync(bigCsv, bigText + "\n");

  const client = await connectCapabilityServer();
  try {
    // A. Tool surface: exactly the 19 capabilities.
    const listed = await client.listTools({});
    const names = new Set((listed.tools ?? []).map((t) => t.name));
    const missing = [...EXPECTED_TOOLS].filter((n) => !names.has(n));
    const extra = [...names].filter((n) => !EXPECTED_TOOLS.has(n));
    check(
      `capability server exposes ${EXPECTED_TOOLS.size} exact tools (got ${names.size})`,
      names.size === EXPECTED_TOOLS.size && missing.length === 0 && extra.length === 0,
      `missing=${missing.join(",")} extra=${extra.join(",")}`,
    );

    // B. tabular_read_file on a small fixture (allowed roots default to cwd).
    const read = await callCapability(client, "tabular_read_file", { file_path: smallCsv });
    check("tabular_read_file: ok envelope on tmp fixture", read.ok === true && (read.content ?? "").includes("alpha"), JSON.stringify(read.error ?? read.content?.slice(0, 80)));

    // C. Big-table compaction through the adapter seam.
    check("big fixture exceeds ask floor", shouldAsk(bigText));
    const replacement = await compactToolResult(client, {
      text: bigText,
      toolName: "mcp__daa__tabular_read_file",
      resultId: "smoke-dsh-1",
    });
    check(
      "compactToolResult: sampling summary replaces big table",
      replacement !== null && replacement.includes("数据采样摘要") &&
        replacement.includes("retrieve_result") && replacement.includes("smoke-dsh-1") &&
        replacement.length < bigText.length,
      replacement === null ? "returned null" : `len=${replacement.length}`,
    );

    // D. Recall: the original first line comes back (page = header + body).
    const page = await callCapability(client, "retrieve_result", { result_id: "smoke-dsh-1", offset: 0, limit: 50 });
    const pageLines = (page.content ?? "").split("\n");
    check(
      "retrieve_result: original first line",
      page.ok === true && pageLines[0].startsWith("[result_id=smoke-dsh-1") &&
        pageLines[1] === "id,name,value" && page.data?.total_lines === 2001,
      JSON.stringify(page.error ?? pageLines.slice(0, 2)),
    );

    // E. Trajectory recording: valid event in, invalid rejected.
    const validEvent = {
      event: "turn_start", ts: "2026-08-26T12:00:00.000Z", session_id: "smoke-session",
      turn: 0, harness: "dsh", data: { user_input_digest: digest("hello") },
    };
    const rec = await callCapability(client, "evolution_record_event", validEvent);
    check("evolution_record_event: valid event recorded", rec.ok === true && rec.data?.written === true, JSON.stringify(rec.error ?? rec.data));

    const invalidEvent = {
      event: "turn_start", ts: "2026-08-26T12:00:00.000Z", session_id: "smoke-session",
      turn: 0, harness: "dsh",
      data: { user_input_digest: digest("x"), raw_dump: "RAW CONTENT MUST BE REJECTED ".repeat(20) },
    };
    const bad = await callCapability(client, "evolution_record_event", invalidEvent);
    check(
      "evolution_record_event: raw content -> validation_error",
      bad.ok === false && bad.error?.code === "validation_error",
      JSON.stringify(bad.error ?? bad.data),
    );
  } finally {
    await client.close();
  }
}

unitTests();
await capabilityTests();
console.log(failed === 0 ? "DSH SMOKE: PASS" : `DSH SMOKE: FAIL (${failed} failed)`);
process.exit(failed === 0 ? 0 : 1);

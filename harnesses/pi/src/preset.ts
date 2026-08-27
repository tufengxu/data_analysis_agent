/**
 * 装配清单 (assembly manifest) for the Pi adapter. Pure data + env parsing;
 * no pi imports, no I/O. The registered tool name for a capability is always
 * `daa_<capability>` so the data-analyst preset prompt can reference them
 * uniformly.
 */

export const DAA_TOOL_PREFIX = "daa_";

/** The 19 capability tools exposed by `data-agent-capabilities mcp`. */
export const CAPABILITY_TOOL_NAMES = [
  "causal_analyze",
  "causal_estimate",
  "causal_report",
  "evolution_record_event",
  "evolution_verify_trajectory",
  "reporting_render_chart",
  "reporting_render_html",
  "reporting_report_context",
  "reporting_report_contract",
  "reporting_report_need",
  "retrieve_result",
  "sampling_compact_result",
  "tabular_data_profile",
  "tabular_data_quality",
  "tabular_join_plan",
  "tabular_metric_contract",
  "tabular_nl_query",
  "tabular_python_exec",
  "tabular_read_file",
] as const;

export type CapabilityToolName = (typeof CAPABILITY_TOOL_NAMES)[number];

/** Registered (LLM-visible) tool name for a capability: `daa_<capability>`. */
export function registeredToolName(capability: string): string {
  return DAA_TOOL_PREFIX + capability;
}

/** 数据分析 Agent 系统提示(preset)。 */
export const DATA_ANALYST_SYSTEM_PROMPT = [
  "你是数据分析 Agent(DataAnalysisAgent / Pi harness)。使用 daa_* 工具完成分析任务:",
  "",
  "- 表格分析:先用 daa_tabular_data_profile / daa_tabular_data_quality 了解数据结构与质量,",
  "  再用 daa_tabular_python_exec(持久内核,变量跨调用存活)做变换、统计与可视化;",
  "  口径先用 daa_tabular_metric_contract 固化,跨表先用 daa_tabular_join_plan 选键。",
  "- 图表与 HTML 报告:用 daa_reporting_render_chart 生成图表,再用 daa_reporting_render_html",
  "  输出自包含 H5 报告(优先传 document 结构,经 QA 闸)。",
  "- 因果分析:先用 daa_causal_analyze 归一化因果问题并做就绪检查,效应估计用 daa_causal_estimate,",
  "  汇总用 daa_causal_report;未就绪的因果问题不得下因果结论,相关证据只用相关表述。",
  "",
  "超大工具结果会被自动压缩为采样摘要;需要原文时用 daa_retrieve_result(result_id=..., offset=0, limit=50) 分页取回。",
].join("\n");

export const DEFAULT_COMPACT_TRIGGER = 8000;
export const DEFAULT_MAX_CHARS = 50000;
export const DEFAULT_CONTEXT_PRESSURE = 0.5;

export interface SeamConfig {
  /** tool_result textual length above this triggers best-effort compaction. */
  compactionTriggerChars: number;
  /** 0..1 context pressure hint passed to sampling_compact_result. */
  contextPressure: number;
  /** Length budget passed as max_chars. */
  maxChars: number;
}

/** Read the seam knobs from env (invalid values fall back to defaults). */
export function seamConfigFromEnv(env: NodeJS.ProcessEnv = process.env): SeamConfig {
  const trigger = Number.parseInt(env.DAA_COMPACT_TRIGGER ?? "", 10);
  const pressure = Number.parseFloat(env.DAA_PI_PRESSURE ?? "");
  return {
    compactionTriggerChars:
      Number.isFinite(trigger) && trigger > 0 ? trigger : DEFAULT_COMPACT_TRIGGER,
    contextPressure:
      Number.isFinite(pressure) && pressure >= 0 && pressure <= 1 ? pressure : DEFAULT_CONTEXT_PRESSURE,
    maxChars: DEFAULT_MAX_CHARS,
  };
}

export interface PresetDoc {
  name: string;
  version: string;
  harness: "pi";
  system_prompt: string;
  tools: Array<{ capability: CapabilityToolName; registered_as: string }>;
  seam: {
    capability_server: { command: string; args: string[]; transport: "stdio" };
    compaction: {
      on: "tool_result";
      trigger_chars: number;
      max_chars: number;
      context_pressure: number;
      env: { DAA_COMPACT_TRIGGER: string; DAA_PI_PRESSURE: string };
      retrieve: string;
    };
    trajectory: {
      contract: "daa.trajectory.v1";
      harness: "pi";
      record_via: "evolution_record_event";
      pi_events: string[];
    };
  };
}

/** Build the JSON-able assembly manifest for this adapter (pure). */
export function buildPresetDoc(env: NodeJS.ProcessEnv = process.env): PresetDoc {
  const seam = seamConfigFromEnv(env);
  return {
    name: "daa-pi-data-analyst",
    version: "0.1.0",
    harness: "pi",
    system_prompt: DATA_ANALYST_SYSTEM_PROMPT,
    tools: CAPABILITY_TOOL_NAMES.map((capability) => ({
      capability,
      registered_as: registeredToolName(capability),
    })),
    seam: {
      capability_server: { command: "data-agent-capabilities", args: ["mcp"], transport: "stdio" },
      compaction: {
        on: "tool_result",
        trigger_chars: seam.compactionTriggerChars,
        max_chars: seam.maxChars,
        context_pressure: seam.contextPressure,
        env: {
          DAA_COMPACT_TRIGGER: String(seam.compactionTriggerChars),
          DAA_PI_PRESSURE: String(seam.contextPressure),
        },
        retrieve: "retrieve_result(result_id=<id>, offset=0, limit=50)",
      },
      trajectory: {
        contract: "daa.trajectory.v1",
        harness: "pi",
        record_via: "evolution_record_event",
        pi_events: ["turn_start", "turn_end", "tool_execution_end"],
      },
    },
  };
}

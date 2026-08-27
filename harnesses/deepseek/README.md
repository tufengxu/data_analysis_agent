# dsh (DeepSeek Harness) 适配层

DataAnalysisAgent 能力层在 DeepSeek Harness(dsh,Cordis 插件运行时)上的适配器。
职责是**纯胶水**:不重复注册工具、不实现分析逻辑,只做两件事——

1. **超大工具结果压缩**(`src/index.ts` 挂 `tools/post-execute` 瀑布):官方
   `@deepseek-ai/dsh-mcp-client` 插件把能力服务器的 19 个工具以
   `mcp__daa__<capability_name>` 形态挂进 dsh;当某次调用的文本结果超过阈值
   (默认 8000 字符,`DAA_COMPACT_TRIGGER` 可调)时,经共享客户端调用
   `sampling_compact_result`,把结果替换为「数据采样摘要 + 回取句柄」。
2. **轨迹事件旁路**(挂 `session/event`):把 dsh 的持久会话记录翻译成
   `daa.trajectory.v1` 契约事件(只记结构不记数值:digest 与计数),按会话排队,
   在 `turn/end` 批量写入 `evolution_record_event`(fire-and-forget,绝不影响主循环)。

两条臂都是旁路:任何失败静默降级(保留原结果/丢弃事件),绝不把成功的工具调用变成错误。

## 前置

- Node ≥ 22.19(本机实测 v24.15.0;npm 11)。dsh 为 **dev preview**,版本
  **pin `@deepseek-ai/dsh@0.1.1-rc.2`**(devDependency,供类型核对与本地 CLI)。
- 仓库 `.venv` 已可编辑安装(`uv pip install -e ".[data,dev,web]"`),
  能力服务器入口 `.venv/bin/data-agent-capabilities` 经 **PATH 查找**。
- 共享 MCP 客户端:`harnesses/shared/capability-client.ts`(官方
  `@modelcontextprotocol/sdk`;进程 spawn 全部在 SDK transport 内,本目录零进程构造代码)。

## 安装

```bash
cd harnesses/deepseek
npm install --no-fund --no-audit     # 装入本目录 node_modules(含 pin 版 dsh)

# 让 dsh profile 能以相对路径加载本插件(推荐软链;相对路径以
# ~/.dsh/profiles/<profile>/ 为基准解析,含空格的仓库路径不能直接当 specifier):
ln -s "$(pwd)" ~/.dsh/profiles/headless/daa    # 在 harnesses/deepseek 目录内执行
# 用 tui/web profile 时同理换目录名
```

## 无 key 冒烟(不调模型)

```bash
npm run typecheck   # tsc --noEmit(strict NodeNext)
npm run smoke       # node smoke/smoke.ts,打印 [PASS]/[FAIL] 与 DSH SMOKE: PASS|FAIL
```

冒烟覆盖:19 个能力工具名精确比对;`tabular_read_file` 读临时 fixture;
2000 行大表经 `compactToolResult`(resultId=smoke-dsh-1)压缩后含「数据采样摘要」
与 `retrieve_result` 回取提示;`retrieve_result` 取回原文首行;
`evolution_record_event` 合法/非法(validation_error)事件;
`shouldCompact` 边界与 `dshRecordToTrajectoryEvent` 各类记录的纯单元断言。

环境隔离说明:MCP stdio transport 只向子进程传递安全名单环境变量
(HOME/PATH/USER…),`DAA_CAPABILITIES_*` 不会透传,冒烟脚本以 **HOME 覆盖 +
chdir** 两个杠杆实现「全新 tmp 目录」隔离(见 `smoke/smoke.ts` 头注释)。

## cordis.example.yml 说明

本仓库整体**零 YAML**;`cordis.example.yml` 是 dsh 基座自身强制的配置格式,
是本适配层唯一的示例配置文件(以 patch 覆盖层使用,不是项目配置):

```bash
PATH="/path/to/DataAnalysisAgent/.venv/bin:$PATH" \
  harnesses/deepseek/node_modules/.bin/dsh \
  --profile headless --patch harnesses/deepseek/cordis.example.yml "任务描述"
```

patch 语义(@0.1.1-rc.2 实测):`{insert: [...]}` 追加新插件行、`{id: ...}` 覆盖
已有行。文件内含:`mcp-daa`(官方 mcp-client,stdio → `data-agent-capabilities mcp`,
serverName=daa)、`daa-capabilities`(本插件,`./daa/src/index.ts` 相对于 profile 目录,
依赖上面的软链;不做软链时改用 `file://` 绝对 URL,空格需 `%20` 转义)、以及权限预设注释
(dsh 默认 sandbox-policy 已是 `workspace-write`,可用 `DSH_PERMISSION_MODE` 覆盖)。

## 真实运行

### CLI(本机已实测,2026-08-26)

```bash
cd /tmp/你的工作目录                # 能力服务器 allowed-roots 默认取进程 cwd
PATH="/path/to/DataAnalysisAgent/.venv/bin:$PATH" \
  harnesses/deepseek/node_modules/.bin/dsh \
  --profile headless --patch /path/to/cordis.example.yml \
  "用 mcp__daa__tabular_read_file 读取 small.csv 并告诉我列名"
```

实测结论(真实 DeepSeek 端点):模型成功调用 `mcp__daa__tabular_read_file`;
32KB 大表被压缩为 1.3KB 采样摘要(模型复述了「数据采样摘要」与回取句柄);
轨迹事件落盘 `~/.daa/trajectories/v2/session-*.jsonl`
(turn_start/tool_call/tool_result/turn_end,均为 digest/计数)。

### Python SDK(未验证,需真实 key)

`python/agent.py` 按 PyPI `deepseek-harness-sdk 0.1.1rc1` 的真实签名编写
(签名经 wheel 反编译核对:`DeepSeekHarness(provider/model/cwd/session_root/cordis/env)`、
`.run(prompt, session_id=...)`),但**未在本任务环境实际运行**(安装该包时网络受限)。

```bash
uv pip install deepseek-harness-sdk
export DEEPSEEK_API_KEY=sk-...
.venv/bin/python harnesses/deepseek/python/agent.py harnesses/deepseek/cordis.example.yml
```

## 排障

- `spawn data-agent-capabilities ENOENT`:启动 dsh 前没把 `.venv/bin` 前置到 PATH
  (mcp-client 与本插件各自 spawn 能力服务器,均走 PATH 查找)。
- `plugin tree failed to load: ... (./daa/src/index.ts)`:软链不存在,或相对路径
  写错基准(dsh 以 `~/.dsh/profiles/<profile>/` 为基准;`!!js` 表达式不作用于
  插件行 `name` 字段,只能用静态字符串)。
- 想看旁路细节:`DAA_DSH_DEBUG=1` 会在 stderr 打印 post-execute 长度、压缩前后
  字符数、每条 session/event 与被拒的轨迹事件。
- 压缩没触发:阈值 `DAA_COMPACT_TRIGGER`(默认 8000)或服务端增益门控判定
  「摘要不够小、原文又未超 max_chars」→ 原样保留(契约行为)。
- 轨迹没落盘:确认 `~/.daa/trajectories/v2/`(或 `DAA_CAPABILITIES_EVOLUTION_ROOT`);
  批量写入发生在 `turn/end` 与插件卸载时,均为尽力而为。

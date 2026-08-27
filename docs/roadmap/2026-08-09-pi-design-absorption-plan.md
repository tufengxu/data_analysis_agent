# Pi 设计哲学吸纳与 DAA 优化方案

> Status: planning baseline, 2026-08-09
>
> Scope: 吸纳 Pi Agent SDK(earendil-works/pi, v0.84.1, commit c3e7bc60)中经过大规模
> 验证的设计,同时冻结并保留 DAA 已有优势,使 DAA 成为优秀好用的数据分析 Agent。
> 本方案是 2026-07-05 Phase 1/2 执行计划的补充,不推翻其结论。
>
> Note on planned paths: 未来才会存在的文件路径一律写纯文本(不加代码跨度),
> 避免死链检查误报;现存文件用代码跨度。
>
> 调研依据: 本地 harness 逐文件审计 + Pi monorepo 源码级调研(2026-08-08/09)。
> 关键结论: Pi 为纯 TypeScript(Node ≥ 22),无 Python 绑定,RPC 模式无客户端侧
> 工具注册;0.x 版本线 269 个 release 中 44 个含 Breaking Changes;无内置权限系统。

## 0. Executive Decision

**吸设计,不吸运行时。** DAA 保持 Python 单运行时宿主。Pi 的每一项能力按
「设计移植到 Python」吸纳,不引入 Node 进程、不使用 pi RPC/SDK 于产品路径。
唯一的"直接使用"是零运行时依赖的**格式与标准兼容**(agentskills.io SKILL.md、
AGENTS.md 发现规则)。

判据:迁移评估(2026-08-09)已证明语言墙 + 0.x breaking 税 + 无权限系统使
运行时级复用为负收益;而 Pi 真正领先 DAA 的是**设计**——provider 抽象、
hook 契约、steering、session 树、compaction 的 turn 边界策略——这些均可
在 3,500–4,000 行的 harness 核心内以 Python 原生实现,且改动面已被
现有依赖规则(`scripts/drift_rules.py`)和 78 个测试约束。

## 1. 两条冻结清单

### 1.1 DAA 优势冻结清单(任何 Wave 不得削弱,quality gate 回归保护)

| 优势 | 载体 | 保护方式 |
| --- | --- | --- |
| 采样摘要替代盲截断(L0–L3,沙箱内精确 + stdlib 兜底)| `src/data_analysis_agent/sampling/` | ADR 0001/0003;现有测试 |
| CCR-lite 原文回取 | `src/data_analysis_agent/sampling/result_store.py` + `src/data_analysis_agent/tools/retrieve_result.py` | ADR 0003 |
| 持久分析 kernel(变量/DataFrame 跨调用存活 + 崩溃重启显式报告)| `src/data_analysis_agent/kernel/` | 现有测试 |
| deny-first 权限 + ToolGate + 审批通道(超时=deny)| `src/data_analysis_agent/security/` + `src/data_analysis_agent/server/approval.py` | Pi 无权限系统,此处零借鉴、只加固 |
| CJK-aware 压缩配对安全(ASCII 0.25/CJK 1.0 估算)| `src/data_analysis_agent/context/compression.py` | Wave 5 明确保留该估算器 |
| 纯 stdlib 领域层(reporting/causal 零 harness 依赖)| `src/data_analysis_agent/reporting/` + `src/data_analysis_agent/causal/` | ADR 0009/0010;drift 规则 |
| 记结构不记数值的领域记忆 + 离线自进化管线 | `src/data_analysis_agent/memory/` + `src/data_analysis_agent/evolution/` | ADR 0004/0005 |
| 工程纪律(机器校验 manifest、drift 规则、阻断式 quality gate、mypy strict)| `docs/ARCHITECTURE.md` + `scripts/` | 不变 |

### 1.2 Pi 哲学吸纳清单(写入项目原则,零代码成本)

- **小内核 + 扩展点,拒绝功能膨胀**:Pi 刻意不内置 MCP/sub-agents/plan mode。
  DAA 对应承诺:不加 MCP、不加多 agent 编排、不加向量记忆(与 Phase 1/2 计划
  的 avoided path 一致),新能力优先以工具 + 技能形态进入,而非 loop 特性。
- **model decides what, harness decides how much**:双方哲学同源,维持。
- **工具即产品面**:Pi 用"CLI tools with READMEs"替代协议集成;DAA 对应做法是
  领域工具(data_profile/join_planner/metric_contract 等)保持只读、自描述、
  确定性,这是数据分析 Agent 的核心 UX,继续沿此扩展。

## 2. Pi 能力处置分类

| Pi 能力 | 处置 | 去向 |
| --- | --- | --- |
| pi-ai 多 provider 抽象(约 40 provider、统一流事件、错误分类)| **设计移植** | Wave 1 |
| Hook 契约(beforeToolCall/afterToolCall/transformContext/shouldStopAfterTurn)| **设计移植** | Wave 2 |
| Steering / follow-up 双队列 | **设计移植** | Wave 3 |
| JSONL 树形 session(id/parentId、原位分支、entry 类型体系)| **设计移植** | Wave 4 |
| Compaction:turn 边界切割 + keepRecentTokens + split-turn 双摘要 | **设计移植(融合)** | Wave 5 |
| agentskills.io SKILL.md 标准 + 挂载 ~/.claude/skills | **直接使用(格式兼容)** | Wave 6 |
| 工具流式进度(onUpdate → tool_execution_update)| **设计移植**(激活既有桩)| Wave 7 |
| Extension 机制(jiti 进程内 TS 模块)| **不采**:DAA 的回调注入 + runtime 组装根已覆盖同等扩展面;Python 侧无对应轻加载收益 | — |
| RPC / pi-protocol / pi-server | **不采**:语言墙 + 无工具注册 | — |
| harness v2(durable runs/lanes/SQLite)| **不采**:Pi 自身未迁,API 明示可破坏 | — |
| TUI(pi-tui)| **不采**:DAA 的 UX 主战场是 Web Workbench + rich CLI | — |
| 容器化作为安全边界 | **部分借鉴**:作为 ADR 0008 沙箱威胁模型的补充选项写入文档,不改变 deny-first 主路径 | Wave 7(文档)|

## 3. 执行 Waves

依赖序:W1 → W2 → (W3 ∥ W4) → W5 → (W6 ∥ W7)。每个 Wave 独立过
`scripts/quality_gate.py` + eval fixture 基线无回归后才进入下一个。

### Wave 0:基线冻结(0.5 周)

- 用 `src/data_analysis_agent/evolution/evaluator.py` 现有 fixture 机制跑一轮
  全量 eval,产出基线报告存 docs/roadmap/ 下(路径:docs/roadmap/2026-08-pi-absorption-baseline.md)。
- 验收:基线报告落盘;后续每个 Wave 结束重跑,数值锚(ADR 0005 例外)与
  方法断言均不得回归。

### Wave 1:多 provider 抽象 + Anthropic 语义收编(P0,2–3 周)

**动机**:DAA 唯一重大架构债。Pi 的 pi-ai 证明了正确切面:provider 只负责
「请求构造 + 流事件归一 + 错误分类」,loop 只见统一类型。

**现状债务**(审计确认):`src/data_analysis_agent/protocol/client.py` 硬编码
anthropic SDK;Anthropic 语义泄漏至 `src/data_analysis_agent/recovery.py`
(字符串匹配 "prompt is too long")、`src/data_analysis_agent/context/compression.py`
(tool_use/tool_result 配对)、`src/data_analysis_agent/tools/base.py`
(to_anthropic_tool)、`src/data_analysis_agent/state_machine.py`
(to_anthropic_format、裸 stop_reason 字符串)。

**改动**:

1. protocol 下新增 provider 抽象(路径:src/data_analysis_agent/protocol/provider.py):
   `Provider` 接口 = `stream(request) -> AsyncIterator[UnifiedEvent]` +
   `format_tools(list[ToolSpec])` + `classify_error(exc) -> ErrorClass`。
   `ErrorClass` 枚举:CONTEXT_OVERFLOW / TRANSIENT / AUTH / FATAL——recovery.py
   改判 ErrorClass,删除字符串匹配。
2. 统一 `StopReason` 枚举(TOOL_USE / END_TURN / MAX_TOKENS / …)替换裸字符串;
   `Message.to_anthropic_format` 与 `Tool.to_anthropic_tool` 下沉到 Anthropic
   provider 内部。
3. 首批 provider:Anthropic(现有 client.py 改造)+ OpenAI-compat
   (一个实现覆盖 Ollama/vLLM/LM Studio/DeepSeek 等,借鉴 Pi 的 models.json
   注册表设计,配置路径:~/.daa/models.json)。Google 后置,不在本 Wave。
4. 压缩管线的配对规则改为消费统一消息类型;配对语义本身保留
   (OpenAI tool_calls 同样需要配对闭合,抽象不漏)。

**验收**:现有 78 测试 + 新增 provider 契约测试(两 provider 跑同一 fake
对话夹具,事件序列一致);`data-agent doctor` 能报告激活 provider;
recovery 阶梯在 fake OpenAI-compat 429/overflow 上走通;grep 证实
`agent_loop.py`/`recovery.py`/`compression.py` 零 "anthropic" 引用。

### Wave 2:统一 Hook 契约(1–1.5 周)

**动机**:Pi 的四个 hook 是被 160 万周下载验证过的最小扩展面。DAA 现有
注入点(approval_handler、memory_injector、memory_recorder、skill_registry、
sanitizer 调用)语义等价但形态零散,各自一个构造参数。

**改动**:

1. 定义 `AgentHooks` 协议(路径:src/data_analysis_agent/hooks.py):
   `before_tool_call`(返回 allow/block+reason,ToolGate.decide + 审批通道
   重组为其默认实现)、`after_tool_call`(采样压缩 compact_result、artifact
   落盘、memory_recorder 重组为链式实现)、`transform_context`(memory 注入 +
   sanitizer 移入)、`should_stop_after_turn`(为 W3 steering 预留)。
2. `agent_loop.py` 的 step 6–9 改为按序 await hooks;`runtime.py` 组装根
   负责按 preset 装配 hook 链。现有构造参数保留一个大版本作兼容别名,
   Wave 7 清除。
3. drift 规则新增:hooks.py 不得 import agent_loop/runtime/session(与
   tool_gate 同级接缝)。

**验收**:行为零变化(全部现有测试通过,无需改断言);新增 hook 顺序与
block 语义测试;`docs/ARCHITECTURE.md` manifest 同步。

### Wave 3:Steering / follow-up 队列(1 周)

**动机**:数据分析跑批耗时长(kernel 里跑 pandas 几十秒),用户中途纠偏
(「不对,先按地区分组」)目前只能 Ctrl-C 断轮重来,丢 kernel 之外的轮内
进展。Pi 的双队列语义(steer=turn 间插入、follow_up=结束后追加,
one-at-a-time 默认)直接适配。

**改动**:

1. `AgentSession` 增加 `steer(text)` / `follow_up(text)`;`agent_loop.py`
   step 9 判定 ContinueReason 前消费 steering 队列(注入为 user 消息,
   带显式「用户插话」标注);loop 结束后消费 follow-up 队列自动开新轮。
2. CLI 交互模式(`src/data_analysis_agent/__main__.py`)在流式输出期间监听
   输入行 → steer;Web 侧 `src/data_analysis_agent/server/app.py` 加
   POST /api/steer(复用 per-run CSRF 与 run_id 定位),SSE 增加
   steering_queued 事件(`src/data_analysis_agent/server/event_codec.py`
   字段冻结契约同步)。
3. TrajectoryLogger 记录 steering 为独立信号(自进化原料:被打断的轮次
   是高价值负反馈,接入 `src/data_analysis_agent/telemetry/feedback.py`
   的隐式信号面)。

**验收**:集成测试——工具执行中 steer,下一次 model call 的 context 含
插话且 tool ledger 无孤儿;Web 端到端手测(SSE 中发 steer)记录于 PR。

### Wave 4:Session 树形分支(1–1.5 周)

**动机**:数据分析的天然形态是「从同一画像出发试多条分析路径」。现有
`src/data_analysis_agent/persistence.py` 的 fork 是整文件复制,无谱系。
Pi session v3 的 id/parentId JSONL 树 + entry 类型体系是成熟答案。

**改动**:

1. JSONL 行增加 `id`/`parent_id`(8-hex,借鉴 Pi)与 `entry_type`
   (message / compaction / branch_point / label / custom;custom 不进
   LLM 上下文——给 telemetry/审计用)。旧格式(无 id 的线性 JSONL)
   读取时自动迁移,一次性重写(复用 `src/data_analysis_agent/jsonl_store.py`
   原子重写)。
2. `MessageStore.fork` 改为树内分支(新 branch_point entry,零复制);
   resume 沿 parent 链重建活跃路径,ledger 闭合逻辑不变。
3. CLI:data-agent sessions tree 子命令列出分支;`--resume` 接受分支 id。
   Web 侧本 Wave 不做 UI,只保证 API 不破坏。

**验收**:迁移测试(v0 线性 → 树,内容逐字节等价);fork 后两分支独立
append 互不污染;孤儿 tool_use 闭合在分支路径上仍成立。

### Wave 5:Compaction 融合(1 周)

**动机**:DAA 的 L1–L4(预算/剪窗/微压缩/staged collapse)比 Pi 细,
**保留**;L5 AutoCompact 的切割策略(head+tail2)比 Pi 的
turn 边界切割 + keepRecentTokens 预算 + split-turn 双摘要粗糙,**换**。

**改动**:

1. `context/compression.py` L5 改为:按 turn 边界切割压缩段(不劈开
   turn 内 tool 链),保留最近 keep_recent_tokens(默认对齐 Pi 的 20k,
   走 `src/data_analysis_agent/config.py` 可配),跨切割点的 turn 用
   split-turn 双摘要合并。
2. CJK 估算器、配对安全回退、L4 staged collapse-drain(413 零成本路径)
   全部保留——此三项是 DAA 增值,Pi 没有。
3. 摘要 prompt 增加数据分析定向指令:保留「数据集画像结论、口径定义、
   已确认的 join 键」优先于过程叙述(与 memory 的记结构不记数值一致)。

**验收**:现有 compression 测试全绿 + 新增 turn 边界测试(压缩点永不
落在 tool_use/tool_result 之间);长会话 fixture 压缩后 token 预算命中率
量化进基线报告。

### Wave 6:Skills 标准兼容(0.5–1 周)

**动机**:agentskills.io SKILL.md 是 Pi/Claude Code/Codex 共同标准,
DAA 的 DeclarativeSkill JSON(`src/data_analysis_agent/skills/loader.py`)
是自有格式。兼容读取 = 零成本接入用户已有技能生态(~/.claude/skills),
也让 DAA 自产技能可被其他 agent 消费。

**改动**:

1. loader.py 增加 SKILL.md 前置元数据(name/description)解析,映射为
   DeclarativeSkill;`src/data_analysis_agent/config.py` 增加 skills_paths
   (默认含 ~/.daa/skills,可选挂 ~/.claude/skills)。
2. 渐进披露语义对齐:匹配时只注入 SKILL.md 正文,skill 目录内其余文件
   由模型经 read_file 按需读取(路径需在权限白名单内——经现有
   permissions 引擎,不开新口子)。
3. evolution 管线产出的 candidate 技能增加 SKILL.md 导出(JSON 仍是
   内部真理源,导出是投影)。

**验收**:夹具 SKILL.md 装载/路由/工具白名单测试;权限测试证明技能
目录读取仍受 deny-first 约束;README 技能节更新。

### Wave 7:桩收尾与清理(1 周)

激活审计发现的预留位中有产品价值的三项,清除其余:

1. **工具流式进度**(借鉴 Pi onUpdate):`Tool.call` 增可选 `on_progress`
   回调;`python_exec` 在 kernel 长执行时上报心跳/部分 stdout;loop 发射
   既有 `ToolProgressEvent`(`src/data_analysis_agent/events.py` 已定义未用),
   CLI spinner 与 SSE 消费。数据分析场景刚需:跑 30 秒的 groupby 不再静默。
2. **只读工具并行**:`is_concurrency_safe` 且 is_read_only 的工具
   (data_profile/data_quality/join_planner 等)允许同轮并行执行;
   mutator 与 python_exec 维持串行。fail-closed 默认不变。
3. **清理**:删除 Wave 2 兼容别名;`TurnState`、PermissionRule.condition
   等确认无消费者的桩——condition 若 local_safe preset 无需求则删除
   (遵循「不为不可能场景写错误处理」);manifest 与 ARCHITECTURE 同步。
4. **文档**:ADR 0008 补容器化选项一节(Pi 立场引用);新增 ADR:
   「吸设计不吸运行时」决策及触发重评条件(Pi 1.0 semver + harness v2
   落地 + 进程内多语言工具协议三者齐备)。

**验收**:并行只读工具的竞态测试(共享 result_store);进度事件端到端
(CLI + SSE);quality gate 全绿。

## 4. 非目标(本方案明确不做)

- 不引入 Node/TS 运行时,不使用 pi RPC/SDK/extension 于产品路径。
- 不采 Pi harness v2、pi-protocol/server、pi-tui。
- 不加 MCP、sub-agents、plan mode、向量记忆(与 Phase 1/2 计划一致)。
- 不弱化 deny-first/审批/沙箱任何一层以换取「流畅度」。
- 不把 reporting/causal 领域层与任何新 harness 设施耦合(ADR 0009/0010 不变)。

## 5. 工作量与风险

总量:**8–10.5 人周**(W0 0.5 / W1 2–3 / W2 1–1.5 / W3 1 / W4 1–1.5 /
W5 1 / W6 0.5–1 / W7 1),单人全职约 2–2.5 个月;W3∥W4、W6∥W7 可并行
压缩日历时间。

| 风险 | 缓解 |
| --- | --- |
| W1 抽象漏(某 provider 语义装不进统一事件)| 首批只做 Anthropic + OpenAI-compat 两家,契约测试跑同一夹具;Google 等后置 |
| W2 hook 重组引入行为漂移 | 验收标准是「现有测试零改动通过」,任何断言修改都是红旗 |
| W4 session 格式迁移损坏历史 | 迁移前自动备份原文件(jsonl_store 原子重写已有此语义);迁移测试逐字节比对 |
| W5 换切割策略导致分析上下文丢失 | eval fixture 基线对比;摘要定向指令保留领域结论 |
| 多 Wave 期间 Pi 上游 API 漂移使借鉴目标失效 | 借鉴的是设计不是 API,漂移不影响;方案引用锚定 commit c3e7bc60 |
| 范围蔓延(借 Pi 之名加功能)| 第 4 节非目标清单 + quality gate 的 600 LOC 体积告警 |

## 6. 完成定义

全部 Waves 合入后,以下命题须同时为真,方可宣布本方案完成:

1. `data-agent` 可在 Anthropic 与至少一个 OpenAI-compat 本地模型间切换,
   恢复阶梯与压缩管线行为一致(契约测试证明)。
2. 运行中可插话纠偏(CLI 与 Web),且插话被轨迹记录为反馈信号。
3. 同一数据集画像可分支出多条分析路径,谱系可列出、可恢复。
4. 30 秒以上的 kernel 执行有可见进度。
5. ~/.claude/skills 下的 SKILL.md 技能可被路由使用,且受 deny-first 约束。
6. 第 1.1 节冻结清单全部优势的既有测试无一削弱;quality gate 与 eval
   基线全绿。

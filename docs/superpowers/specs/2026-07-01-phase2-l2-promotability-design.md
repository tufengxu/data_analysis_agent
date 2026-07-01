# Phase 2 · L2 技能质量与可晋升性 — 方案设计

> 自进化三层里的 L2(技能层,日/周节奏)。本设计把"评估—晋升"回路的两个断点(E4 冷启动、E5 过程不可见)接上,使 `evaluate` 能给出 `promote`/`retire` 而非清一色 `needs_review`。
>
> 配套阅读:`REPORT.md` §4.3(自进化分期)、ADR 0004(记忆记结构不记数值)、ADR 0005(评估只验方法/结构 + 样本门)、`2026-06-08-ccr-lite-result-retrieval-design.md`(同套"一手源码核对"风格)。

---

## 1. 背景与主要矛盾

自进化骨架已存在(trajectory → synthesize → evaluate → promote 离线管线),Phase 1 已把 L1 记忆写入回路接通。但 L2 的关键回路仍是断的,断在两处:

- **E5(过程不可见)**:轨迹只记了"调了什么工具"(name + 耗时 + 结果字符数),**没记"怎么调的"**——参数/代码骨架全丢。于是 synthesizer 的 LLM 反思拿到的是瘦记录,几乎无法提炼可复用配方。
- **E4(冷启动)**:`decide_promotion` 要 `n ≥ MIN_SAMPLES=5` 个**相关**任务才晋升/退役,否则一律 `needs_review`。而全仓库只有 1 个手写 eval task(`examples/eval_tasks/descriptive_smoke.json`)。于是:synthesizer 产 candidate → evaluate 永远 needs_review → 自进化回路死在 evaluate。

**主要矛盾:evaluate 给不出非 needs_review 的判定。** 主要方面是"样本不足"(E4)——只要评估任务集够大,E5 改善的反思质量才有用武之地;两者合力,回路才闭环。

---

## 2. 一手源码核对得到的设计校准(调查优先)

初判曾认为"2A 要把 tool input 从 client 铺到 ToolUseEvent"。核查源码后**否决**:

| 核查点                            | 事实                                                                                                               | 结论                              |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------ | --------------------------------- |
| `events.py:66-76`                 | `ToolUseEvent` 早有 `parameters: dict` 字段                                                                        | 不用加字段                        |
| `agent_loop.py:298-303`           | 构造时已 `ToolUseEvent(tool_use_id=item.id, tool_name=item.name, parameters=item.input, parameters_complete=True)` | 管线已通                          |
| `protocol/client.py`              | 只产原始 `ContentBlock`,不构造 ToolUseEvent                                                                        | 不用动 client                     |
| `telemetry/trajectory.py:121-122` | ToolUseEvent 分支只存 `(event.tool_name, self._monotonic())` 进 `_tool_starts`                                     | **唯一缺口:把 `parameters` 丢了** |

→ **2A 是单文件改动(trajectory.py)+ 隐私处理 + 一个配置开关 + synthesizer 联动**。不动 client、不动 event 协议。体量与风险都比初判小一档。

---

## 3. 范围(Phase 2 = 2A + 2B;2C 显式 defer)

**IN**

- **2A**:轨迹记录"成功过程"——`ToolCallRecord` 加 `input_digest` + `referenced_files`;捕获逻辑;路径脱敏;`enable_trajectory_inputs` 开关;synthesizer 拿到更厚的 turn 记录。
- **2B**:`evolution/eval_harvester.py` 自动收割 EvalTask + 冻结 fixture;`config.eval_tasks_dir()`;evaluator 读双目录;`harvest-eval` CLI。
- 两块各自的合成测试(`~/.daa/trajectories/` 当前为空,只能合成验证)。

**OUT(显式 defer)**

- **2C**:eval 接进 `scripts/quality_gate.py`(或独立 `eval_gate.py` 走发布/夜间节奏)——理由见 §7。
- **L3 策略层**:eval 基线出现前不做(REPORT §4.3)。
- 真实数据验证(无轨迹);content-hash fixture 消歧;synthesizer 的 LLM prompt 调参。

---

## 4. 分期与依赖(持久战)

```
2A (轨迹记录过程) ──► 2B (收割 eval 任务) ──► [defer] 2C (eval 进 gate)
        │                       │
        └─── 真依赖 ────────────┘
```

**2A→2B 是硬依赖**:harvester 靠 2A 产出的 `referenced_files` 才知道"这条轨迹碰过哪些数据文件"→ 才能冻结 fixture。没 2A,harvester 不知道冻结什么 → 没 fixture → evaluate 仍是死的。故 2A 必须先做,2B-first 会返工。

每块独立 TDD,块末走独立代码审查闭环(CLAUDE.md §2.9)。

---

## 5. 2A 组件设计 — 轨迹记录"成功过程"

### 5.1 数据结构(`telemetry/trajectory.py`)

`ToolCallRecord` 现状:`{name, is_error, duration_ms, result_chars}`。新增两字段:

| 字段                                | 用途                                                      | 消费方                        |
| ----------------------------------- | --------------------------------------------------------- | ----------------------------- |
| `input_digest: str`                 | 参数的脱敏 JSON 摘要(路径剥前缀、≤ `_INPUT_DIGEST_CHARS`) | synthesizer 反思(L2 学"过程") |
| `referenced_files: tuple[str, ...]` | 该次调用引用到的数据文件 basename                         | 2B 收割 fixture               |

**为何拆两份?** 反思要脱敏(防绝对路径/用户名随轨迹外泄),2B 要真实路径去冻结数据集。一份字段满足不了两个诉求;拆成"脱敏摘要给人看 + basename 给机器收割",隐私与功能都不妥协(鲁棒性)。

`_tool_starts` 由 `dict[str, tuple[str, float]]` 扩为 `dict[str, tuple[str, float, dict]]`(多挂一份 `parameters`)。新增模块常量 `_INPUT_DIGEST_CHARS = 1000`(与既有 `_DIGEST_CHARS = 2000` 并列;tool input 里的代码骨架价值高,但也要封顶防爆轨迹膨胀)。

### 5.2 捕获逻辑(同文件 `TrajectoryLogger.__call__`)

- **ToolUseEvent 分支**:除 `(name, started)` 外再存 `event.parameters`。
- **ToolResultEvent 分支**:pop 出 parameters,先抽 `referenced_files`(扫值里 `.csv/.tsv/.xlsx/.xls/.parquet` 结尾、或解析为 analysis_paths 下存在路径——best-effort,过收无害),再算 `input_digest`(脱敏 + 截断),一起塞进 `ToolCallRecord`。

### 5.3 隐私处理 `_digest_tool_input(params, *, analysis_paths, cap)`

- 递归遍历参数值,把命中 `~/`(HOME 前缀,**始终可用**)或 analysis_paths(若已透传则更紧)前缀的字符串替换为 `<path:basename>`。脱敏的硬依赖只是 HOME,analysis_paths 是可选增强——不构成对装配顺序的强约束;
- `json.dumps(ensure_ascii=False)` 后截到 `cap`,尾缀 `…(truncated)`;
- 定位:轨迹本就是本地 `~/.daa` 文件、用户自有,脱敏是"分享/同步时多一层保险",比例得当、不做更重(不上加密、不上哈希)。

### 5.4 配置开关(`config.py`)

新增 `enable_trajectory_inputs: bool = True`。

- **默认 True**:telemetry 侧信道整体已由 `enable_telemetry=False` opt-out,且现行轨迹已在记 `user_input`/`final_text_digest`(同属用户内容);input 是同类增量,不是新暴露面。本开关是更细粒度旋钮,给敏感部署单独关 input 记录而保留其余遥测。
- 关闭时:`input_digest=""`、`referenced_files=()`,其余字段照常——**降级而非崩溃**。
- 接线:`TrajectoryLogger` 构造时拿到开关(经 `AgentRuntime`/`AgentSession` 透传,沿用既有 telemetry 装配路径,不新开通道)。

### 5.5 synthesizer 联动(`evolution/synthesizer.py`,小改)

`reflect_fn(cluster.turns)` 现在拿到的 turn 记录里 `tool_calls[*]` 带 `input_digest`——LLM 反思终于能看到**真实参数/代码骨架**。`is_eligible`/`cluster_uncovered`/`load_corpus` 均不变。**只保证数据到位,不保证 LLM 一定用好(prompt 调参是非目标)。**

### 5.6 向后兼容

ToolCallRecord 加字段是加法。`load_turns` 返回 `list[dict]`,旧 jsonl 缺新字段时下游用 `.get(..., 默认)` 读回,不报错。既有 trajectory 测试不受破坏。

---

## 6. 2B 组件设计 — eval 任务自动收割(解 E4 冷启动)

### 6.1 输入与产出

- **输入**:复用 `synthesizer.load_corpus(trajectories_dir)`(已合并 feedback,不重写加载器)。
- **产出**:每个合格 turn → 一份 `EvalTask` JSON + 把它引用的数据文件拷进 `fixtures/`。
- **合格判定**:复用 `is_eligible` 语义(COMPLETED、无 bad/rephrase、`model_turns≥4`)——只从"做成了的"里学,镜像 synthesizer。

### 6.2 路径恢复(核心难点:隐私 vs 功能)

2A 出于隐私只存了 basename;harvester 要真实文件去冻结。解法:

- harvester 接 `data_search_paths: list[Path]`(用户收割时给,通常是 agent 当初的 `analysis_paths`);
- basename 在这些目录解析,**best-effort**:找不到 → 跳过该任务并 `log`(**不许静默丢**);同名歧义 → 取首个并 `log`。
- **不做**捕获期算 content-hash(给热路径加 I/O、capture 时不知 data_root)。同名消歧作为已知可选加固,先 YAGNI。

### 6.3 EvalTask 形状(对齐 `descriptive_smoke.json` + ADR 0005 只验方法/结构)

```json
{
  "task_id": "<sha1(input+referenced_files)[:12]>",
  "input": "<turn.user_input,路径改写为 fixtures/<basename>>",
  "dataset_fixture": "fixtures/<basename>",
  "assertions": {
    "no_error_results": true,
    "min_tool_calls": 1,
    "tool_call_count_max": "max(2, ceil(源 tool_count × 1.5)),硬上限 20"
  }
}
```

- **绝不**固化数值断言(留存率==12% 之类会随数据漂移腐烂——ADR 0005);
- `tool_call_count_max` 从源轨迹派生并留余量,既放过合理重跑、又抓失控循环;
- `task_id` 用内容哈希 → **重跑幂等**:同轨迹产出同 task_id,覆盖而非堆积;
- `input` 把原始路径改写成 `fixtures/<basename>`,与 `resolve_task_input` 的运行期改写语义一致。

### 6.4 fixture 冻结

- basename 在 data_search_paths 命中 → 拷进 `fixtures/`,已存在不覆盖(去重);
- 找不到 → 该任务跳过 + log(引用了文件却冻结不了,eval 必失败,不如不收);
- 任务总数上限 `_MAX_HARVESTED_TASKS = 50`(常量可调),超限 log(**不静默截断**)。

### 6.5 落盘位置 + evaluator 取数(否则 2B 是死写路径)

- 手写金标准留 `examples/eval_tasks/`(随仓库);
- **收割产出落 `~/.daa/eval_tasks/`(+ `fixtures/`)**——含真实数据集名,不污染仓库;
- `config` 加 `eval_tasks_dir()`(镜像 `trajectories_dir()`,走 `_evolution_subdir`);
- evaluator 现读单目录(`SkillEvaluator.__init__(eval_tasks_dir)`),**小幅拓宽**为 `str | Path | list`,内部拼接 examples + daa 两源。**这属于 2B**(让产出可达),不是 2C。

### 6.6 CLI

仿 `register_evaluate_cli`,加 `harvest-eval` 子命令:`--data-search-path` 可重复;用 config 定位 trajectories / eval_tasks 目录。

---

## 7. 2C defer 理由(分阶段,非逃避 — §2.8 vs §2.10)

2C = 把 evaluator 接进 `scripts/quality_gate.py`。defer 四条硬理由:

1. **成本/速度不对**:eval 经 `make_agent_run_fn` 跑完整生产 agent → 真实 LLM 调用 × N 任务 × 2 臂。现 gate 全是本地项;塞进每次提交 = 慢且烧 token。
2. **冷启动空转**:现在 0 candidate、1 eval task。此刻接 gate = 永远 no-op 的步骤,早了。
3. **职责/节奏不同**:gate 守代码正确性(提交级、秒级);eval 守技能质量(天/周级),属离线进化管线(已是 CLI `daa evolve evaluate`),不该挤进每次提交。
4. **Phase 2 验收不依赖 2C**(见 §10)。

**已穷举的替代**(避免"逃避"误判):A)进每次提交 gate ← 被 1/2 否;B)独立 `scripts/eval_gate.py` 走发布/夜间节奏 ← **这就是 2C 落地形态**;C)注入 mock-client 免 API 跑 eval ← 但 eval 的意义就是跑真 agent,mock 等于自欺,否。

→ 2C = 形态 B,等"有 candidate + 有 ≥5 相关任务 + 有 API 预算"三条件齐了再做。

---

## 8. 错误与降级链(鲁棒性)

| 触发                                | 行为                                                           |
| ----------------------------------- | -------------------------------------------------------------- |
| `enable_trajectory_inputs=False`    | `input_digest=""`、`referenced_files=()`,其余照常(降级不崩)    |
| ToolUseEvent 缺 parameters(空 dict) | `input_digest="{}"`、`referenced_files=()`,正常记录            |
| 脱敏/截断异常                       | 包裹 try/except,回退到截断的原始 JSON,不丢整条 turn            |
| harvester 找不到 referenced 文件    | 跳过该任务 + log,继续其余                                      |
| harvester basename 同名歧义         | 取首个 + log                                                   |
| EvalTask 写盘/fixture 拷贝失败      | 跳过该任务 + log,不崩整批                                      |
| evaluator 读双目录,其一不存在       | 当作空集,不报错(沿用 `load_eval_tasks` 既有 `not exists → []`) |

---

## 9. 测试(TDD,须过质量闸)

`~/.daa/trajectories/` 当前为空,全部用合成 fixture,严格对齐 TurnRecord schema。

**2A**

- 带 parameters 的 ToolUseEvent → ToolResultEvent 后:`ToolCallRecord.input_digest` 非空、绝对路径已脱敏为 `<path:basename>`、`referenced_files` 含目标 basename;
- 超长参数截断到 `_INPUT_DIGEST_CHARS` 且带尾缀;
- `enable_trajectory_inputs=False` 时两字段为空、其余字段正常;
- 旧格式 jsonl(无新字段)读回不报错、`.get` 默认正确。

**2B**

- 合成若干带 `input_digest`+`referenced_files` 的 turn jsonl + 一个真实小 csv 在 search path;
- 产出 EvalTask 形状正确、`task_id` 稳定、fixture 已拷、input 改写成 `fixtures/<basename>`;
- **断言全是方法/结构、无任何数值**(ADR 0005 守护测试);
- 幂等:重跑同 corpus → 同文件、无重复;
- 缺文件:referenced basename 不在 search path → 任务跳过 + log、不崩;
- evaluator 双目录:examples + daa 两源任务都被 `relevant_tasks` 命中。

---

## 10. 验收标准(集中优势兵力)

**主验收(必须)**:2A 捕获的(合成)轨迹 → synthesizer 产 1 个 candidate → harvester 产 ≥5 相关 EvalTask → evaluate 跑 → `decision ∈ {promote, retire}`,**不退回 needs_review**。这条通了,自进化回路即闭环。

**次验收(不能破)**:

- 质量闸 `scripts/quality_gate.py` 全绿(ruff + format + mypy + pytest + drift);
- 现有测试行为不变;
- ToolCallRecord 加字段向后兼容,旧轨迹读回无错;
- 独立代码审查闭环(CLAUDE.md §2.9)零遗留。

---

## 11. 风险与对策

| 风险                                       | 对策                                                                                   |
| ------------------------------------------ | -------------------------------------------------------------------------------------- |
| 无真实轨迹,验证只能合成                    | 合成 fixture 严格对齐 TurnRecord schema;真轨迹出现后重跑 harvester 复核;已知限制如实记 |
| basename 同名歧义 → 冻错 fixture           | 取首个 + log;日后上 content-hash(已记可选加固)                                         |
| synthesizer 反思质量(LLM 未必用好真实代码) | 不在 Phase 2 范围:只保证数据到位,prompt 调参是另一个旋钮                               |
| 隐私面扩大(多记 input)                     | 2A 脱敏 + 开关 + 默认评估                                                              |
| fixture 存储膨胀                           | basename 去重不重拷;任务数上限 + log                                                   |

---

## 12. 非目标(YAGNI,留后续)

- 2C(eval 进 gate / 独立 eval_gate)— §7;
- L3 策略层 — eval 基线出现前不做;
- 真实数据验证 — 无轨迹;
- content-hash fixture 消歧 — §6.2;
- synthesizer 的 LLM prompt 调参 — §5.5;
- 轨迹加密 / 哈希存储 — §5.3 已定为比例得当不做。

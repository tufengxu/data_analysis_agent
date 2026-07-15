# 0005 — 技能 eval:验证方法非数值 + 最小样本门槛降级人审

- 状态: Accepted (2026-06-14)

## 背景

阶段二让 synthesizer 从轨迹半自动合成 candidate 技能,需要一道护栏判定 candidate
能否晋升为 active。数据分析有个独有杠杆:技能是**可执行的分析手法**,能在冻结数据集上
**真跑一遍**验证,比通用 LLM-judge 客观。但完整 A/B promote/rollback 有统计学陷阱:
冷启动期 candidate 1-2 个、相关 eval 任务 10-20 个,自动晋升的决策是**噪声主导**的。

## 决策

建完整 eval 闭环(`evolution/evaluator.py`:fixture 重跑 + 对照/实验组 A/B +
promote/rollback),但加两条数据分析特化约束:

1. **断言验证「方法/结构」而非「具体数值」** — 数据会变,断言 `留存率==12%` 会随数据漂移
   失效;改为断言「无报错 / 调了正确工具 / 产出了同期群矩阵或图表」。fixture 数据集**冻结**
   保证可复现。
2. **最小样本门槛(`MIN_SAMPLES`)** — 命中某 candidate 的相关 eval 任务数 `< MIN_SAMPLES`
   时,**不自动 promote**,降级为「needs_review」人审清单(保持 candidate 状态);达标且
   "通过率不降 ∧ 工具成本不增"才自动 promote,否则 retire。

## 理由

把数据分析"可重算验证"的优势用起来(冒烟重跑),同时承认"完整闭环建成"≠"自动决策可信"——
二者的 gap 由数据规模决定。样本门槛让闭环既建成、又不在冷启动期被噪声自欺。这与 ADR 0004 的
"记结构不记数值"红线一脉相承:数据会变,所以记忆和 eval 都不能锚定具体数值。

## 影响

新增 `evolution/evaluator.py` + `examples/eval_tasks/`(JSON 任务 + 冻结 fixture);
核心逻辑(check_assertions/decide_promotion/relevant_tasks)纯函数可测,真跑经注入式
`run_fn`(默认装配轻量 stateless agent)。CLI:`python -m data_analysis_agent.evolution
evaluate`。未来可选:为 `quality_gate.py` 增设 eval 阶段,使行为回归与代码回归同闸(当前未接入)。

## 补充 — 冻结 fixture 数值锚(Addendum, 2026-07-15)

「断言验证方法非数值」的红线针对**会漂移的活数据**:活数据下 `留存率==12%` 会随数据变动
失效,故禁。但一项 eval 任务若带 `dataset_fixture`(冻结数据),该数据**不漂移**,在其上
锚定一个计算值就不是「钉死易腐的数」,而是「对一份永不改变的输入校验确定性结果」——这是
红线的**意图合规**(intent-compliant)例外,不是放弃红线。

落地(design: `docs/superpowers/specs/2026-07-15-eval-numeric-anchors-design.md`):

- `EvalRun.computed_outputs`:捕获本次运行中 `python_analysis` 工具结果文本(原始素材,
  本身不构成任何断言)。
- `numeric_anchor` 断言:从 `computed_outputs` 解析数值,要求至少一个落在
  `abs(value) * tolerance` 窗口内(value≈0 用绝对地板兜底)。确定性浮点比较,**无 LLM judge**。
- `eval_gate.validate_task` 强制:`numeric_anchor` **仅**允许出现在带 `dataset_fixture` 的任务上;
  无 fixture 的数值锚 = gate FAIL。值级守卫 `_NUMERIC_PIN_RE` **不**扫描 `numeric_anchor`
  (锚的 `value` 本就该是数)。

红线依然覆盖所有 fixture-less 任务;该例外只闭合「跑通 + 调对工具 + 产出报告,但算错了一个数」
这个此前 eval 无法检测的盲区(TR-2 / G3–G5 correctness)。

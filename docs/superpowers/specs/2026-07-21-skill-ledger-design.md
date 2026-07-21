# 2026-07-21 Skill Promotion Regression Ledger (Wave 3, Slice 1)

> P1-6.5 回归账本。审计 G1-1(人审门)已在代码层落地(evaluate 只写 proposed_promote;
> approve/retire 是唯一写 active 的路径,且已测)。但 approve/retire **不留任何证据** ——
> 没有晋升/退役记录、时间、执行人、eval 分数。本 slice 补回归账本。

## Intent

每次技能状态流转(evaluate 提议、approve 激活、retire 退役)追加一条 append-only 账本记录,
可查、可审计,支撑"至少一个技能能从轨迹→eval→人审→active 追溯到证据"(roadmap P1-6 退出标准)。

## Ground truth(已核实)

- `evolution/evaluator.py`:`SkillEvaluator.apply` 写 proposed_promote/retired(设 skill.eval_score);
  `approve_skill`/`retire_skill` 翻 status(idempotent,已测于 test_evaluator.py:317)。三处都**不记账本**。
- 代码里的 "ledger" 全指 **tool ledger**(agent_loop 消息配对),非技能晋升账本 —— 无冲突。
- 账本自然位置:`<skills_dir>/ledger.jsonl`(`~/.daa/skills/ledger.jsonl`),与技能文件同根。
- `evolution/__main__.py`:`register_evaluate_cli` 注册 evaluate/approve/retire;无 ledger 子命令。

## 设计决策

1. **append-only JSONL**(`<skills_dir>/ledger.jsonl`),镜像 web feedback.jsonl 的简单追加模式;
   不复用 JsonlStore(它是 read/rewrite 语义,账本只追加)。
2. **三条记录源**:
   - `evaluator.apply` decision=promote → action `proposed_promote`(带 metrics);decision=retire → `eval_retire`。
   - `approve_skill` → action `approve`(from_status→active)。
   - `retire_skill` → action `retire`(from_status→retired)。
   - **只在真实状态变更时记**(idempotent no-op 不记)。
3. **字段**:`skill, action, from_status, to_status, eval_score, metrics, decided_at(UTC ISO), actor(env USER 或 "cli")`。
4. **新子命令** `evolution ledger [name]`:打印账本(可选按技能名过滤),newest-last(追加序)。
5. **`_append_skill_ledger` helper** 放 evaluator.py,三处共用。

## 文件范围

- `evolution/evaluator.py`:`_append_skill_ledger` + `_utc_now_iso` helper;`apply`/`approve_skill`/`retire_skill` 调用。
- `evolution/__main__.py`:`register_evaluate_cli` 注册 `ledger` 子命令;`cmd_ledger`。
- 新测试 `tests/test_skill_ledger.py`(或并入 test_evaluator.py)。

## 验收

- approve 一次 candidate→active:账本多一条 action=approve,from=candidate,to=active。
- retire、evaluate-proposed_promote 同理各记一条;no-op(已 active 再 approve)不记。
- 账本 append-only:多次流转累积,不覆盖。
- `evolution ledger` 打印全部;`evolution ledger <name>` 过滤。
- 既有 approve_skill/retire_skill 测试仍绿(行为不变,只是多写一行)。
- 质量门绿;独立审查零 blocking/major。

## 显式不在本 slice

G1-2 `make_agent_run_fn` 集成测试(Slice 2);G1-3 跑通一次**真实**晋升(运维性,需真实轨迹,落地后另记)。

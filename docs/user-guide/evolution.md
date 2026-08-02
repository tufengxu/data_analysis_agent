# Evolution 工作流（自进化）

DataAnalysisAgent 能从真实使用里**离线**学习：把反复出现、但现有技能覆盖不到的任务，
蒸馏成新的候选技能；候选必须在冻结 fixture 上重跑评估、并**经人工批准**后才激活。
整条管线**永远不在 live 循环里跑**——它是离线 sink，不影响在线行为。

> **Phase 1 治理红线：无自动晋升。** 技能激活的唯一入口是人工 `approve`。评估通过只会把
> 技能标为 `proposed_promote`（待审），不会自己上线。

---

## 技能生命周期

```
candidate            # 由轨迹合成,未经验证;只在显式请求时加载,不进活注册表
  │  evaluate(评估通过)
  ▼
proposed_promote     # 已过 eval,等待人工批准;仍不进活注册表
  │  approve(人工批准)—— 唯一激活入口
  ▼
active               # 进活注册表,可被路由命中
  │  retire
  ▼
retired              # 退役,移出活注册表,保留审计
```

每次 `proposed_promote` / `approve` / `retire` 都会向**只增（append-only）晋升账本**
追加一条记录，可随时审计。

---

## CLI

统一入口：`python -m data_analysis_agent.evolution <子命令>`

| 子命令           | 作用                                                                      |
| ---------------- | ------------------------------------------------------------------------- |
| `synthesize`     | 轨迹 → 聚类 → 反思 → **candidate 技能**（过拟合防护）                     |
| `mine-memory`    | 轨迹 → L1 领域记忆（口径/偏好/隐患），metric 先写「未确认」               |
| `harvest-eval`   | 轨迹 → eval 任务 JSON + 冻结 fixture（解决评估冷启动）                    |
| `list`           | 列出 active / proposed_promote / candidate 技能                           |
| `evaluate`       | 在冻结 fixture 上重跑候选 → 通过则标 `proposed_promote`（**不自动激活**） |
| `approve <name>` | **人工批准** `proposed_promote`/`candidate` → `active`                    |
| `retire <name>`  | 退役技能 → `retired`（移出活注册表）                                      |
| `ledger [name]`  | 查看晋升/退役账本（append-only）                                          |

---

## 典型流程

```bash
# 1. 跑过一些真实分析后(产生了轨迹),离线蒸馏候选技能
python -m data_analysis_agent.evolution synthesize

# 2. 看看合成了什么
python -m data_analysis_agent.evolution list

# 3. 在冻结 fixture 上评估某个候选(通过 → proposed_promote)
python -m data_analysis_agent.evolution evaluate

# 4. 人工审查后批准(唯一激活入口)
python -m data_analysis_agent.evolution approve <技能名>

# 5. 审计晋升历史
python -m data_analysis_agent.evolution ledger
```

---

## 防过拟合 / 防退化

- **评估验证方法/结构，不钉数值**：fixture 重跑核的是"会做对这类任务"，不是背答案
  （ADR 0005 允许在冻结 fixture 上用数值锚做正确性抽查）。
- **最小样本门槛**：样本不足时不自动晋升，回退到人工审查。
- **A/B + rollback**：晋升决策可回滚；账本全程留痕。
- **记忆数值不背**：领域记忆记结构与口径，刻意不记数值结论（ADR 0004），避免陈旧数值误导。

---

## 相关

[workspace.md](workspace.md)（数据存哪） · [local-safe.md](local-safe.md) ·
[troubleshooting.md](troubleshooting.md)

# DataAnalysisAgent v0.1.0-rc1 — Phase 1 Release Candidate

日期：2026-08-02 · 基线：`main`（PR #45 之后）

这是 Phase 1 的 Release Candidate 说明。它**诚实**记录：本次 RC 包含什么、威胁模型边界
在哪、以及**哪些 §8 完成项仍有 Gap**。RC 不等于"全部完成"——它是"已达标的清楚、
未达标的也清楚"的可核验快照。

---

## 1. 这个 RC 是什么

一个**单用户本地数据分析工作台**：命令行 + 浏览器 Web Workbench，跑真实分析项目，
产物/会话/run 清单落在本地 project 工作区，并能离线自进化（受人工审批门约束）。

适用：你在本机用它分析自己的 CSV/Excel，看 live 进度、审批工具调用、预览报告、
跨会话续接 project、让它从使用中沉淀可复用技能。

不适用（Phase 2 范围）：多用户、认证、分布式执行、共享租户记忆、生产 SLO。

---

## 2. 威胁模型（诚实陈述，必读）

**`python_analysis` 的沙箱是 best-effort 容器，不是安全边界。**

它要兜住的是「**模型写出蠢代码、误伤你自己的文件**」，**不是**「抵御恶意租户 / 主动
逃逸」。完整论证见 [ADR 0008](adr/0008-sandbox-best-effort-not-security-boundary.md)，
要点：

- **已知残留洞**（刻意不逐个封堵，避免虚假安全感）：
  - 计算路径读取（`pd.read_csv(变量)` 的非字面量参数不经路径白名单）；
  - 标准库「按路径读文件」的开放类（`linecache`/`tarfile`/`shelve`/`__loader__` 等，
    无法穷举）；
  - 反射/内省 → ACE 的开放类（已封 `operator`/`inspect` 廉价入口 + 统一拦帧属性 sink，
    但 `gc`/`types`/`functools` 同类不可穷举）；
  - 网络/DB 客户端库（`sqlalchemy`/`paramiko`/`boto3` 等，默认 venv 未装；装了的话模型
    可借其外发数据）；
  - 字面相对路径 `../` 穿越可滑过绝对路径白名单（blast radius 低：沙箱 cwd 在
    `$TMPDIR` 深处）。
- **缓解有效的前提下**：这些是「单用户本地、读的是你自己本就可读的文件」的场景，
  blast radius = 你自己的进程权限。**一旦威胁模型变为「需要抵御主动逃逸」，正确做法是
  换容器化 / 安全执行运行时（网络命名空间），而不是继续扩黑名单。**

**其它边界，同样诚实：**

- **Web Workbench 无认证层。** 默认只绑 127.0.0.1；`--unsafe` 可绑 LAN，但任何能到达
  端口的人都能驱动它（跑 run、审批、上传）。**公网暴露不要用 `--unsafe`。**
- **sensitive 模式是「不捕获」不是「净化」。** 它阻止用户输入落盘 + 停记忆写入，但
  **不**对 `python_analysis` 的计算输出做 PII 擦除（输出可能回显输入）。主动 output
  redaction 是后续项。处理高度敏感数据时请把这点纳入你的威胁模型。
- **权限引擎 fail-closed，但只覆盖工具调用层**，不替代 OS 级隔离。

---

## 3. §8 完成清单核对（22 项，逐项给证据）

核对基准 `main`，每项附代码/测试/PR 证据。**✅ 13 项无 Gap，⚠️ 6 项部分，❌ 1 项，修复 1 项，绑定 1 项。**

| #   | 项                                           | 状态              | 证据 / Gap                                                                                                                                                                                                                                       |
| --- | -------------------------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | local_safe 为 Web 默认                       | **✅ 本 RC 修复** | 此前 `create_app` 用 `from_env()`（preset="" → 无引擎全放行），是文档/代码矛盾的真 bug；本 RC 起 `_default_workbench_config()` 钉 `local_safe`（`server/app.py`），测试锁定（`test_default_config_is_local_safe` / `test_supplied_config_is_respected`） |
| 2   | 工具/artifacts/result store/uploads 路径限定 | ✅                | `file_read.py`/`data_profile.py`/`python_exec.py`（AST 仅直接 `open`）/`artifacts.py`（名称消毒+Web bare-name 守卫）/`result_store.py`（固定目录）/`server/app.py`（upload 白名单+扩展名+200MB）                                                         |
| 3   | Web 仅 localhost 默认                        | ✅                | `server/bind.py` fail-closed（PR #44），非 loopback 无 `--unsafe` 拒启；27 用例（7 测试函数）含 11 个伪装主机输入防回归                                                                                                                                  |
| 4   | 上传/选择 CSV + Excel                        | ✅                | `_UPLOAD_EXTS={.csv,.xlsx,.xls,.parquet}`；Excel header-health（PR #21）                                                                                                                                                                                 |
| 5   | 授权目录需显式确认                           | ⚠️                | 授权 fail-closed（空路径报错）+ UI 显示已授权列表，但**无二次确认环节**；Web 不校验 `paths` 是否在 project manifest `authorized_paths` 内（浏览器填什么授什么）                                                                                          |
| 6   | 浏览器 live 进度                             | ✅                | `event_codec.py` + SSE 流式面板（PR #25/#32）                                                                                                                                                                                                            |
| 7   | 浏览器审批 ASK 工具                          | ✅                | `approval.py` + `/api/approval` + 超时=deny（PR #27）；修复 #1 后默认配置下真正生效                                                                                                                                                                      |
| 8   | 浏览器 artifact 列表 + 打开报告              | ✅                | `web/app.py` artifact 路由（CSP sandbox + traversal 守卫）                                                                                                                                                                                               |
| 9   | 浏览器反馈                                   | ✅                | `/api/feedback` + Good/Bad/Rephrase（PR #28/#40）。注：CSRF token 仅校验 `/api/run/stream` 与 `/api/approval`，feedback/upload 不校验（暴露面由 localhost-only + CSP sandbox 兜底）                                                                      |
| 10  | 每次 run 有 manifest                         | ⚠️                | CLI project run 写 `runs/<id>.json`（跨进程锁 PR #42）；**Gap：所有 Web run 不写 manifest、崩溃 run 不落、非 project run 无**                                                                                                                            |
| 11  | sensitive 抑制 telemetry/memory 写           | ⚠️                | memory 写入全停 + 输入不捕获（`runtime.py:296`）；**但结构性轨迹（工具名/时长/token）仍写**——只抑制"输入形"；output 净化未做                                                                                                                             |
| 12  | causal 四级 claim 分标                       | ✅                | `causal/model.py` `ClaimLevel` 封闭词表 + 确定性 `infer_claim_level`                                                                                                                                                                                     |
| 13  | A/B 读出 balance/lift/caveat/有界决策        | ✅                | `causal/experiment.py`：effect+CI、SRM、guardrails、`DecisionLevel` 封闭词表、`build_action_plan`；边界：balance 仅 SRM（无协变量均衡）                                                                                                                  |
| 14  | 观察性请求→hypothesis/readiness              | ✅                | `causal/qa.py` 6 态；观察性+显式假设→ASSUMPTION_READY 且不做效应数值声称                                                                                                                                                                                 |
| 15  | memory list/confirm/correct/forget/export    | ⚠️                | list/confirm/correct 有（correct=同键覆盖）；**Gap：forget ❌（全仓无 delete）、export ❌**；list/confirm 仅方法层无 CLI/UI 入口                                                                                                                         |
| 16  | 30-50 eval 任务                              | ✅                | 38 个（12 causal + 24 reports + 2 顶层）+ 冻结 fixture；CI eval gate（≥20 任务 ≥3 域）                                                                                                                                                                   |
| 17  | 候选技能须 eval+人工批准                     | ✅                | evaluate 只写 `proposed_promote`；`approve_skill` 是唯一 active 入口；注入标记拒写；G1 e2e（PR #41）                                                                                                                                                     |
| 18  | active 技能有 regression 账本                | ⚠️ 机制全/实例零  | 账本机制 + 11 测试完备；**运行时零 active 技能零账本条目**——即 Wave 3 G1 真实晋升未跑（见下）                                                                                                                                                            |
| 19  | 质量闸绿                                     | ✅                | CI `gate` conclusion=success（PR #45 head）；gate=ruff+format+mypy+pytest+drift+eval                                                                                                                                                                     |
| 20  | release notes 诚实陈述威胁模型               | ✅ 本文件         | 见 §2                                                                                                                                                                                                                                                    |

---

## 4. 已知 Gap（RC 如实登记，不粉饰）

按严重度：

1. **~~local_safe 未接线~~ → 本 RC 已修**（§3 #1）。
2. **#18 / Wave 3：G1 真实晋升未跑。** 自进化闭环**集成已验证**（PR #41 e2e，无集成 bug），
   但**真实 LLM / 真实轨迹**的一次晋升从未发生（`~/.daa/trajectories/` 空，阻塞于 API key
   配置）。这是 Phase 1 唯一剩下的"活"主目标。
3. **#10：Web run 不落 run manifest**（backlog 未列，本次核对新发现）；崩溃 run 不落
   （backlog 已列未做）。
4. **#15：memory 缺 forget / export**；list/confirm 无用户入口。
5. **#5：目录授权无显式确认环节**；Web 端授权不校验 project manifest。
6. **#11：sensitive 口径**——只抑制输入形捕获，结构性轨迹仍写；output redaction 未做
   （与 §2 威胁模型一致，是已知边界）。

## 5. 与 Milestone 1E 的对照（退出标准）

> "A single user can use the system as a durable local workbench for real analysis projects."

- **durable local workbench**：✅ project 工作区 + run manifest（CLI）+ 持久 kernel +
  跨会话记忆/技能。
- **docs**：✅ 本 RC 起有用户指南（`docs/user-guide/`：local-safe / web-workbench /
  workspace / evolution / troubleshooting）+ README 更新 + 本 release notes。
- **eval gate**：✅ CI 已接（structural，≥20 任务 ≥3 域）。
- **full review loop**：✅ 全程独立子 Agent 对抗审查（0 blocking/major）+ quality gate 绿。

**结论**：Phase 1 在「本地工作台」维度**已具备 RC 条件**；唯一未闭环的主目标是
**Wave 3 G1 真实晋升**（§4 #2）——它不阻塞"本地工作台可用"的判定，但阻塞"自进化真
闭环"的完整宣称。建议：本 RC 发布 → 配好 API key 后跑通一次 G1 真实晋升 → 再封
Phase 1 正式版。

---

## 6. 本 RC 的变更（相对上一状态）

- **修复**：Web Workbench 默认权限引擎接线（`local_safe`），此前默认启动无任何权限防护
  （§3 #1）。
- **文档**：新增 `docs/user-guide/`（5 篇）+ README 刷新（Tools/Skills/Architecture/
  User Guide 入口）+ 本 release notes。
- 无 API / 行为破坏：CLI 默认 `local_dev` 不变；显式传 config 的调用方行为不变。

## 7. 升级与回滚

- 升级：`git pull` 后 `pip install -e ".[data,dev,web]"`。
- 回滚本 RC 唯一行为变化（Web 默认 local_safe）：显式以 `--preset local_dev` 启动对应
  CLI，或在自定义 config 里设 `permission_preset`（见 §3 #1 的"显式 config 优先"）。

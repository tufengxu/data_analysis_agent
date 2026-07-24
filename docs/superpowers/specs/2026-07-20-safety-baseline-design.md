# 2026-07-20 Safety Baseline — Permission Presets + Sensitive Mode (Wave 1, Slice 2a)

> P1-1 安全脊梁的第一刀。给 agent 一个 deny-by-default 的权限姿态(`local_safe`)
> 和一个隐私抑制开关(`--sensitive`,关轨迹输入捕获 + 记忆写入)。doctor 命令与
> ~/.daa 全目录磁盘上限推到 Slice 2b。

## Intent

让本地产品态默认安全:`local_safe` 预设(read-only 允许、变更工具 ASK、未知工具 DENY),
以及 sensitive-mode(强制 `enable_trajectory_inputs=False` + `enable_memory=False`,
即"只记工具名/时长、不记输入、不写记忆"的隐私态)。这两项是 Web workbench(Wave 2)
将要默认启用的安全基线。

## Ground truth(已核实)

- `security/permissions.py` `PermissionEngine.check`:规则序 deny → ask → allow,
  **兜底硬编码 ASK**(line 114)。当前无法表达"未知工具 = DENY"。
- `runtime.build_permission_engine`:`permission_mode`(default|plan|auto|bypass)
  - `deny_patterns` → engine;`default` 且无 deny → `None`(无引擎,全允许,CLI 友好)。
- `READ_ONLY_TOOLS` 已在 runtime.py 定义。
- `AgentConfig` 已有 `enable_memory` / `enable_telemetry` / `enable_trajectory_inputs`;
  `enable_trajectory_inputs=False` 的语义本就是"只记工具名/时长/result_chars"(隐私态)。

## 设计决策

1. **引擎加 `default_behavior`**:`PermissionEngine.__init__` 增
   `default_behavior: PermissionBehavior = ASK`;`check()` 兜底返回它(替换硬编码 ASK)。
   向后兼容(既有引擎默认 ASK,行为不变)。
2. **config 增两个字段**:`permission_preset: str = ""`("" / `local_safe` / `local_dev`)
   与 `sensitive_mode: bool = False`。
3. **build_permission_engine 读 preset**:
   - `local_safe` → `PermissionEngine(default_behavior=DENY)` + allow=READ_ONLY_TOOLS
     - ask=(python_analysis/visualization/html_report/chart_render)。未知 → DENY。
   - `local_dev` → `None`(显式命名今天的 CLI 友好全允许)。
   - 无 preset → 既有逻辑(permission_mode + deny_patterns)。
4. **from_config 应用 sensitive**:开头 `if config.sensitive_mode:
config = replace(config, enable_memory=False, enable_trajectory_inputs=False)`。
   下游 `_build_memory_injector` 与 trajectory logger 自动看到抑制后的值;telemetry 仍开
   但 input-less(已是其隐私语义)。
5. **CLI 旗标**:`--preset {local_safe,local_dev}`、`--sensitive`;project.json 已有
   `preset` 字段(Workspace Slice 1),init 时记录。

## 文件范围

- `security/permissions.py`:引擎加 `default_behavior` + check() 兜底用它。
- `config.py`:加 `permission_preset` + `sensitive_mode` 字段。
- `runtime.py`:`build_permission_engine` 读 preset;`from_config` 开头应用 sensitive。
- `__main__.py`:`--preset` / `--sensitive` 旗标。
- 新测试 `tests/test_safety_baseline.py`;既有 `tests/test_permissions*.py` 跑回归。

## 验收

- `local_safe`:read-only 工具 ALLOW;python_analysis/visualization/html_report/chart_render
  ASK;未知工具名 DENY。既有 default-mode 行为不变。
- `--sensitive`:该 run 的 `runtime.memory_injector is None`,且 trajectory 不记 input。
- 无 preset 且无 `--sensitive`:逐字节等同今天。
- `scripts/quality_gate.py` 绿;独立审查零 must-fix。

## 验证命令

```
.venv/bin/python scripts/quality_gate.py
.venv/bin/pytest tests/test_safety_baseline.py tests/test_permissions.py -v
```

## 显式不在本 slice(Slice 2b)

`data-agent doctor` 健康检查;~/.daa 全目录磁盘上限(memory/profiles/skills);
对已捕获内容的主动 PII scrubbing(sensitive-mode 目前是"不捕获"而非"净化")。

# context-compression 规范增量(context-compression-upgrade)

## ADDED Requirements

### Requirement: collapse 桩携带可回取 digest

会话级 ContextCollapse 以占位符替换旧 tool_result 时,应尽力从被压内容提取摘要元信息(行数/列数/采样方法)与召回句柄中的 result_id,生成形如 `[collapsed: table N rows × M cols · retrieve_result(result_id="…")]` 的桩;提取逻辑 SHALL 由能力层(capabilities/sampling)提供的助手实现,会话层模块仅调用;任一提取失败应回退既有占位文案 `[Earlier tool result collapsed]`(fail-closed)。

#### Scenario: 带召回句柄的结果被 collapse

- **WHEN** 被替换的 tool_result 内容含采样摘要头(`- rows=… · method=…`)与 recall 句柄
- **THEN** 桩文本应包含行数、列数与 result_id
- **AND** 不应包含完整摘要正文

#### Scenario: 无标记内容回退

- **WHEN** 被替换的 tool_result 为无任何采样标记的普通文本
- **THEN** 桩文本应为既有占位文案

### Requirement: compaction 保数据态

reactive auto-compact 的摘要输入应为被丢弃历史的头尾拼接(头部含早期 schema 结论,尾部含最近工具结果,中段以省略标记代替),而非仅取末尾;摘要 prompt 应为结构化 handoff 模板,固定包含任务目标、已读文件与 schema、kernel 变量清单、关键数值结论、未决事项与可回取 result_id 分节;数据态内容应由注入的 `data_state_provider` 提供,provider 缺失或失败时对应分节省略且不影响压缩流程;压缩成功后应追加一条 is_meta 的数据状态重注入消息(provider 失败时跳过)。

#### Scenario: 头部 schema 不被切掉

- **WHEN** 被丢弃段开头含数据 schema 结论、结尾含大工具结果且总长超过摘要输入上限
- **THEN** 送入摘要模型的内容应同时包含头部片段与尾部片段

#### Scenario: provider 失败降级

- **WHEN** data_state_provider 抛出异常或返回 None
- **THEN** 摘要照常生成且模板中数据态分节省略,不产生重注入消息

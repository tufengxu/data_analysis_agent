# CLAUDE.md — DataAnalysisAgent

项目通用架构、开发、测试与验收规则见同目录 `AGENTS.md`。以下规则专门约束 Claude Code
在本项目中使用 CodeGraph；它们不扩大文件写入、Hook、MCP、升级或发布权限。本节是
`AGENTS.md` 中同名章节的 Claude 入口镜像，修改任一处时必须同步维护另一处。

## CodeGraph 辅助检索

本项目已在 `.codegraph/` 建立本机索引。统一使用固定的受控入口，避免 GUI/IDE 会话与终端的
`PATH` 不一致：

```bash
cd "/Users/fengxutu/FENGXU TU/Projects/DataAnalysisAgent"
/Users/fengxutu/.local/bin/codegraph status .
# 仅在索引陈旧、任务确需最新图谱且允许刷新派生索引时执行
/Users/fengxutu/.local/bin/codegraph sync .
/Users/fengxutu/.local/bin/codegraph explore "<架构、调用链或影响范围问题>"
/Users/fengxutu/.local/bin/codegraph node "<符号名>"
/Users/fengxutu/.local/bin/codegraph callers "<符号名>"
/Users/fengxutu/.local/bin/codegraph callees "<符号名>"
/Users/fengxutu/.local/bin/codegraph impact "<符号名>"
```

- 架构梳理、跨文件调用链、符号定位和影响分析可先用 CodeGraph 缩小范围；简单的已知路径
  读取或精确文本搜索可直接使用原生文件工具和 `rg`。
- 开始结构性调查前先执行 `codegraph status .`。若索引陈旧，或其他 Agent/工作树修改过代码，
  且当前任务允许刷新本机派生索引，再运行 `codegraph sync .`。现有 Git Hooks 只在 commit、
  merge/pull、checkout 后自动同步，不覆盖所有未提交编辑。
- CodeGraph 是基于某一索引时点的静态派生结果，可能遗漏动态调用、反射和启发式无法解析的关系；
  它只提供辅助证据，不是当前源码的权威替代。
- 修改、缺陷根因、安全、权限、持久化、并发和数据完整性等高影响结论，必须使用当前源码、配置、
  测试、静态检查和必要的运行结果复核；发生冲突时以当前可复现证据为准。
- 不得因为 CodeGraph 返回了结果而禁止必要的源码读取、`rg` 或验证，也不为简单问题机械调用图谱。
- 未经用户明确要求，不执行 `codegraph init`、`index`、`uninit`、`install` 或真实升级；只允许用
  `codegraph upgrade --check` 检查新版本。

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for `tufengxu/data_analysis_agent`. See `docs/agents/issue-tracker.md`.

### Triage labels

The default five-label triage vocabulary is used unchanged. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repo with `CONTEXT.md` and `docs/adr/` at the root. See `docs/agents/domain.md`.

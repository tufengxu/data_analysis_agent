# DataAnalysisAgent — Context

Shared language for the agent's **context management** — how an unbounded
conversation and oversized tool outputs are kept within the model's finite
window. This is a glossary, not a spec; it pins what words mean, not how the
code works.

## Language

### Shrinking mechanisms (two parallel, non-overlapping subsystems)

**Context Compression（上下文压缩）**:
History-level reshaping of the whole conversation message list so it fits the
token budget. The five-level pipeline (Snip / Microcompact / Collapse /
Auto-Compact and the per-message budget cap) is _part of_ compression — those
are its internal level names, not separate concepts.
_Avoid_: calling it "compaction" (that word is overloaded — see Flagged
ambiguities); "sampling".

**Result Sampling（结果采样摘要）**:
Result-level lossy summarization of a **single** tool output _before_ it enters
the context. Operates on one result in isolation; unrelated to the history-level
pipeline.
_Avoid_: "compaction"; "compression". Never describe sampling as a step of the
compression pipeline — they are independent.

**Retrieval / CCR-lite（回取）**:
Fetching back the original, un-sampled content of a tool result that Result
Sampling shrank. The full original is held aside so the model can page through
it on demand.
_Avoid_: "cache lookup", "restore".

**Ledger Closure（账本闭合）**:
Repairing a message history so every assistant tool-use is answered by a
matching tool-result, synthesizing placeholders for any orphans. Keeps a
resumed or interrupted conversation valid for the Messages API.
_Avoid_: "history repair", "cleanup".

### Budgets

**Context Budget（上下文预算）**:
The token ceiling that triggers **Context Compression** and that defines
"context pressure". It governs the **message list only** — it deliberately
excludes the system prompt, tool schemas, and injected memory, none of which the
compression pipeline can touch. It is therefore _not_ the whole-window limit.
_Avoid_: reading it as "the model's context window" or "total prompt budget".

## Flagged ambiguities

- **"Compaction / 压缩" is overloaded in the code.** It appears both as the
  history-level pipeline (`context/compression.py`) and as the tool-result
  mechanism (`sampling/` docstring: "Sampling-based compaction"; the L5 class
  `AutoCompactStrategy`). **Resolution:** "compaction/压缩" refers _only_ to
  history-level **Context Compression**. The tool-result mechanism is **Result
  Sampling** and is never called compaction. (`AutoCompact` survives only as the
  internal name of compression level L5.)
- **"Budget / 预算" reads as whole-window but isn't.** The **Context Budget**
  governs only the message list; the system prompt, tool schemas, and injected
  memory ride outside it and are never shrunk by **Context Compression**.
  **Resolution:** treat the budget as a _messages-only_ ceiling. There is no
  single gate that sums system + tools + messages against the real window — the
  remaining headroom is left implicit, and the reactive 413 path is what catches
  an under-estimate.

## Example dialogue

> **Dev:** The 10-million-row query result blew up the context.
> **Expert:** It shouldn't — that's a single tool result, so **Result Sampling**
> catches it and replaces it with a summary before it ever lands in context.
> **Dev:** So that's the compression pipeline kicking in?
> **Expert:** No — keep them separate. **Context Compression** only reshapes the
> _conversation history_ once the whole thing exceeds budget. Result Sampling
> works on _one result_ at a time. Different trigger, different subsystem.
> **Dev:** And if the model actually needs the raw rows it sampled away?
> **Expert:** It calls **Retrieval** — the full original was held aside, it can
> page through it. And whatever survives, **Ledger Closure** guarantees every
> tool-use still has its matching result so a resume stays API-valid.

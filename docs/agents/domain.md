# Domain Docs

How the engineering skills should consume this repo’s domain documentation when
exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root.
- **`docs/adr/`** — read ADRs that touch the area being explored.

If either is absent, proceed silently. Domain-modeling workflows create or
extend these documents when terminology or decisions are actually resolved.

## File structure

This is a single-context repository:

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-sampling-exact-over-sketch.md
│   ├── ...
│   └── 0010-causal-decision-domain-layer.md
└── src/data_analysis_agent/
```

## Use the glossary’s vocabulary

When output names a domain concept—in an issue title, refactor proposal,
hypothesis, or test name—use the term defined in `CONTEXT.md`. Do not drift to
synonyms the glossary explicitly avoids.

If a needed concept is absent, reconsider whether the term belongs to the
project or record the gap for `/domain-modeling`.

## Flag ADR conflicts

If output contradicts an existing ADR, surface the conflict explicitly rather
than silently overriding it.

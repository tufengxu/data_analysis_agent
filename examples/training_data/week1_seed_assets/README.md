# Week 1 Seed Assets

This directory contains the first-week seed assets for training a small
tool-using data-analysis model that works with `DataAnalysisAgent`.

## Contents

- `data/`: 20 executable synthetic CSV datasets.
- `dataset_manifest.json`: schemas, domains, metrics, and quality notes.
- `seed_tasks.jsonl`: 100 business-analysis seed tasks, 5 per dataset.
- `scripts/generate_assets.py`: deterministic asset generator.
- `scripts/validate_assets.py`: lightweight validation checks.
- `scripts/smoke_tool_execution.py`: executes one dataset with real tools.

## Design Notes

- The seed unit is a business analysis task, not a plain Q&A prompt.
- Each task names expected DataAnalysisAgent tools and an oracle-style acceptance rule.
- `dataset_path` is relative to the `DataAnalysisAgent` project root. For
  `python_analysis`, resolve it to an allowed absolute path before emitting
  `pd.read_csv(...)` code because the tool executes from an isolated temp cwd.
- The CSVs intentionally include realistic analysis issues: missing values, outliers,
  skewed metrics, seasonality, flags, and segment effects.
- The assets are synthetic and contain no real personal data.

## Coverage

- Domains: education, finance, healthcare, hospitality, hr, insurance, manufacturing, marketing, operations, product, retail, risk, saas, sales, supply_chain, support.
- Task types: data quality profile, grouped KPI, time trend, anomaly/risk,
  and business recommendation with visualization.

## Regenerate

```bash
cd DataAnalysisAgent
python examples/training_data/week1_seed_assets/scripts/generate_assets.py
python examples/training_data/week1_seed_assets/scripts/validate_assets.py
python examples/training_data/week1_seed_assets/scripts/smoke_tool_execution.py
```

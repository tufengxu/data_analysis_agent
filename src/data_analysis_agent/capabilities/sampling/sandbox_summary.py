"""Exact DataFrame summarizer, designed to be INLINED into the python_exec sandbox.

Hard constraints (do not break):
    * No imports from ``data_analysis_agent`` — the subprocess runs with
      ``PYTHONPATH=""`` and cannot see this package. The harness inlines this
      file's source text into the wrapped script.
    * No ``from __future__`` import — inlining places this below a preamble, and
      ``__future__`` imports must be first in a file.
    * ``pandas`` / ``numpy`` are imported lazily inside functions so the module
      is safe to exec even when they are absent (the caller guards the call).

The output is a plain dict matching :meth:`TableSummary.to_dict`, so the harness
renders it with the single renderer in :mod:`render`.
"""

import math
from typing import Any


def summarize_dataframe(
    df: Any,
    *,
    max_sample_rows: int = 20,
    top_k: int = 10,
    quantiles: tuple[float, ...] = (0.01, 0.25, 0.5, 0.75, 0.99),
    stratify: str = "auto",
    include_outliers: bool = True,
    max_outlier_rows: int = 5,
    seed: int = 0,
) -> dict[str, Any]:
    """Compute an exact, sampled summary of a pandas DataFrame / Series."""
    import numpy as np
    import pandas as pd

    if isinstance(df, pd.Series):
        df = df.to_frame()

    n_rows, n_cols = int(df.shape[0]), int(df.shape[1])
    rng = np.random.default_rng(seed)

    columns: list[dict[str, Any]] = []
    numeric_names: list[Any] = []
    for name in df.columns:
        col = df[name]
        null_count = int(col.isna().sum())
        count = int(col.shape[0] - null_count)

        if pd.api.types.is_bool_dtype(col):
            kind = "bool"
            counts = col.dropna().value_counts()
            stats: dict[str, Any] = {
                "cardinality": int(col.nunique(dropna=True)),
                "top_k": [[bool(value), int(freq)] for value, freq in counts.head(top_k).items()],
            }
        elif pd.api.types.is_numeric_dtype(col):
            kind = "numeric"
            numeric_names.append(name)
            arr = col.dropna().to_numpy(dtype="float64")
            stats = _numeric_stats(arr, quantiles, np)
        elif pd.api.types.is_datetime64_any_dtype(col):
            kind = "datetime"
            non_null = col.dropna()
            stats = {
                "min": str(non_null.min()) if len(non_null) else None,
                "max": str(non_null.max()) if len(non_null) else None,
            }
        else:
            kind = "categorical"
            cardinality = int(col.nunique(dropna=True))
            counts = col.astype("object").dropna().value_counts()
            stats = {
                "cardinality": cardinality,
                "top_k": [
                    [_jsonable(value), int(freq)] for value, freq in counts.head(top_k).items()
                ],
                "tail_truncated": cardinality > top_k,
            }

        columns.append(
            {
                "name": str(name),
                "kind": kind,
                "count": count,
                "null_count": null_count,
                "stats": stats,
            }
        )

    sample_df, method = _sample_df(df, max_sample_rows, stratify, numeric_names, rng)
    sample_rows = _records(sample_df)
    outlier_rows = _outlier_rows(df, numeric_names, include_outliers, max_outlier_rows)

    notes: list[str] = []
    if n_rows > len(sample_rows):
        notes.append("列统计为全量精确计算;样本行为代表性子集。")

    return {
        "n_rows": n_rows,
        "n_cols": n_cols,
        "columns": columns,
        "sample_rows": sample_rows,
        "outlier_rows": outlier_rows,
        "sampling_method": method,
        "fidelity_level": "exact-stats",
        "notes": notes,
        "truncated": n_rows > len(sample_rows),
    }


def _numeric_stats(arr: Any, quantiles: tuple[float, ...], np: Any) -> dict[str, Any]:
    if arr.size == 0:
        return {}
    q_values = np.quantile(arr, list(quantiles))
    q_pairs = [[float(p), _round(float(v))] for p, v in zip(quantiles, q_values, strict=False)]
    q1, q3 = float(np.quantile(arr, 0.25)), float(np.quantile(arr, 0.75))
    iqr = q3 - q1
    n_outliers = int(((arr < q1 - 1.5 * iqr) | (arr > q3 + 1.5 * iqr)).sum())
    return {
        "min": _round(float(arr.min())),
        "max": _round(float(arr.max())),
        "mean": _round(float(arr.mean())),
        "std": _round(float(arr.std())),
        "quantiles": q_pairs,
        "n_outliers": n_outliers,
    }


def _sample_df(
    df: Any,
    k: int,
    stratify: str,
    numeric_names: list[Any],
    rng: Any,
) -> tuple[Any, str]:
    n = int(df.shape[0])
    k = min(k, n)
    if k <= 0:
        return df.iloc[:0], "none"

    strat_col = None
    if stratify == "auto":
        for name in df.columns:
            if name in numeric_names:
                continue
            cardinality = int(df[name].nunique(dropna=True))
            if 2 <= cardinality <= 10 and cardinality < n:
                strat_col = name
                break

    if strat_col is None:
        chosen = rng.choice(n, size=k, replace=False)
        return df.iloc[sorted(int(i) for i in chosen)], "reservoir"

    picks: list[Any] = []
    for _, group in df.groupby(strat_col, dropna=False, observed=True):
        share = min(max(1, round(k * group.shape[0] / n)), group.shape[0])
        local = rng.choice(group.shape[0], size=share, replace=False)
        picks.extend(group.index[sorted(int(i) for i in local)].tolist())
    sample = df.loc[picks]
    if sample.shape[0] > k:
        keep = rng.choice(sample.shape[0], size=k, replace=False)
        sample = sample.iloc[sorted(int(i) for i in keep)]
    return sample, f"stratified[{strat_col}]"


def _outlier_rows(
    df: Any,
    numeric_names: list[Any],
    include: bool,
    max_rows: int,
) -> list[dict[str, Any]]:
    if not include or not numeric_names:
        return []
    name = numeric_names[0]
    col = df[name].dropna()
    if col.empty:
        return []
    q1, q3 = float(col.quantile(0.25)), float(col.quantile(0.75))
    iqr = q3 - q1
    mask = (df[name] < q1 - 1.5 * iqr) | (df[name] > q3 + 1.5 * iqr)
    return _records(df[mask].head(max_rows))


def _records(frame: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        records.append({str(key): _jsonable(value) for key, value in row.items()})
    return records


def _round(value: float, sig: int = 6) -> Any:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return float(f"{value:.{sig}g}")


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    try:
        import numpy as np

        if isinstance(value, np.generic):
            scalar = value.item()
            if isinstance(scalar, float) and (math.isnan(scalar) or math.isinf(scalar)):
                return None
            return scalar
    except Exception:
        pass
    return str(value)

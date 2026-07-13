# MVP Arrow/cuDF Baselines Report

Date: 2026-07-13
Worktree: `/home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation`
Branch: `codex/tpch-q5-arrow-implementation`

## Scope

Implemented the exact PyArrow/cuDF baseline MVP in the owned files:

- `baselines/common.py`
- `baselines/python_q5.py`
- `baselines/arrow_q5.py`
- `baselines/duckdb_q5.py`
- `baselines/cudf_q5.py`
- `scripts/run_benchmarks.py`
- `tests/python/test_baseline_exact_mvp.py`

## Summary

- Switched the shared Python result contract from `revenue_cents` to raw
  `revenue_1e4`, including exact hashing and half-away-from-zero display at
  two decimals.
- Updated the dependency-free Python reference and DuckDB baseline to use the
  exact scale-1e4 formula `price_cents * (100 - discount_hundredths)`.
- Replaced the PyArrow baseline's `.tbl` parsing path with canonical Arrow IPC
  loading through `baselines/arrow_dataset.py`, with PyArrow filter/join/
  group-by/sort operators over exact integer columns derived from Decimal128.
- Replaced the cuDF baseline's `.tbl` parsing path with the same canonical
  Arrow IPC loader, converting Arrow tables into cuDF tables before running
  merge/filter/groupby/sort. Exact integer columns are derived in Arrow first
  so the result contract does not depend on cuDF Decimal128 behavior.
- Added focused pytest coverage for the raw result contract, hash sensitivity,
  Python/Arrow tiny parity, and benchmark routing/warmup behavior.
- Extended `scripts/run_benchmarks.py` with `--arrow-dataset`, Arrow/cuDF input
  rejection when it is absent, and `--warmup N` runs that execute but are not
  written to the CSV output.

## RED

The focused test was added first and run before production edits.

### RED command

```bash
conda run -p /tmp/memq5-arrow-cpu-task1 pytest -q tests/python/test_baseline_exact_mvp.py
```

### RED output

```text
FFF                                                                      [100%]
=================================== FAILURES ===================================
__ test_result_contract_uses_raw_revenue_1e4_display_rounding_and_exact_hash ___
E       AssertionError: assert 'nation,revenue_cents,revenue' == 'nation,revenue_1e4,revenue'

_____________ test_python_and_arrow_match_tiny_exact_rows_and_hash _____________
E   FileNotFoundError: [Errno 2] Failed to open local file '/tmp/.../tiny-arrow/region.tbl'

_____ test_run_benchmarks_requires_arrow_dataset_and_skips_warmups_in_csv ______
E       AssertionError: assert 'ok' == 'error'

3 failed in 0.64s
```

Observed RED matched the intended missing behaviors:

- shared result contract still exposed `revenue_cents`
- PyArrow baseline still tried to read `.tbl`
- benchmark runner still let Arrow use `--data-dir`

## GREEN

### Focused pytest

```bash
conda run -p /tmp/memq5-arrow-cpu-task1 pytest -q tests/python/test_baseline_exact_mvp.py
```

```text
...                                                                      [100%]
3 passed in 1.39s
```

### Tiny Arrow conversion

```bash
conda run -p /tmp/memq5-arrow-cpu-task1 python scripts/prepare_arrow_dataset.py \
  --input tests/fixtures/tpch_q5_tiny \
  --output /tmp/memq5-tiny-arrow-baselines \
  --scale-factor tiny \
  --batch-rows 2 \
  --source-command fixture \
  --replace
```

Result: exit 0.

### Tiny Python CLI

```bash
conda run -p /tmp/memq5-arrow-cpu-task1 python baselines/python_q5.py \
  --data-dir tests/fixtures/tpch_q5_tiny \
  --region ASIA \
  --date 1994-01-01 \
  --format json
```

```json
{
  "result_hash": "248d10b6ee352953",
  "rows": [
    {
      "nation": "JAPAN",
      "revenue_1e4": 1900000,
      "revenue": "190.00"
    },
    {
      "nation": "INDIA",
      "revenue_1e4": 900000,
      "revenue": "90.00"
    }
  ]
}
```

### Tiny Arrow CLI

```bash
conda run -p /tmp/memq5-arrow-cpu-task1 python baselines/arrow_q5.py \
  --dataset /tmp/memq5-tiny-arrow-baselines \
  --region ASIA \
  --date 1994-01-01 \
  --format json
```

```json
{
  "result_hash": "248d10b6ee352953",
  "rows": [
    {
      "nation": "JAPAN",
      "revenue_1e4": 1900000,
      "revenue": "190.00"
    },
    {
      "nation": "INDIA",
      "revenue_1e4": 900000,
      "revenue": "90.00"
    }
  ]
}
```

### Tiny hash equality

```bash
conda run -p /tmp/memq5-arrow-cpu-task1 python -c "from pathlib import Path; import sys; sys.path.insert(0,'/home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/baselines'); from python_q5 import run_q5 as rp; from arrow_q5 import run_q5 as ra; from common import result_hash; p=rp(Path('/home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/tests/fixtures/tpch_q5_tiny'),'ASIA','1994-01-01'); a=ra(Path('/tmp/memq5-tiny-arrow-baselines'),'ASIA','1994-01-01'); print('python_rows', [(r.nation, r.revenue_1e4) for r in p]); print('arrow_rows', [(r.nation, r.revenue_1e4) for r in a]); print('python_hash', result_hash(p)); print('arrow_hash', result_hash(a)); print('hash_equal', result_hash(p) == result_hash(a))"
```

```text
python_rows [('JAPAN', 1900000), ('INDIA', 900000)]
arrow_rows [('JAPAN', 1900000), ('INDIA', 900000)]
python_hash 248d10b6ee352953
arrow_hash 248d10b6ee352953
hash_equal True
```

### Benchmark runner verification

Arrow benchmark with warmup:

```bash
conda run -p /tmp/memq5-arrow-cpu-task1 python scripts/run_benchmarks.py \
  --project-root /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation \
  --engines arrow \
  --arrow-dataset /tmp/memq5-tiny-arrow-baselines \
  --warmup 1 \
  --repeat 1 \
  --output /tmp/memq5-arrow-baseline-bench.csv
```

```text
wrote 1 rows to /tmp/memq5-arrow-baseline-bench.csv
```

Observed CSV contents:

```text
bench_rows 1 [{'run_id': '0', 'status': 'ok', 'engine': 'arrow', 'region': 'ASIA', 'date': '1994-01-01', 'threads': '1', 'result_rows': '2', 'result_hash': '248d10b6ee352953', 'build_ms': '0', 'h2d_ms': '0', 'kernel_ms': '0', 'd2h_ms': '0', 'scan_ms': '167.495405', 'total_ms': '167.495405', 'elapsed_ms': '336.762926', 'error': ''}]
```

Arrow benchmark without `--arrow-dataset`:

```bash
conda run -p /tmp/memq5-arrow-cpu-task1 python scripts/run_benchmarks.py \
  --project-root /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation \
  --engines arrow \
  --output /tmp/memq5-arrow-baseline-missing.csv
```

```text
wrote 1 rows to /tmp/memq5-arrow-baseline-missing.csv
1 runs failed or were unavailable
- arrow: --arrow-dataset is required for arrow and cudf engines
```

Observed CSV contents:

```text
missing_rows 1 [{'run_id': '0', 'status': 'error', 'engine': 'arrow', 'region': 'ASIA', 'date': '1994-01-01', 'threads': '1', 'result_rows': '', 'result_hash': '', 'build_ms': '', 'h2d_ms': '', 'kernel_ms': '', 'd2h_ms': '', 'scan_ms': '', 'total_ms': '', 'elapsed_ms': '0.000000', 'error': '--arrow-dataset is required for arrow and cudf engines'}]
```

### cuDF tiny CLI

```bash
conda run -n memq5-cudf python baselines/cudf_q5.py \
  --dataset /tmp/memq5-tiny-arrow-baselines \
  --region ASIA \
  --date 1994-01-01 \
  --format json
```

```json
{
  "result_hash": "248d10b6ee352953",
  "rows": [
    {
      "nation": "JAPAN",
      "revenue_1e4": 1900000,
      "revenue": "190.00"
    },
    {
      "nation": "INDIA",
      "revenue_1e4": 900000,
      "revenue": "90.00"
    }
  ]
}
```

## Verification

### Python syntax

```bash
python -m py_compile \
  baselines/common.py \
  baselines/python_q5.py \
  baselines/arrow_q5.py \
  baselines/duckdb_q5.py \
  baselines/cudf_q5.py \
  scripts/run_benchmarks.py \
  tests/python/test_baseline_exact_mvp.py
```

Result: exit 0.

### Diff check

```bash
git -C /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation diff --check
```

Result: no output.

## Concerns

1. The installed `memq5-cudf` environment does not expose `cudf.from_arrow`
   at module scope, so the baseline includes a narrow compatibility fallback to
   `cudf.DataFrame.from_arrow` while still transferring Arrow tables into cuDF
   before query execution.

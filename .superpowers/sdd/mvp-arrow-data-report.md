# MVP Arrow Data Report

Status: DONE

Base branch: `codex/tpch-q5-arrow-implementation`

Task commit: `6eca6a8`

## Files Changed

- `scripts/tpch_arrow_schema.py`
- `scripts/prepare_arrow_dataset.py`
- `baselines/arrow_dataset.py`
- `tests/python/test_arrow_dataset_mvp.py`

## RED/GREEN Evidence

### RED: test-first failure before implementation

Command:

```bash
CONDA_PREFIX=/tmp/memq5-arrow-cpu-task1 PATH=/tmp/memq5-arrow-cpu-task1/bin:$PATH pytest -q tests/python/test_arrow_dataset_mvp.py
```

Output:

```text
==================================== ERRORS ====================================
___________ ERROR collecting tests/python/test_arrow_dataset_mvp.py ____________
ImportError while importing test module '/home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/tests/python/test_arrow_dataset_mvp.py'.
...
E   ModuleNotFoundError: No module named 'arrow_dataset'
=========================== short test summary info ============================
ERROR tests/python/test_arrow_dataset_mvp.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.15s
```

### GREEN: final pytest verification after implementation

Command:

```bash
CONDA_PREFIX=/tmp/memq5-arrow-cpu-task1 PATH=/tmp/memq5-arrow-cpu-task1/bin:$PATH pytest -q tests/python/test_arrow_dataset_mvp.py
```

Output:

```text
.......                                                                  [100%]
7 passed in 0.54s
```

## Exact Verification Commands And Outputs

### 1. Required CLI conversion

Command:

```bash
CONDA_PREFIX=/tmp/memq5-arrow-cpu-task1 PATH=/tmp/memq5-arrow-cpu-task1/bin:$PATH python scripts/prepare_arrow_dataset.py --input tests/fixtures/tpch_q5_tiny --output /tmp/memq5-tiny-arrow --scale-factor tiny --batch-rows 2 --source-command fixture
```

Output:

```text
[no stdout/stderr, exit 0]
```

### 2. Required shared loader verification

Command:

```bash
CONDA_PREFIX=/tmp/memq5-arrow-cpu-task1 PATH=/tmp/memq5-arrow-cpu-task1/bin:$PATH python -c "from pathlib import Path; from baselines.arrow_dataset import load_arrow_dataset; d=load_arrow_dataset(Path('/tmp/memq5-tiny-arrow')); assert d['lineitem'].num_rows == 6"
```

Output:

```text
[no stdout/stderr, exit 0]
```

## Self-Review

- The implementation stays within the owned Python surface from the brief.
- Decimal parsing uses `Decimal` only; no float path is used for `l_extendedprice` or `l_discount`.
- The dataset writer is non-destructive by default, writes through a temporary sibling directory, and renames atomically into place.
- Loader validation covers checksum, missing files, schema drift, row-count drift, record-batch drift, byte-size drift, and nulls in required columns.
- Dictionary columns are preserved in IPC output and loaded back as dictionary-typed Arrow columns.

## Concerns

- Dictionary-typed tables (`region`, `nation`) are materialized once up front so every record batch shares one stable dictionary, which is fine for the tiny dimension tables in this schema.
- I wrote this report after the commit to honor "commit only owned files", so the report itself is intentionally not part of commit `6eca6a8`.

## Review Fix Addendum

### Blocking finding: `replace=True` must preserve prior dataset on conversion failure

RED command:

```bash
CONDA_PREFIX=/tmp/memq5-arrow-cpu-task1 PATH=/tmp/memq5-arrow-cpu-task1/bin:$PATH pytest -q tests/python/test_arrow_dataset_mvp.py -k 'replace_preserves_existing_dataset_on_conversion_failure or writes_batched_ipc_files'
```

RED output:

```text
.F                                                                       [100%]
=================================== FAILURES ===================================
_ test_prepare_dataset_replace_preserves_existing_dataset_on_conversion_failure _
...
E       FileNotFoundError: [Errno 2] No such file or directory: '/tmp/pytest-of-xuzihuan/pytest-20/test_prepare_dataset_replace_p0/dataset'
...
1 failed, 1 passed, 6 deselected in 0.29s
```

GREEN command:

```bash
CONDA_PREFIX=/tmp/memq5-arrow-cpu-task1 PATH=/tmp/memq5-arrow-cpu-task1/bin:$PATH pytest -q tests/python/test_arrow_dataset_mvp.py -k 'replace_preserves_existing_dataset_on_conversion_failure or writes_batched_ipc_files'
```

GREEN output:

```text
..                                                                       [100%]
2 passed, 6 deselected in 0.24s
```

### Final verification for the review fix

1. `pytest -q tests/python/test_arrow_dataset_mvp.py`
   - `8 passed in 0.54s`
2. `python scripts/prepare_arrow_dataset.py --input tests/fixtures/tpch_q5_tiny --output /tmp/memq5-tiny-arrow-review-fix --scale-factor tiny --batch-rows 2 --source-command fixture`
   - exit 0, no stdout/stderr
3. `python -c "from pathlib import Path; from baselines.arrow_dataset import load_arrow_dataset; d=load_arrow_dataset(Path('/tmp/memq5-tiny-arrow-review-fix')); assert d['lineitem'].num_rows == 6"`
   - exit 0, no stdout/stderr
4. `python -m py_compile scripts/prepare_arrow_dataset.py tests/python/test_arrow_dataset_mvp.py`
   - exit 0, no stdout/stderr
5. `git diff --check -- scripts/prepare_arrow_dataset.py tests/python/test_arrow_dataset_mvp.py .superpowers/sdd/mvp-arrow-data-report.md`
   - exit 0, no output

### Final SHA

Recorded in the task result. This report lives inside the commit it describes, so embedding the exact final HEAD SHA here would change that SHA.

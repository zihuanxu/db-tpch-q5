# MVP Oracle Verifier Report

## Scope

Implemented the official Q5 oracle verifier CLI in `scripts/verify_q5_oracle.py`
with focused pytest coverage in `tests/python/test_verify_q5_oracle.py`.

## Owned Files Touched

- `scripts/verify_q5_oracle.py`
- `tests/python/test_verify_q5_oracle.py`
- `.superpowers/sdd/mvp-oracle-verifier-report.md`

I did not modify, revert, or stage unrelated work in the worktree.

## Requirements Covered

1. Added CLI arguments `--actual`, `--oracle`, and optional `--output-json`.
2. Parses MEMQ5 `--format rows` CSV, stops logical result parsing at
   `result_hash`, ignores following timing rows, and requires exactly one
   16-hex hash.
3. Parses official `q5.out` style pipe-delimited output with whitespace
   trimming.
4. Compares ordered nation names and exact two-decimal revenue strings with no
   float arithmetic.
5. On match, exits `0`, prints `ok rows=<n> result_hash=<hash>`, and writes
   stable sorted JSON when requested.
6. On mismatch, exits nonzero with useful stderr and writes
   `matched: false` plus mismatch details when JSON output is requested.
7. Added tests for match, revenue mismatch, ordering/nation mismatch,
   malformed hash, and stable JSON output.

## TDD Evidence

### RED

Command:

```bash
pytest /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/tests/python/test_verify_q5_oracle.py -q
```

Observed failure before implementation:

- `5 failed in 0.10s`
- Each failure reported Python could not open
  `scripts/verify_q5_oracle.py` because the verifier did not exist yet.

This was the expected missing-feature RED signal.

### GREEN

After implementing `scripts/verify_q5_oracle.py`, the same focused test suite
passed:

```bash
pytest /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/tests/python/test_verify_q5_oracle.py -q
```

Observed result:

- `5 passed in 0.15s`

## Verification Commands

Run after implementation:

```bash
pytest /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/tests/python/test_verify_q5_oracle.py -q
python -m py_compile /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/scripts/verify_q5_oracle.py /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/tests/python/test_verify_q5_oracle.py
git -C /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation diff --check -- scripts/verify_q5_oracle.py tests/python/test_verify_q5_oracle.py .superpowers/sdd/mvp-oracle-verifier-report.md
```

## Notes

- The verifier intentionally treats revenue as an exact string contract, so
  formatting mismatches are surfaced directly instead of normalized through
  decimal or float conversion.
- JSON output is sorted and indented to keep artifacts stable for downstream
  diffing.

## Concerns

- Tests use synthetic inline fixtures rather than a repository copy of the
  official `q5.out`, because no checked-in oracle fixture was present in this
  worktree.

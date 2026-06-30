# Submission Checklist

Use this checklist before handing in the project.

## Code And Tests

- [ ] Run local self-check:

  ```bash
  python3 scripts/self_check.py
  ```

- [ ] Confirm CPU tests pass.
- [ ] Confirm CUDA compile-only tests pass.
- [ ] On GPU server, confirm `ctest --test-dir build-cuda --output-on-failure`
      runs `test_q5_cuda` without skipping for missing devices.

## Data

- [ ] Prepare official TPC-H dbgen data:

  ```bash
  python3 scripts/prepare_tpch_q5_data.py \
    --source-dir /path/to/dbgen-output \
    --output-dir data/tpch_sf1 \
    --scale-factor 1 \
    --mode copy \
    --force
  ```

- [ ] Keep `memq5_manifest.json` for the final report appendix.
- [ ] Do not submit large generated `.tbl` files unless explicitly required.

## Experiments

- [ ] Run tiny GPU correctness experiment.
- [ ] Run at least one official TPC-H scale-factor experiment.
- [ ] Run CPU thread sweep.
- [ ] Run `gpu-copy`, `gpu-managed`, and `gpu-mapped`.
- [ ] Run cuDF baseline if RAPIDS is available.
- [ ] Verify all successful result hashes match:

  ```bash
  python3 scripts/verify_benchmark_hashes.py results/experiments/<run>/benchmarks.csv
  ```

## Report

- [ ] Start from `docs/FINAL_REPORT_DRAFT.md`.
- [ ] Replace expected-trend text with measured GPU results.
- [ ] Include `assets/total_time.svg`.
- [ ] Include `assets/time_breakdown.svg`.
- [ ] Include environment metadata from `environment.json`.
- [ ] Mention limitations honestly if cuDF or official dbgen data could not be
      run in the available environment.

## Package

Create a source-only archive:

```bash
python3 scripts/package_submission.py --output dist/memq5_submission.tar.gz
```

The archive intentionally excludes generated `build/`, `data/`, and `results/`
directories.

# Submission Checklist

Use this checklist before handing in the project.

Current status: GPU runtime validation has already passed on an RTX 4090 server
on 2026-07-01. Official TPC-H SF1 data and RAPIDS/cuDF were not available in
the active environment, so those items remain open.

## Code And Tests

- [x] Run local self-check:

  ```bash
  python3 scripts/self_check.py
  ```

- [x] Confirm CPU tests pass.
- [x] Confirm CUDA compile-only tests pass.
- [x] Confirm `CUDA_VISIBLE_DEVICES=0 ctest --test-dir build-cuda
      --output-on-failure` runs `test_q5_cuda` without skipping for missing
      devices.

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

- [x] Run tiny GPU correctness experiment.
- [ ] Run at least one official TPC-H scale-factor experiment.
- [x] Run CPU thread sweep on synthetic development data.
- [x] Run `gpu-copy`, `gpu-managed`, and `gpu-mapped`.
- [ ] Run cuDF baseline if RAPIDS is available.
- [x] Verify all successful result hashes match:

  ```bash
  python3 scripts/verify_benchmark_hashes.py results/experiments/<run>/benchmarks.csv
  ```

## Report

- [x] Start from `docs/FINAL_REPORT_DRAFT.md`.
- [x] Replace expected-trend text with measured GPU results.
- [x] Include `assets/total_time.svg`.
- [x] Include `assets/time_breakdown.svg`.
- [x] Include environment metadata from `environment.json`.
- [x] Mention limitations honestly if cuDF or official dbgen data could not be
      run in the available environment.

## Package

Create a source-only archive:

```bash
python3 scripts/package_submission.py --output dist/memq5_submission.tar.gz
```

The archive intentionally excludes generated `build/`, `data/`, and `results/`
directories.

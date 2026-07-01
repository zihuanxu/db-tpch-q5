# Current Status

This project has a working CPU correctness path, runtime-validated CUDA paths
for the three planned GPU memory modes, dependency-free correctness baselines,
benchmark automation, and official TPC-H SF1 results. GPU runtime validation
and the SF1 experiment were completed on an NVIDIA GeForce RTX 4090 server on
2026-07-01.

## Implemented Engines

- `cpu`
  - Builds Q5 filter-propagation maps on CPU.
  - Scans `lineitem` and aggregates revenue.
  - Supports `--threads N` for parallel lineitem scans.
- `gpu-copy`
  - Builds Q5 filter-propagation maps on CPU.
  - Copies maps and `lineitem` columns to GPU with explicit `cudaMemcpy`.
  - Runs a handwritten CUDA lineitem aggregation kernel.
- `gpu-managed`
  - Uses `cudaMallocManaged`.
  - Prefetches input/output buffers before and after the kernel.
- `gpu-mapped`
  - Uses `cudaHostAllocMapped`.
  - Lets the GPU read mapped pinned host input buffers through device pointers.

The GPU engines compile and run on the validated GPU server. The 2026-07-01 run
used `CUDA_VISIBLE_DEVICES=0` on an RTX 4090 with compute capability 8.9 and
`CMAKE_CUDA_ARCHITECTURES=89`.

## Implemented Baselines

- `baselines/python_q5.py`
  - Dependency-free correctness reference.
- `baselines/duckdb_q5.py`
  - SQL correctness/performance baseline when the DuckDB Python package is
    installed.
- `baselines/cudf_q5.py`
  - RAPIDS cuDF operator-library baseline on a RAPIDS GPU environment.

## Implemented Tools

- `scripts/generate_synthetic_tpch_q5.py`
  - Generates deterministic TPC-H-like Q5 `.tbl` data for local development.
  - Not a replacement for official TPC-H dbgen data in final experiments.
- `scripts/validate_tpch_q5_data.py`
  - Validates required Q5 `.tbl` files, required columns, key/date/decimal
    parsing, target region, and date-window coverage.
- `scripts/prepare_tpch_q5_data.py`
  - Takes an existing official TPC-H dbgen output directory, copies or symlinks
    the six Q5-required `.tbl` files, validates the data, and writes a manifest
    with row counts, byte sizes, hashes, source paths, and validation status.
- `scripts/run_benchmarks.py`
  - Runs a matrix of engines and repeats.
  - Supports `--thread-list` for CPU/C++ thread sweeps.
  - Writes a unified benchmark CSV.
- `scripts/verify_benchmark_hashes.py`
  - Verifies all successful runs for the same `(region, date)` agree on
    `result_hash`.
- `scripts/summarize_benchmarks.py`
  - Produces median/mean/min summaries from benchmark CSVs.
- `scripts/make_report_assets.py`
  - Produces report-ready `summary.md`, `total_time.svg`, and
    `time_breakdown.svg` from benchmark CSVs.
- `scripts/capture_environment.py`
  - Captures platform, CPU, CUDA, NVIDIA driver, CMake, DuckDB, and cuDF
    metadata for the final report.
- `scripts/run_experiment_pipeline.py`
  - One-command pipeline for data validation, environment capture, benchmark
    runs, hash verification, summary generation, and report asset generation.
- `scripts/self_check.py`
  - Runs Python syntax checks, CMake configure/build, CPU tests, CUDA
    compile-only tests when `nvcc` is available, data validation, and a tiny
    end-to-end experiment pipeline.
- `scripts/package_submission.py`
  - Creates a source-only submission archive, excluding generated build, data,
    and result directories.

## Verification

CPU build:

```bash
cmake --build build
ctest --test-dir build --output-on-failure
```

CUDA compile-only build:

```bash
cmake --build build-cuda
ctest --test-dir build-cuda --output-on-failure
```

GPU runtime validation:

```bash
CUDA_VISIBLE_DEVICES=0 ctest --test-dir build-cuda --output-on-failure
```

Result: all six CTest tests passed, including `test_q5_cuda` on a real NVIDIA
GPU.

Tiny GPU correctness:

```bash
CUDA_VISIBLE_DEVICES=0 python3 scripts/run_experiment_pipeline.py \
  --name tiny_gpu_modes \
  --memq5 build-cuda/memq5 \
  --data-dir tests/fixtures/tpch_q5_tiny \
  --engines cpu,gpu-copy,gpu-managed,gpu-mapped,python \
  --repeat 5 \
  --force
```

Result hash: `1e07d78fa8eededb` for CPU, all three GPU modes, and Python.

Synthetic GPU development run:

```bash
CUDA_VISIBLE_DEVICES=0 python3 scripts/run_experiment_pipeline.py \
  --name synthetic_gpu_modes \
  --memq5 build-cuda/memq5 \
  --data-dir data/synthetic_gpu_dev \
  --engines cpu,gpu-copy,gpu-managed,gpu-mapped,python \
  --thread-list 1,2,4,8 \
  --repeat 5 \
  --force
```

Result hash: `d5ffe393223a207e` for CPU, all three GPU modes, and Python.

Official TPC-H SF1 run:

```bash
python3 scripts/prepare_tpch_q5_data.py \
  --source-dir data/tpch_sf1_raw \
  --output-dir data/tpch_sf1 \
  --scale-factor 1 \
  --mode copy \
  --force

CUDA_VISIBLE_DEVICES=0 python3 scripts/run_experiment_pipeline.py \
  --name tpch_sf1_gpu_modes \
  --memq5 build-cuda/memq5 \
  --data-dir data/tpch_sf1 \
  --engines cpu,gpu-copy,gpu-managed,gpu-mapped \
  --thread-list 1,2,4,8 \
  --repeat 5 \
  --force
```

Result hash: `9f1f5f7578dd816e` for CPU and all three GPU modes.

Synthetic data check:

```bash
python3 scripts/generate_synthetic_tpch_q5.py --output data/synthetic_dev \
  --customers 1000 --orders 5000 --lineitems 20000 --suppliers 500 --asia-heavy

python3 scripts/validate_tpch_q5_data.py --data-dir data/synthetic_dev

python3 scripts/run_benchmarks.py --engines cpu,python --thread-list 1,2,4 \
  --data-dir data/synthetic_dev --repeat 2 \
  --output results/synthetic_thread_sweep.csv

python3 scripts/verify_benchmark_hashes.py results/synthetic_thread_sweep.csv
python3 scripts/summarize_benchmarks.py results/synthetic_thread_sweep.csv
```

## Remaining Work

1. Add RAPIDS cuDF to the environment and include `cudf` in the benchmark
   matrix if RAPIDS is available.
2. Keep generated TPC-H tools, `.tbl` data, raw `results/`, and build
   directories out of version control.

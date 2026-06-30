# Current Status

This project has a working CPU correctness path, CUDA compile-only paths for the
three planned GPU memory modes, dependency-free correctness baselines, and
benchmark automation.

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

The GPU engines compile on this machine, but runtime validation requires a
working NVIDIA driver. Current local `nvidia-smi` cannot communicate with the
driver.

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

## Local Verification

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

## Next Required Work

1. Move to a GPU server with a working NVIDIA driver.
2. Build with CUDA enabled, using the server's architecture:

   ```bash
   cmake -S . -B build-cuda -DMEMQ5_ENABLE_CUDA=ON \
     -DMEMQ5_ENABLE_TESTS=ON -DCMAKE_CUDA_ARCHITECTURES=<arch>
   cmake --build build-cuda
   ctest --test-dir build-cuda --output-on-failure
   ```

3. Run GPU engine correctness and benchmark matrix:

   ```bash
   python3 scripts/run_benchmarks.py --memq5 build-cuda/memq5 \
     --engines cpu,gpu-copy,gpu-managed,gpu-mapped,python \
     --data-dir tests/fixtures/tpch_q5_tiny --repeat 5 \
     --output results/tiny_gpu_modes.csv

   python3 scripts/verify_benchmark_hashes.py results/tiny_gpu_modes.csv
   python3 scripts/summarize_benchmarks.py results/tiny_gpu_modes.csv
   ```

4. Add RAPIDS cuDF to the GPU server environment and include `cudf` in the
   benchmark matrix.
5. Prepare official TPC-H dbgen data for final experiments.
6. Run scale sweeps and thread sweeps.
7. Analyze bottlenecks and write the final report.

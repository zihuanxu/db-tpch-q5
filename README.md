# Memory DB TPC-H Q5 Lab

This repository is planned as a deliverable course project for an in-memory
database assignment. The target is a small but complete heterogeneous columnar
query engine for TPC-H Q5, with CPU execution, handwritten CUDA execution,
explicit PCIe transfer, UVA/unified-memory experiments, and comparisons against
NVIDIA RAPIDS cuDF. Apache Arrow remains a layout reference and optional CPU
baseline, not the central deliverable.

The project starts from course notes and teacher-discussion transcripts. The
reconstructed requirements and final implementation plan are in:

- `docs/RECONSTRUCTED_REQUIREMENTS.md`
- `docs/FINAL_IMPLEMENTATION_PLAN.md`
- `docs/IMPLEMENTATION_CHECKLIST.md`
- `docs/BENCHMARK_PROTOCOL.md`
- `docs/TECHNICAL_DECISIONS.md`

## Final Deliverable Shape

The code repository should eventually contain:

- An Arrow-compatible fixed-width column store for the TPC-H Q5 columns.
- A CPU vectorized/parallel implementation of Q5.
- CUDA implementations of Q5 using explicit host-device copies and UVA-style
  mapped/managed memory modes.
- Bitmap/array filter propagation for region, nation, customer, supplier, and
  orders before probing lineitem.
- Benchmark scripts for CPU, GPU, transfer modes, Apache Arrow/Acero, RAPIDS
  cuDF, and optionally DuckDB validation.
- Unit tests on small deterministic fixtures and validation against a SQL
  baseline on TPC-H dbgen data.
- A final report with correctness, throughput, transfer-cost, memory-footprint,
  and hardware-analysis results.

## Current Status

The implementation now includes the CPU path, three handwritten CUDA memory
modes, Python/DuckDB/cuDF baseline scripts, benchmark automation, and report
asset generation. GPU runtime validation passed on an RTX 4090 server on
2026-07-01. Official TPC-H dbgen SF1 data was generated and benchmarked on the
same server. The generated TPC-H tools, `.tbl` data, and raw results are not
committed to the repository.

See [docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md) for the latest implemented
engines, verification commands, and next required work.

Useful delivery documents:

- [docs/GPU_SERVER_RUNBOOK.md](docs/GPU_SERVER_RUNBOOK.md)
- [docs/FINAL_REPORT_DRAFT.md](docs/FINAL_REPORT_DRAFT.md)
- [docs/COMPLETION_AUDIT.md](docs/COMPLETION_AUDIT.md)

## Build And Run

```bash
cmake -S . -B build -DMEMQ5_ENABLE_CUDA=OFF -DMEMQ5_ENABLE_TESTS=ON
cmake --build build
ctest --test-dir build --output-on-failure
./build/memq5 --engine cpu --data-dir tests/fixtures/tpch_q5_tiny \
  --region ASIA --date 1994-01-01 --format rows
```

CUDA compile-only check, on a machine with `nvcc`:

```bash
cmake -S . -B build-cuda -DMEMQ5_ENABLE_CUDA=ON -DMEMQ5_ENABLE_TESTS=ON
cmake --build build-cuda
```

Override `CMAKE_CUDA_ARCHITECTURES` for the benchmark GPU when needed, for
example `-DCMAKE_CUDA_ARCHITECTURES=80` for A100-class machines or `89` for
RTX 4090/L20-class Ada machines.

Running `--engine gpu-copy`, `--engine gpu-managed`, or `--engine gpu-mapped`
additionally requires a working NVIDIA driver.

Optional baselines:

```bash
python3 baselines/python_q5.py --data-dir tests/fixtures/tpch_q5_tiny \
  --region ASIA --date 1994-01-01 --format rows
python3 baselines/duckdb_q5.py --data-dir tests/fixtures/tpch_q5_tiny \
  --region ASIA --date 1994-01-01 --format rows
python3 baselines/cudf_q5.py --data-dir tests/fixtures/tpch_q5_tiny \
  --region ASIA --date 1994-01-01 --format rows
```

`python_q5.py` is dependency-free and intended for correctness checks only.
`duckdb_q5.py` requires the DuckDB Python package. `cudf_q5.py` requires a
RAPIDS environment with cuDF and a working NVIDIA GPU stack.

Batch benchmark runner:

```bash
python3 scripts/validate_tpch_q5_data.py --data-dir tests/fixtures/tpch_q5_tiny
python3 scripts/run_benchmarks.py --engines cpu,python --repeat 3 \
  --output results/tiny_cpu_python.csv
```

Prepare an existing official TPC-H dbgen output directory:

```bash
python3 scripts/prepare_tpch_q5_data.py \
  --source-dir /path/to/dbgen-output \
  --output-dir data/tpch_sf1 \
  --scale-factor 1 \
  --mode copy
```

CPU thread sweep:

```bash
python3 scripts/generate_synthetic_tpch_q5.py --output data/synthetic_dev \
  --customers 1000 --orders 5000 --lineitems 20000 --suppliers 500 --asia-heavy
python3 scripts/run_benchmarks.py --engines cpu,python --thread-list 1,2,4,8 \
  --data-dir data/synthetic_dev --repeat 5 --output results/thread_sweep.csv
python3 scripts/summarize_benchmarks.py results/thread_sweep.csv
```

On a GPU server, point `--memq5` at the CUDA build and include GPU engines:

```bash
python3 scripts/run_benchmarks.py --memq5 build-cuda/memq5 \
  --engines cpu,gpu-copy,gpu-managed,gpu-mapped,cudf --repeat 5 \
  --output results/gpu_server.csv
```

Check successful benchmark rows agree on correctness:

```bash
python3 scripts/verify_benchmark_hashes.py results/gpu_server.csv
```

Generate report-ready tables and SVG figures:

```bash
python3 scripts/make_report_assets.py results/gpu_server.csv \
  --output-dir results/report_assets --title "TPC-H Q5 GPU Server"
```

Capture machine metadata for the report:

```bash
python3 scripts/capture_environment.py --output results/environment.json
```

One-command experiment pipeline:

```bash
python3 scripts/run_experiment_pipeline.py \
  --name tiny_cpu_python \
  --data-dir tests/fixtures/tpch_q5_tiny \
  --engines cpu,python \
  --repeat 3 \
  --force
```

Local self-check:

```bash
python3 scripts/self_check.py
```

Create a source submission archive:

```bash
python3 scripts/package_submission.py --output dist/memq5_submission.tar.gz
```

# Implementation Checklist

This checklist converts the final plan into concrete coding tasks. Follow it in
order; each section has an exit criterion.

## 1. Repository Skeleton

Files to create:

- `CMakeLists.txt`
- `src/cli/memq5.cpp`
- `src/common/timer.hpp`
- `tests/CMakeLists.txt`
- `tests/test_smoke.cpp`

Tasks:

- Require C++17.
- Add optional CUDA language support behind `MEMQ5_ENABLE_CUDA`.
- Build `memq5`.
- Add `ctest` integration.
- CLI supports `--help`.

Exit criterion:

```bash
cmake -S . -B build -DMEMQ5_ENABLE_CUDA=OFF -DMEMQ5_ENABLE_TESTS=ON
cmake --build build
ctest --test-dir build
./build/memq5 --help
```

## 2. Core Data Structures

Files to create:

- `src/common/aligned_buffer.hpp`
- `src/common/column.hpp`
- `src/common/bitmap.hpp`
- `src/common/date.hpp`
- `tests/test_common.cpp`

Tasks:

- Implement 64-byte aligned allocation.
- Implement owning `Column<T>`.
- Implement non-owning `ColumnView<T>`.
- Implement optional validity bitmap functions:
  - set bit,
  - clear bit,
  - test bit,
  - count set bits.
- Implement date parser for `YYYY-MM-DD`.
- Convert date to integer day offset.
- Implement `add_year(date)` for Q5 upper bound.

Exit criterion:

- aligned buffer pointer is 64-byte aligned,
- date range for `1994-01-01` to `1995-01-01` is correct,
- bitmap tests pass.

## 3. TPC-H Loader

Files to create:

- `src/io/tpch_schema.hpp`
- `src/io/tpch_loader.hpp`
- `src/io/tpch_loader.cpp`
- `tests/fixtures/tpch_q5_tiny/*.tbl`
- `tests/test_loader.cpp`

Tasks:

- Parse pipe-delimited TPC-H `.tbl` lines.
- Load only required columns.
- Dictionary encode `r_name` and `n_name`.
- Store prices/discounts as fixed-point integers.
- Ignore unused columns without storing them.
- Validate row counts in fixture.

Exit criterion:

- fixture loads successfully,
- required column lengths match expected fixture counts,
- dictionaries can map `ASIA` and nation names.

## 4. Q5 CPU Engine

Files to create:

- `src/engine/q5_params.hpp`
- `src/engine/q5_result.hpp`
- `src/engine/q5_plan.hpp`
- `src/cpu/q5_cpu.hpp`
- `src/cpu/q5_cpu.cpp`
- `tests/test_q5_cpu.cpp`

Tasks:

- Implement `nation_in_region[25]`.
- Implement `supplier_nation_by_key`.
- Implement `customer_nation_by_key`.
- Implement `order_nation_by_key`.
- Implement lineitem revenue aggregation.
- Use fixed-point revenue.
- Sort output by revenue descending.
- Expose timing counters.

Exit criterion:

- CPU result matches expected fixture output exactly.
- Running `memq5 --engine cpu --data-dir tests/fixtures/tpch_q5_tiny`
  prints deterministic results and timing.

## 5. DuckDB Validation Baseline

Files to create:

- `baselines/duckdb_q5.py`
- `tests/test_duckdb_validation.py`

Tasks:

- Load fixture `.tbl` files into DuckDB.
- Run exact Q5 SQL.
- Convert output to same fixed-point convention or compare decimal output.
- Compare custom CPU JSON/CSV result against DuckDB.

Exit criterion:

- DuckDB validation passes on fixture when DuckDB Python package is installed.
- Test is skipped cleanly when DuckDB is absent.

## 6. CUDA Explicit-Copy Engine

Files to create:

- `src/cuda/q5_cuda.hpp`
- `src/cuda/q5_cuda.cu`
- `src/cuda/transfer.hpp`
- `src/cuda/transfer.cu`
- `tests/test_q5_cuda.cpp`

Tasks:

- Add CUDA CMake target.
- Implement device buffer wrapper.
- Implement async H2D/D2H copy helpers.
- Implement `build_order_nation_kernel`.
- Implement `lineitem_q5_aggregate_kernel`.
- Add CUDA event timing.
- Return same `Q5Result` as CPU.

Exit criterion:

- `gpu-copy` compiles with `nvcc`.
- `gpu-copy` matches CPU on the fixture; this was validated on an RTX 4090 on
  2026-07-01.
- If no driver exists, runtime test is skipped with a clear message.

## 7. GPU Mapped And Managed Memory Modes

Files to extend:

- `src/cuda/transfer.hpp`
- `src/cuda/transfer.cu`
- `src/cuda/q5_cuda.cu`

Tasks:

- Add `PinnedMappedBuffer`.
- Allocate host memory with `cudaHostAllocMapped`.
- Get device pointer with `cudaHostGetDevicePointer`.
- Run kernels using mapped pointers.
- Add `ManagedBuffer`.
- Add optional `cudaMemPrefetchAsync`.
- Ensure benchmark reports memory mode.

Exit criterion:

- `gpu-mapped` and `gpu-managed` match CPU on fixture.
- Benchmark CSV separates H2D, kernel, D2H, total time.

## 8. PyArrow Baseline

Files:

- `baselines/arrow_q5.py`
- `scripts/run_benchmarks.py`

Tasks:

- Keep PyArrow optional so the C++ core builds without Arrow.
- Read required TPC-H `.tbl` columns through `pyarrow.csv`.
- Store Q5 inputs as real PyArrow `Table` objects.
- Execute equivalent filters, joins, projection, groupby, and sort through
  PyArrow Table/compute APIs.
- Emit the same rows, JSON, benchmark CSV, and result hash format as other
  baselines.
- Register `arrow` in the unified benchmark runner.

Exit criterion:

- Build succeeds without PyArrow.
- `arrow` validates on the tiny fixture.
- Official TPC-H SF1 full matrix validates
  `cpu,arrow,gpu-copy,gpu-managed,gpu-mapped,cudf` with 90 benchmark rows,
  0 errors, and hash `9f1f5f7578dd816e`.

## 9. RAPIDS cuDF Baseline

Files to create:

- `baselines/cudf_q5.py`

Tasks:

- Read `.tbl` files.
- Select required columns.
- Filter region/date.
- Merge tables according to Q5.
- Compute revenue.
- Group by nation and sort.
- Emit result JSON and benchmark CSV.

Exit criterion:

- Script runs in a RAPIDS environment.
- Output matches CPU on fixture and generated data.
- The validated GPU server used the existing `memq5-cudf` conda environment
  with cuDF `26.06.00`.

## 10. Benchmark Runner

Files to create:

- `scripts/run_bench.py`
- `scripts/plot_results.py`
- `scripts/collect_env.py`
- `results/.gitkeep`

Tasks:

- Run engine matrix.
- Handle unavailable optional engines cleanly.
- Compute `result_hash`.
- Write per-repeat CSV.
- Write environment JSON.
- Generate required plots.

Exit criterion:

```bash
python3 scripts/run_bench.py \
  --data-dir tests/fixtures/tpch_q5_tiny \
  --engines cpu \
  --repeat 3 \
  --out results/fixture_bench.csv
```

produces a valid CSV and plots.

## 11. TPC-H Data Generation Script

Files to create:

- `scripts/generate_tpch.sh`
- `docs/DATA_GENERATION.md`

Tasks:

- Document how to obtain official TPC-H tools.
- Build `dbgen`.
- Generate `.tbl` files for a given scale factor.
- Do not vendor TPC-H tools.

Exit criterion:

- A user with TPC-H tools can generate SF1 data following the doc.

## 12. Final Report

Files to create:

- `docs/FINAL_REPORT.md`
- optional `docs/FINAL_REPORT.pdf`

Tasks:

- Fill `REPORT_TEMPLATE.md`.
- Include source citations.
- Include benchmark plots.
- State hardware limitations.
- Explain why mapped/UVA-style access behaves as measured.

Exit criterion:

- Report can be read without inspecting code.
- Claims are backed by results and reproducible commands.

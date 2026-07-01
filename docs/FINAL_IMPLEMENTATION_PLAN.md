# Final Implementation Plan

## 0. Project Thesis

Build a small in-memory columnar query engine specialized for TPC-H Q5. The
engine should show, with measurable experiments, how column layout, bitmap/array
filter propagation, CPU/GPU placement, PCIe transfer, and UVA-style memory access
affect an analytical multi-table join.

The project is intentionally not a general SQL DBMS. It is a focused, complete
course deliverable: one realistic query, multiple execution strategies, correct
validation, benchmark automation, and a report that ties performance to hardware.

## 1. Iteration Log

### Version 0: Literal Reading

Initial interpretation:

- Implement CPU and GPU databases.
- Implement Apache Arrow.
- Compare UVA and PCIe.
- Compare with Apache Arrow and NVIDIA native implementation.
- Run TPC-H Q5.

Problem: this is too broad and underspecified. A full database, a full Arrow
implementation, and a full GPU engine are not realistic for a course project.

### Version 1: Query-First Scope

Narrow the system to one benchmark query: TPC-H Q5.

Benefits:

- Tables, columns, predicates, joins, and aggregation are fixed.
- Correctness can be checked against SQL.
- Every optimization can be tied to a real query stage.

Remaining gap: still unclear how to connect Q5 to the note's bitmap/star-join
idea.

### Version 2: Filter Propagation Instead Of Generic Joins

Represent region and date predicates as compact arrays/bitmaps and push them
through dimension-to-fact relationships:

1. `region -> nation mask`
2. `nation mask -> supplier nation map`
3. `nation mask -> customer nation map`
4. `customer + date -> order nation map`
5. `orders + supplier -> lineitem revenue aggregation`

This turns Q5 into a star-join-like bitmap/array propagation pipeline, matching
the note's "级层传递" requirement.

### Version 3: Arrow-Compatible Layout, Not Full Arrow

Implement only the subset needed for Q5:

- fixed-width primitive columns,
- optional validity bitmaps,
- 64-byte aligned buffers,
- dictionary encoding for names,
- Arrow export/import boundary where possible.

Use Apache Arrow/Acero as a baseline instead of reimplementing all Arrow
metadata, IPC, nested arrays, and compute kernels.

### Version 4: Explicit GPU Transfer Modes

Define transfer modes precisely:

- `gpu-copy`: host Arrow-like buffers -> device buffers via `cudaMemcpyAsync`.
- `gpu-mapped`: pinned mapped host memory accessed by GPU through UVA-style
  device pointers.
- `gpu-managed`: `cudaMallocManaged`, with optional `cudaMemPrefetchAsync`.

Benchmark them separately with transfer time, kernel time, and total time.

### Version 5: Stable Final Scope

Final deliverable:

- CPU Q5 engine.
- CUDA Q5 engine with explicit-copy and UVA-style modes.
- Apache Arrow/Acero CPU baseline.
- RAPIDS cuDF GPU baseline.
- DuckDB optional correctness/performance baseline.
- TPC-H dbgen ingestion plus small test fixtures.
- Benchmark scripts and final report.

This scope is implementable and satisfies the original notes without pretending
to build a complete commercial DBMS.

## 2. Data Model

Only Q5 columns are loaded.

### `region`

- `r_regionkey`: `int32`
- `r_name`: dictionary code, with side dictionary

### `nation`

- `n_nationkey`: `int32`
- `n_name`: dictionary code, with side dictionary
- `n_regionkey`: `int32`

### `supplier`

- `s_suppkey`: `int32`
- `s_nationkey`: `int32`

### `customer`

- `c_custkey`: `int32`
- `c_nationkey`: `int32`

### `orders`

- `o_orderkey`: `int32`
- `o_custkey`: `int32`
- `o_orderdate`: `int32`

`o_orderdate` is stored as days since `1970-01-01` or days since TPC-H's minimum
date. The exact epoch must be fixed in `date.hpp` and used everywhere.

### `lineitem`

- `l_orderkey`: `int32`
- `l_suppkey`: `int32`
- `l_extendedprice`: fixed-point `int64`, cents
- `l_discount`: fixed-point `int32`, basis points or hundredths

For exact repeatability, use integer fixed-point arithmetic in the engine and
convert to decimal/double only at output.

## 3. Column Store Design

Implement `Column<T>` and `Table` in `src/common`.

Required properties:

- contiguous value buffer,
- 64-byte aligned allocation,
- length and null count metadata,
- optional validity bitmap,
- non-owning `ColumnView<T>` for CPU kernels,
- `DeviceColumnView<T>` for CUDA kernels,
- load from TPC-H `.tbl` files,
- export to Arrow arrays/tables where Arrow C++ is enabled.

Minimal API:

```cpp
template <class T>
struct ColumnView {
  const T* data;
  const uint8_t* validity;
  int64_t size;
};

template <class T>
class Column {
 public:
  Column(int64_t size, MemoryResource resource);
  ColumnView<T> view() const;
  T* mutable_data();
  int64_t size() const;
};
```

## 4. Q5 Execution Plan

### Parameters

- `--sf <scale factor>`
- `--data-dir <path>`
- `--region ASIA|EUROPE|...`
- `--date 1994-01-01`
- `--engine cpu|gpu-copy|gpu-mapped|gpu-managed|arrow|cudf|duckdb`
- `--threads <n>`
- `--repeat <n>`
- `--chunk-rows <n>` for GPU chunking experiments

### Logical Plan

```sql
select
  n_name,
  sum(l_extendedprice * (1 - l_discount)) as revenue
from customer, orders, lineitem, supplier, nation, region
where c_custkey = o_custkey
  and l_orderkey = o_orderkey
  and l_suppkey = s_suppkey
  and c_nationkey = s_nationkey
  and s_nationkey = n_nationkey
  and n_regionkey = r_regionkey
  and r_name = :region
  and o_orderdate >= :date
  and o_orderdate < :date + 1 year
group by n_name
order by revenue desc;
```

### Physical Plan

1. Build `region_key` from `region`.
2. Build `nation_in_region[25]` bitset and `nation_name[25]`.
3. Build `supplier_nation_by_key[max_suppkey + 1]`:
   - value is nation key if supplier's nation is in selected region,
   - `-1` otherwise.
4. Build `customer_nation_by_key[max_custkey + 1]` similarly.
5. Build `order_nation_by_key[max_orderkey + 1]`:
   - date predicate is applied on `orders`,
   - customer nation is looked up from `customer_nation_by_key`,
   - valid value is customer nation,
   - invalid value is `-1`.
6. Scan `lineitem`:
   - `order_nation = order_nation_by_key[l_orderkey]`
   - `supplier_nation = supplier_nation_by_key[l_suppkey]`
   - accept if both valid and equal,
   - add `l_extendedprice * (1 - l_discount)` into `revenue[order_nation]`.
7. Sort the at-most-25 nation outputs by revenue descending.

This avoids materializing six-table join results and matches a columnar OLAP
engine's predicate-pushdown/filter-propagation style.

## 5. CPU Engine

The CPU path should be the correctness oracle and a serious baseline.

Implementation:

- Use the same physical plan as GPU.
- Build small dimension maps on CPU.
- Parallelize orders and lineitem passes with a simple thread pool or OpenMP.
- Use per-thread `int64 revenue[25]` arrays, then reduce.
- Avoid locks in the lineitem loop.
- Optionally add SIMD-friendly batch loops later.

Expected output:

- result rows,
- scan rows,
- accepted rows,
- build time,
- probe/aggregate time,
- total time,
- peak memory.

## 6. GPU Engine

### `gpu-copy`

Flow:

1. Load host Arrow-like buffers.
2. Allocate device buffers with `cudaMalloc`.
3. Copy required columns/maps with `cudaMemcpyAsync`.
4. Run kernels:
   - `build_order_nation_kernel`
   - `lineitem_q5_aggregate_kernel`
5. Copy `revenue[25]` back.
6. Sort/output on CPU.

Timing:

- H2D time,
- kernel time,
- D2H time,
- total time.

### `gpu-mapped`

Flow:

1. Allocate/load relevant host buffers with pinned mapped memory.
2. Obtain device pointers with `cudaHostGetDevicePointer`.
3. Run kernels directly against mapped host memory.

This is the UVA-style zero-copy experiment. It is expected to reduce explicit
copy cost but may be slower for repeated/random access because GPU reads cross
PCIe/NVLink on demand.

### `gpu-managed`

Flow:

1. Allocate with `cudaMallocManaged`.
2. Test two modes:
   - no prefetch,
   - `cudaMemPrefetchAsync` to GPU before kernels.

This captures managed-memory page migration behavior separately from mapped
host-memory zero-copy.

### Kernels

`build_order_nation_kernel`:

```cpp
for each order i:
  if date in range:
    nation = customer_nation_by_key[o_custkey[i]]
    order_nation_by_key[o_orderkey[i]] = nation
  else:
    order_nation_by_key[o_orderkey[i]] = -1
```

`lineitem_q5_aggregate_kernel`:

```cpp
shared int64 block_revenue[25]

for each lineitem i:
  on = order_nation_by_key[l_orderkey[i]]
  sn = supplier_nation_by_key[l_suppkey[i]]
  if on >= 0 and on == sn:
    atomicAdd(block_revenue[on], revenue(l_extendedprice, l_discount))

after block reduction:
  atomicAdd(global_revenue[n], block_revenue[n])
```

Use integer arithmetic for revenue. If `int64 atomicAdd` support is an issue on
the target GPU, use unsigned long long or a two-stage reduction buffer.

## 7. Baselines

### Apache Arrow/Acero

Purpose: compare against an Arrow-native CPU execution engine.

Implementation options:

- C++ Acero execution plan if Arrow C++ is installed.
- Python/PyArrow fallback if C++ linkage is too heavy.

The baseline should run semantically equivalent Q5 and report total time.

### RAPIDS cuDF

Purpose: compare against NVIDIA's high-level GPU DataFrame implementation.

Use `cudf.read_csv`, `merge`, filtering, computed revenue column, `groupby`, and
sort. This is not expected to beat a specialized hand-written kernel on one
query, but it is the correct "NVIDIA native ecosystem" baseline.

### DuckDB

Purpose: correctness validation and optional CPU reference. DuckDB is not part
of the original requirement but is useful because it can run the exact SQL over
generated `.tbl`/CSV data.

## 8. Benchmark Protocol

### Data Scale

Minimum:

- fixture: tiny deterministic data committed in `tests/fixtures`.
- SF 0.01 or synthetic small scale for smoke tests.
- SF 1 for final correctness/performance.

If hardware permits:

- SF 10, SF 30, SF 100.

### Runs

For each engine and scale:

- warm up once,
- run at least 5 repeats,
- report median and p95,
- report separate transfer/kernel times for GPU,
- report peak memory where available.

### Metrics

- total query time,
- lineitem rows per second,
- accepted lineitems,
- revenue output,
- H2D/D2H transfer time,
- kernel time,
- bytes transferred,
- memory footprint,
- speedup relative to CPU and baselines.

### Required Comparisons

1. CPU custom vs GPU custom.
2. GPU explicit PCIe copy vs mapped UVA-style access vs managed memory.
3. Custom CPU vs Apache Arrow/Acero.
4. Custom GPU vs RAPIDS cuDF.
5. Optional custom CPU vs DuckDB.

## 9. Validation

Correctness checks:

1. Unit tests for:
   - date parsing and integer date offset,
   - bitmap operations,
   - dimension-map construction,
   - revenue arithmetic,
   - CPU Q5 fixture output.
2. GPU result equals CPU result on fixture.
3. CPU/GPU output equals DuckDB SQL output on generated TPC-H data.
4. For final report, use TPC-H validation parameters where practical:
   - `REGION = ASIA`
   - `DATE = 1994-01-01`

## 10. Repository Structure

```text
memory-db-tpch-q5/
  CMakeLists.txt
  README.md
  docs/
    RECONSTRUCTED_REQUIREMENTS.md
    FINAL_IMPLEMENTATION_PLAN.md
    BENCHMARK_PROTOCOL.md
    REPORT_TEMPLATE.md
  src/
    common/
      aligned_buffer.hpp
      bitmap.hpp
      column.hpp
      date.hpp
      timer.hpp
    io/
      tpch_loader.hpp
      tpch_loader.cpp
      dictionaries.hpp
    engine/
      q5_plan.hpp
      q5_result.hpp
    cpu/
      q5_cpu.hpp
      q5_cpu.cpp
    cuda/
      q5_cuda.hpp
      q5_cuda.cu
      transfer.hpp
      transfer.cu
    cli/
      memq5.cpp
  baselines/
    duckdb_q5.py
    cudf_q5.py
    arrow_acero_q5.cpp
  scripts/
    generate_tpch.sh
    run_bench.py
    plot_results.py
  tests/
    fixtures/
    test_cpu.cpp
    test_validation.py
  results/
    .gitkeep
```

## 11. Build Plan

Use CMake with optional components:

- `MEMQ5_ENABLE_CUDA=ON/OFF`
- `MEMQ5_ENABLE_ARROW=ON/OFF`
- `MEMQ5_ENABLE_TESTS=ON/OFF`

CPU-only build must work without CUDA driver access. CUDA build requires `nvcc`.
GPU runtime tests were completed on an RTX 4090 server on 2026-07-01.

Validation machine check:

- `nvidia-smi` reported RTX 4090 and L20 GPUs.
- The validation run used `CUDA_VISIBLE_DEVICES=0` on an RTX 4090.
- CUDA was built with `CMAKE_CUDA_ARCHITECTURES=89`.
- `test_q5_cuda`, `tiny_gpu_modes`, and `synthetic_gpu_modes` completed with
  matching CPU/GPU/Python hashes.

## 12. Implementation Milestones

### Milestone 1: Skeleton And Fixtures

- Add CMake project.
- Add `memq5` CLI with argument parsing.
- Add tiny Q5 fixture data.
- Add CPU date/bitmap/unit tests.

Exit criterion: CPU fixture test passes.

### Milestone 2: CPU Q5 Engine

- Implement `.tbl` loader for required columns.
- Implement dictionary/name handling.
- Implement dimension maps and CPU Q5 pipeline.
- Validate against DuckDB on fixture and generated small data.

Exit criterion: CPU output matches SQL baseline.

### Milestone 3: CUDA Explicit-Copy Engine

- Implement device buffer transfer.
- Implement order map and lineitem aggregate kernels.
- Add GPU fixture validation.
- Add timing breakdown.

Exit criterion: `gpu-copy` equals CPU on fixture and SF1.

### Milestone 4: UVA/Managed Experiments

- Implement mapped pinned host path.
- Implement managed-memory path.
- Benchmark against explicit-copy path.

Exit criterion: report contains total, transfer, and kernel breakdown for all
GPU memory modes.

### Milestone 5: Baselines

- Add Arrow/Acero baseline.
- Add cuDF baseline.
- Add DuckDB validation script.

Exit criterion: one command runs all available engines and emits a unified CSV.

### Milestone 6: Report

- Generate plots.
- Explain Q5 filter propagation.
- Explain CPU/GPU placement.
- Discuss PCIe/UVA results.
- Compare with Arrow and cuDF.
- Document hardware limitations.

Exit criterion: repository is buildable, testable, benchmarkable, and reportable.

## 13. Main Risks And Mitigations

| Risk | Mitigation |
| --- | --- |
| Arrow C++ linkage is heavy | Keep custom layout independent; make Arrow baseline optional |
| cuDF install is large | Keep cuDF baseline in a separate Python environment |
| No GPU driver on current machine | Implement CPU and compile-only CUDA here; run GPU benchmarks on target server |
| TPC-H dbgen licensing/distribution | Do not vendor dbgen; provide download/build script and small committed fixtures |
| `UVA` terminology is ambiguous | Report mapped pinned host memory and managed memory separately |
| Floating-point mismatch | Use fixed-point integer revenue internally |
| Full SQL parser would explode scope | Hard-code Q5 physical plan and document the scope |

## 14. Coding Order For The Next Phase

1. Create CMake project and C++17/CUDA targets.
2. Implement `aligned_buffer`, `column`, `bitmap`, and `date`.
3. Add fixture loader and CPU Q5.
4. Add tests and DuckDB validation script.
5. Add CUDA explicit-copy path.
6. Add mapped/managed paths.
7. Add baselines and benchmark scripts.
8. Write final report.

The first coding phase should avoid Arrow and cuDF dependencies. Those should be
added only after the custom CPU/GPU engines are correct.

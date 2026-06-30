# TPC-H Q5 On A Heterogeneous In-Memory Column Store

## Abstract

This project implements a focused in-memory analytical query engine for TPC-H
Q5. The goal is not to build a full SQL DBMS, but to study the core execution
problem discussed in the course project: how a multi-table analytical database
query behaves under CPU execution, handwritten CUDA execution, explicit PCIe
transfer, CUDA managed memory, mapped pinned host memory, and GPU operator
library execution. The current implementation includes a CPU engine, three CUDA
memory-mode engines, correctness baselines, validation scripts, benchmark
automation, environment capture, and report asset generation. Local verification
passes all CPU and CUDA compile-only tests. Runtime GPU evaluation still needs a
server with a working NVIDIA driver.

## 1. Background

Analytical database queries usually scan large columns, apply selective
predicates, join dimension tables with fact tables, and aggregate the result.
This makes them a good workload for studying memory layout, CPU cache behavior,
PCIe transfer overhead, GPU memory bandwidth, and GPU kernel efficiency.

TPC-H Q5, Local Supplier Volume, is a representative query because it joins
`region`, `nation`, `supplier`, `customer`, `orders`, and `lineitem`, then
aggregates revenue by nation. The query is fixed enough to implement directly,
but still realistic enough to exercise multi-table filter propagation and large
fact-table scanning.

## 2. Requirement Reconstruction

The original teacher discussion emphasized the following requirements:

- implement CPU and GPU database query paths,
- compare CPU and GPU behavior,
- compare handwritten CUDA with NVIDIA/GPU operator-library style execution,
- compare explicit PCIe data movement with unified-memory or UVA-style access,
- use TPC-H Q5 as the target scenario,
- use integer date representation and bitmap/filter-style propagation,
- treat small data as CPU-friendly and large data as GPU-friendly.

The project therefore implements one complete physical plan for TPC-H Q5 rather
than a general parser, optimizer, transaction manager, or full SQL engine.

## 3. Data Layout

Only Q5-required columns are loaded:

- `region`: `r_regionkey`, `r_name`
- `nation`: `n_nationkey`, `n_name`, `n_regionkey`
- `supplier`: `s_suppkey`, `s_nationkey`
- `customer`: `c_custkey`, `c_nationkey`
- `orders`: `o_orderkey`, `o_custkey`, `o_orderdate`
- `lineitem`: `l_orderkey`, `l_suppkey`, `l_extendedprice`, `l_discount`

The custom column store uses contiguous fixed-width buffers, 64-byte aligned
allocation, non-owning column views, optional bitmap support, integer date
encoding, string dictionary encoding for low-cardinality names, and fixed-point
integer revenue. This follows the important Arrow-style layout idea without
reimplementing the full Arrow format, IPC metadata, or compute engine.

## 4. Query Plan

The logical SQL is equivalent to TPC-H Q5:

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

The physical plan is a star-join-like filter-propagation pipeline:

1. Find `region_key` from the selected region name.
2. Build `nation_in_region`.
3. Build `supplier_nation_by_key`.
4. Build `customer_nation_by_key`.
5. Build `order_nation_by_key` with the date predicate.
6. Scan `lineitem`, check order/supplier nation equality, and aggregate revenue
   by nation.

This avoids materializing large intermediate six-table join results.

## 5. CPU Implementation

The CPU implementation builds the filter-propagation maps and scans `lineitem`.
It supports `--threads N`. Each worker scans a slice of `lineitem` into a local
revenue array, then the local arrays are reduced into the final result. This
keeps the hot loop simple and avoids atomic updates in the CPU path.

Implemented files:

- `src/engine/q5_plan.cpp`
- `src/cpu/q5_cpu.cpp`
- `src/common/*`
- `src/io/tpch_loader.cpp`

## 6. GPU Implementation

Three handwritten CUDA execution paths are implemented:

- `gpu-copy`: explicit host-to-device copies with `cudaMemcpy`, kernel execution,
  and device-to-host result copy.
- `gpu-managed`: `cudaMallocManaged` buffers with `cudaMemPrefetchAsync`.
- `gpu-mapped`: mapped pinned host buffers created with `cudaHostAllocMapped`,
  accessed by the GPU through device pointers.

All three paths currently reuse the CPU-built filter-propagation maps and run a
CUDA `lineitem` aggregation kernel. This is a correct first GPU milestone. A
later optimization step can move more map construction work to the GPU and
reduce atomic contention in the aggregation kernel.

Implemented files:

- `src/cuda/q5_cuda.cu`
- `src/cuda/q5_cuda.hpp`
- `tests/test_q5_cuda.cpp`

## 7. Baselines

Implemented baselines:

- `python_q5.py`: dependency-free correctness reference.
- `duckdb_q5.py`: SQL baseline when DuckDB is installed.
- `cudf_q5.py`: RAPIDS cuDF baseline when a RAPIDS GPU environment is available.

The cuDF baseline is the main high-level NVIDIA/GPU operator-library comparison
path. The handwritten CUDA implementation is the low-level path.

## 8. Experimental Setup

The project records environment metadata with:

```bash
python3 scripts/capture_environment.py --output results/environment.json
```

The local machine currently has `nvcc`, but `nvidia-smi` fails to communicate
with an NVIDIA driver. Therefore local CUDA testing is compile-only plus runtime
skip behavior. Final GPU results must be collected on a GPU server.

Current local self-check:

```bash
python3 scripts/self_check.py
```

Result: all checks passed, including CPU CTest, CUDA compile-only CTest, data
validation, Python syntax checks, and a tiny end-to-end experiment pipeline.

## 9. Current Results

Tiny fixture correctness:

- CPU result hash: `1e07d78fa8eededb`
- Python reference hash: `1e07d78fa8eededb`
- Result rows:
  - `JAPAN`, `190.00`
  - `INDIA`, `90.00`

Synthetic development data checks:

- `data/synthetic_dev`: CPU and Python reference hashes match.
- `data/synthetic_200k`: CPU thread configurations produce the same hash.

Generated report assets are available under `results/experiments/*/assets/`
after running `scripts/run_experiment_pipeline.py`.

GPU runtime results are not yet available on the local machine because no CUDA
device is visible to the runtime.

## 10. Analysis Plan

The final report should analyze:

- CPU single-thread vs CPU multi-thread behavior,
- CPU vs handwritten CUDA,
- `gpu-copy` vs `gpu-managed` vs `gpu-mapped`,
- handwritten CUDA vs cuDF,
- transfer time vs kernel time,
- when CPU overhead dominates,
- when PCIe transfer dominates,
- whether managed memory prefetch helps,
- whether mapped pinned host memory is slower for repeated or random access,
- how selectivity from `region` and `date` affects the scan.

Expected trend:

- Tiny data should favor CPU because GPU launch and transfer overhead dominate.
- Larger data should favor GPU if transfer and aggregation overheads are
  controlled.
- `gpu-copy` should be strongest when data fits in GPU memory and is reused by
  the kernel.
- `gpu-mapped` may reduce explicit copy time but can be slower because GPU reads
  cross PCIe.
- `gpu-managed` may simplify programming, but page migration and prefetch cost
  must be measured.

## 11. Completion Checklist

Completed:

- CPU engine.
- Multi-thread CPU scan.
- TPC-H Q5 loader.
- Tiny deterministic fixture.
- Python correctness reference.
- DuckDB and cuDF baseline scripts.
- CUDA `gpu-copy`, `gpu-managed`, and `gpu-mapped` compile paths.
- CUDA runtime test with skip behavior when no GPU exists.
- Data validation.
- Environment capture.
- Benchmark runner.
- Hash verifier.
- Summary and SVG report assets.
- One-command experiment pipeline.
- Local self-check.

Still required for final submission:

- Run GPU runtime tests on a machine with a working NVIDIA driver.
- Run final experiments on official TPC-H dbgen data.
- Run RAPIDS cuDF baseline in a RAPIDS environment.
- Insert final GPU tables and figures into this report.
- Write the final conclusion from measured data rather than expected trends.

## References

- TPC-H specification.
- Apache Arrow columnar format documentation.
- CUDA Programming Guide.
- RAPIDS cuDF documentation.
- DuckDB vectorized execution documentation.
- Crystal GPU query implementation reference: `https://github.com/anilshanbhag/crystal`

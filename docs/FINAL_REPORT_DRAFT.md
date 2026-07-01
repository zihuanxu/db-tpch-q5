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
passes all CPU and CUDA compile-only tests. GPU runtime validation was completed
on an NVIDIA GPU server on 2026-07-01. Official TPC-H scale-factor experiments
still require an already downloaded, license-accepted TPC-H dbgen output
directory.

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

GPU server validation was run on 2026-07-01 with `CUDA_VISIBLE_DEVICES=0` so
that the experiments used an idle RTX 4090 instead of the L20 devices that were
already occupied by another process.

Environment summary:

- OS/kernel: Ubuntu Linux, kernel `6.17.0-29-generic`.
- CPU: 2 sockets, AMD EPYC 9654, 384 logical CPUs.
- GPU inventory: six NVIDIA GeForce RTX 4090 GPUs and two NVIDIA L20 GPUs.
- Test GPU: GPU 0, NVIDIA GeForce RTX 4090, compute capability 8.9,
  24 GiB device memory.
- Driver/runtime from `nvidia-smi`: driver `595.71.05`, CUDA runtime `13.2`.
- `nvcc --version` on `PATH`: CUDA `12.6`, `V12.6.85`.
- CMake CUDA compiler selected by configure: `/usr/bin/nvcc`, CUDA `12.0.140`.
- CMake: `4.3.0`.
- Python: `3.11.15`.
- RAPIDS cuDF: not installed in the active Python environment.

The CUDA build used:

```bash
cmake -S . -B build-cuda \
  -DMEMQ5_ENABLE_CUDA=ON \
  -DMEMQ5_ENABLE_TESTS=ON \
  -DCMAKE_CUDA_ARCHITECTURES=89
cmake --build build-cuda
CUDA_VISIBLE_DEVICES=0 ctest --test-dir build-cuda --output-on-failure
```

`ctest` passed all six tests. The CUDA test did not skip: `test_q5_cuda` ran and
passed on the server GPU.

The official TPC-H dbgen SF1 experiment was not run in this checkout because no
official dbgen `.tbl` output, `dbgen` executable, or TPC-H tools zip was present
under the checked local data locations. The official TPC tools download page
requires registration and agreement to the license terms before use, so the
tools were not downloaded automatically during this run.

## 9. Measured Results

### 9.1 Tiny GPU Correctness Fixture

Command:

```bash
CUDA_VISIBLE_DEVICES=0 python3 scripts/run_experiment_pipeline.py \
  --name tiny_gpu_modes \
  --memq5 build-cuda/memq5 \
  --data-dir tests/fixtures/tpch_q5_tiny \
  --engines cpu,gpu-copy,gpu-managed,gpu-mapped,python \
  --repeat 5 \
  --force
```

Hash check:

```text
ok ASIA 1994-01-01 hash=1e07d78fa8eededb engines=cpu,gpu-copy,gpu-managed,gpu-mapped,python
```

Median timing table:

The `threads` column is the `--threads` value passed by the benchmark driver to
the C++ `memq5` executable. It controls CPU worker count for the `cpu` engine.
For the current GPU engines, the CUDA kernel uses a fixed 256-thread block size
and does not use this CLI value; GPU rows with different `threads` values in a
thread-list sweep are retained only so the C++ benchmark matrix lines up with
the CPU sweep.

| engine | threads | runs | hash | total_ms_median | scan_ms_median | h2d_ms_median | kernel_ms_median | d2h_ms_median | elapsed_ms_median |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| cpu | 1 | 5 | `1e07d78fa8eededb` | 0.012799 | 0.001673 | 0.000000 | 0.000000 | 0.000000 | 2.917052 |
| gpu-copy | 1 | 5 | `1e07d78fa8eededb` | 188.151000 | 0.171040 | 0.043008 | 0.108672 | 0.017664 | 256.674921 |
| gpu-managed | 1 | 5 | `1e07d78fa8eededb` | 188.922000 | 0.503456 | 0.117760 | 0.105472 | 0.266976 | 240.041216 |
| gpu-mapped | 1 | 5 | `1e07d78fa8eededb` | 187.576000 | 0.125920 | 0.007040 | 0.097984 | 0.022624 | 255.451920 |
| python | 1 | 5 | `1e07d78fa8eededb` | 0.230724 | 0.230724 | 0.000000 | 0.000000 | 0.000000 | 38.741158 |

Figures:

![Tiny GPU total time](assets/tiny_gpu_modes_total_time.svg)

![Tiny GPU time breakdown](assets/tiny_gpu_modes_time_breakdown.svg)

### 9.2 Synthetic GPU Development Experiment

This experiment uses deterministic generated Q5-shaped data, not official TPC-H
dbgen data. It is useful for GPU runtime and memory-mode trend checks only.

Data size:

- `region.tbl`: 5 rows
- `nation.tbl`: 25 rows
- `supplier.tbl`: 5,000 rows
- `customer.tbl`: 10,000 rows
- `orders.tbl`: 50,000 rows
- `lineitem.tbl`: 200,000 rows
- orders in the `1994-01-01` to `1995-01-01` date window: 34,286

Command:

```bash
python3 scripts/generate_synthetic_tpch_q5.py \
  --output data/synthetic_gpu_dev \
  --customers 10000 \
  --orders 50000 \
  --lineitems 200000 \
  --suppliers 5000 \
  --asia-heavy

CUDA_VISIBLE_DEVICES=0 python3 scripts/run_experiment_pipeline.py \
  --name synthetic_gpu_modes \
  --memq5 build-cuda/memq5 \
  --data-dir data/synthetic_gpu_dev \
  --engines cpu,gpu-copy,gpu-managed,gpu-mapped,python \
  --thread-list 1,2,4,8 \
  --repeat 5 \
  --force
```

Hash check:

```text
ok ASIA 1994-01-01 hash=d5ffe393223a207e engines=cpu,gpu-copy,gpu-managed,gpu-mapped,python
```

Median timing table:

The `threads` column has the same meaning as in the tiny experiment: it is a
C++ benchmark-driver parameter, not a CUDA launch-configuration field. Only the
CPU engine uses it to change host worker count. The GPU implementations build
the CPU-side Q5 filter maps once per process invocation and launch a fixed
CUDA kernel configuration, so the GPU `threads=1,2,4,8` rows should be read as
repeat measurements under the same GPU execution policy.

| engine | threads | runs | hash | total_ms_median | scan_ms_median | h2d_ms_median | kernel_ms_median | d2h_ms_median | elapsed_ms_median |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| cpu | 1 | 5 | `d5ffe393223a207e` | 5.282640 | 3.517350 | 0.000000 | 0.000000 | 0.000000 | 536.606586 |
| cpu | 2 | 5 | `d5ffe393223a207e` | 3.919480 | 2.153160 | 0.000000 | 0.000000 | 0.000000 | 536.685224 |
| cpu | 4 | 5 | `d5ffe393223a207e` | 3.092000 | 1.349080 | 0.000000 | 0.000000 | 0.000000 | 537.803456 |
| cpu | 8 | 5 | `d5ffe393223a207e` | 2.970280 | 1.217070 | 0.000000 | 0.000000 | 0.000000 | 538.073060 |
| gpu-copy | 1 | 5 | `d5ffe393223a207e` | 199.650000 | 0.521152 | 0.378944 | 0.122880 | 0.015712 | 796.141558 |
| gpu-copy | 2 | 5 | `d5ffe393223a207e` | 189.160000 | 0.523936 | 0.371712 | 0.131808 | 0.016384 | 787.868087 |
| gpu-copy | 4 | 5 | `d5ffe393223a207e` | 186.583000 | 0.516800 | 0.374080 | 0.126976 | 0.015744 | 794.882644 |
| gpu-copy | 8 | 5 | `d5ffe393223a207e` | 186.327000 | 0.518624 | 0.372736 | 0.126976 | 0.015840 | 788.315773 |
| gpu-managed | 1 | 5 | `d5ffe393223a207e` | 187.450000 | 0.998400 | 0.799744 | 0.129024 | 0.066560 | 788.664830 |
| gpu-managed | 2 | 5 | `d5ffe393223a207e` | 193.684000 | 1.000450 | 0.814080 | 0.129280 | 0.056224 | 786.843560 |
| gpu-managed | 4 | 5 | `d5ffe393223a207e` | 193.003000 | 1.010620 | 0.827072 | 0.137216 | 0.056320 | 784.143603 |
| gpu-managed | 8 | 5 | `d5ffe393223a207e` | 187.830000 | 1.048380 | 0.833536 | 0.141216 | 0.063488 | 788.378766 |
| gpu-mapped | 1 | 5 | `d5ffe393223a207e` | 193.243000 | 0.469728 | 0.006144 | 0.442368 | 0.022592 | 794.352286 |
| gpu-mapped | 2 | 5 | `d5ffe393223a207e` | 193.769000 | 0.470560 | 0.008192 | 0.444416 | 0.020864 | 796.128510 |
| gpu-mapped | 4 | 5 | `d5ffe393223a207e` | 194.512000 | 0.481696 | 0.006144 | 0.455424 | 0.022400 | 796.462307 |
| gpu-mapped | 8 | 5 | `d5ffe393223a207e` | 195.000000 | 0.497344 | 0.007904 | 0.463872 | 0.023872 | 796.575033 |
| python | 1 | 5 | `d5ffe393223a207e` | 477.305890 | 477.305890 | 0.000000 | 0.000000 | 0.000000 | 516.687679 |

Figures:

![Synthetic GPU total time](assets/synthetic_gpu_modes_total_time.svg)

![Synthetic GPU time breakdown](assets/synthetic_gpu_modes_time_breakdown.svg)

### 9.3 Official TPC-H SF1 Status

Formal SF1 results are not available in this run. The repository expects an
existing official dbgen output directory and then runs:

```bash
python3 scripts/prepare_tpch_q5_data.py \
  --source-dir /path/to/dbgen-output \
  --output-dir data/tpch_sf1 \
  --scale-factor 1 \
  --mode copy \
  --force
```

No such directory was available on the server, and no RAPIDS/cuDF package was
installed. Therefore the cuDF baseline was also not run.

## 10. Analysis

The GPU server work closes the main runtime-correctness gap. `test_q5_cuda`,
the tiny fixture experiment, and the synthetic development experiment all ran
on a real NVIDIA GPU. All successful CPU, GPU, and Python rows produced the
same result hash within each experiment.

The tiny fixture confirms the expected behavior for very small data. CPU is
much faster because GPU launch, context, and transfer overheads dominate the
actual useful work. The median CPU total time is about `0.013 ms`, while GPU
total time is about `188 ms` even though the measured GPU kernel is only about
`0.10 ms`.

The synthetic development experiment shows the same fixed-overhead issue at
200,000 lineitems. CPU improves from `5.28 ms` at one thread to `2.97 ms` at
eight threads. The Python baseline is much slower at `477.31 ms`, which is
expected for a dependency-free row-processing reference. The handwritten CUDA
kernel and transfer components are small, but the total GPU query time remains
around `186 ms` to `195 ms`. This means the current GPU path is functionally
correct but not yet optimized as a full end-to-end query engine.

The memory modes behave as expected at the component level:

- `gpu-copy` has explicit host-to-device copies around `0.37 ms` and kernels
  around `0.13 ms` on the synthetic data.
- `gpu-managed` is simpler to program but costs more in the measured migration
  or prefetch component, around `0.8 ms`.
- `gpu-mapped` nearly eliminates explicit copy time, but the kernel is slower,
  around `0.44 ms`, because the GPU reads mapped host memory through the host
  interconnect.

The remaining performance question is scale. These results validate the CUDA
implementation and show the overhead structure, but they do not prove the final
large-data crossover point. That requires official TPC-H dbgen SF1 or larger
data generated from the licensed TPC tools.

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
- GPU server environment validation with `nvidia-smi`, `nvcc`, CMake, and
  Python.
- CUDA build on RTX 4090-class architecture `89`.
- GPU CTest runtime validation on a real NVIDIA GPU.
- Tiny CPU/GPU/Python correctness experiment with matching result hashes.
- Synthetic GPU development experiment with matching CPU/GPU/Python result
  hashes.

Still required for final submission:

- Provide a license-accepted official TPC-H dbgen output directory and run the
  final SF1 experiment.
- Install RAPIDS/cuDF, or document that the target environment does not include
  RAPIDS, before making the handwritten CUDA vs cuDF comparison.
- Replace the formal SF1 placeholder above with measured official-data tables
  and figures.

## References

- TPC-H specification.
- TPC current specifications page: `https://www.tpc.org/tpc_documents_current_versions/current_specifications5.asp`
- TPC-H tools download request page: `https://www.tpc.org/tpc_documents_current_versions/download_programs/tools-download-request5.asp?bm_type=TPC-H&bm_vers=3.0.1&mode=CURRENT-ONLY`
- Apache Arrow columnar format documentation.
- CUDA Programming Guide.
- RAPIDS cuDF documentation.
- DuckDB vectorized execution documentation.
- Crystal GPU query implementation reference: `https://github.com/anilshanbhag/crystal`

# Report Template

# TPC-H Q5 On A Heterogeneous In-Memory Column Store

## Abstract

Summarize the goal, implementation, and main result in 150-250 words.

## 1. Background

Explain:

- in-memory analytical query processing,
- columnar layout,
- Arrow-style buffers,
- TPC-H Q5,
- CPU/GPU heterogeneous execution,
- PCIe transfer and UVA-style access.

## 2. Requirement Reconstruction

Describe how the project was derived from the original notes:

- CPU and GPU database implementation,
- Apache Arrow comparison,
- UVA and PCIe comparison,
- NVIDIA-native comparison,
- TPC-H Q5,
- bitmap/date/region/nation filter design,
- small data on CPU and large data on GPU.

## 3. Data Layout

Describe:

- loaded TPC-H columns,
- integer date encoding,
- string dictionary encoding,
- fixed-point revenue representation,
- Arrow-compatible buffer layout,
- validity bitmaps.

## 4. Query Plan

Show the SQL for Q5 and the physical plan:

1. `region -> nation` filter.
2. `supplier -> nation` map.
3. `customer -> nation` map.
4. `orders -> nation` map with date predicate.
5. `lineitem` scan and revenue aggregation.

Explain why this is a star-join-like filter propagation plan.

## 5. CPU Implementation

Cover:

- parallel map building,
- parallel lineitem scan,
- per-thread revenue arrays,
- reduction,
- memory footprint.

## 6. GPU Implementation

Cover:

- explicit-copy path,
- mapped pinned UVA-style path,
- managed-memory path,
- kernels,
- aggregation strategy,
- timing instrumentation.

## 7. Baselines

Cover:

- Apache Arrow/Acero,
- RAPIDS cuDF,
- DuckDB validation if used.

## 8. Experimental Setup

Report:

- CPU/GPU hardware,
- memory and NUMA information,
- CUDA/compiler versions,
- data scale factors,
- repeat count,
- benchmark methodology.

## 9. Results

Include:

- correctness table,
- total runtime,
- throughput,
- GPU timing breakdown,
- transfer mode comparison,
- baseline comparison,
- memory usage.

## 10. Analysis

Discuss:

- why CPU or GPU wins at each scale,
- transfer overhead vs kernel speed,
- mapped/UVA-style behavior,
- managed memory prefetch behavior,
- bitmap/filter selectivity,
- Arrow/cuDF overheads and advantages,
- limitations.

## 11. Conclusion

State what was implemented, what was learned, and what could be improved.

## References

Include:

- TPC-H specification,
- Apache Arrow columnar format,
- Apache Arrow Acero documentation,
- CUDA programming guide,
- RAPIDS cuDF documentation,
- DuckDB vectorized execution documentation if cited.

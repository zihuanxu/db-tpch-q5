# Technical Decisions

## 1. Build A Specialized Engine, Not A Full SQL DBMS

Decision:

Implement one complete physical plan for TPC-H Q5 instead of a parser,
optimizer, and general execution engine.

Reason:

The course requirement is about in-memory hardware-aware query processing. A
single complete OLAP query with CPU/GPU execution and serious measurement gives
more useful evidence than a shallow general database.

## 2. Use Arrow-Compatible Layout, Not Full Arrow Reimplementation

Decision:

The custom engine uses contiguous aligned buffers, validity bitmaps, and
fixed-width column views inspired by Arrow. Full Arrow metadata, IPC, nested
arrays, and compute kernels are not reimplemented.

Reason:

This preserves the important memory-layout idea while keeping scope realistic.
Apache Arrow/Acero remains a comparison baseline.

## 3. Encode Dates As Integer Days

Decision:

Dates are parsed once at load time and stored as integer day offsets.

Reason:

TPC-H Q5's date predicate is a half-open one-year range, so integer comparison is
the fastest and simplest representation.

## 4. Encode Names With Dictionaries

Decision:

`region` and `nation` names are dictionary encoded. Execution uses integer keys;
strings are only needed at load/output boundaries.

Reason:

Q5 joins on integer keys and only groups by at most 25 nation names. String
processing inside the hot loop would only add noise.

## 5. Use Filter Propagation Maps

Decision:

Build direct-address maps:

- `supplier_nation_by_key`
- `customer_nation_by_key`
- `order_nation_by_key`

Reason:

TPC-H keys are dense enough at benchmark scales for direct arrays to be simple
and fast. This expresses the note's bitmap/star-join idea without materializing
large intermediate joins.

## 6. Use Fixed-Point Revenue

Decision:

Store and aggregate revenue as integers.

Reason:

Fixed-point arithmetic makes CPU, GPU, DuckDB, Arrow, and cuDF validation easier.
It avoids small floating-point differences becoming a distraction.

## 7. Separate Mapped Pinned Memory From Managed Memory

Decision:

The plan treats these as separate experiments:

- mapped pinned host memory: UVA-style zero-copy access,
- managed memory: page migration with optional prefetch.

Reason:

The phrase "UVA" is often used imprecisely. Separating these modes makes the
benchmark scientifically cleaner.

## 8. Make Arrow And cuDF Optional Dependencies

Decision:

The custom CPU engine must build without Arrow, cuDF, DuckDB, or a working GPU
driver.

Reason:

Optional baselines are valuable, but they should not prevent the core project
from compiling and being tested.

## 9. Keep GPU Runtime Tests Skippable

Decision:

CUDA compilation can be tested where `nvcc` exists, but runtime tests are skipped
when no NVIDIA driver is available.

Reason:

The current machine has `nvcc` but `nvidia-smi` cannot communicate with a driver.
The repository should still be developable here and benchmarkable later on a GPU
server.

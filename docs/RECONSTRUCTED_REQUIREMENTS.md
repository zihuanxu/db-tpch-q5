# Reconstructed Requirements

Source note: `/home/xuzihuan/内存数据库大作业要求.md`

The original note is fragmentary and partly based on memory. This file separates
hard requirements, likely teacher intent, and engineering assumptions.

## Hard Requirements From The Note

1. Implement an in-memory database/query-processing project with both CPU and
   GPU paths.
2. Use or mimic Apache Arrow's columnar in-memory representation.
3. Implement two GPU data-access/transfer schemes and compare them:
   explicit PCIe transfer and a UVA-style scheme.
4. Compare the custom implementation with Apache Arrow and NVIDIA-native
   implementations.
5. Use TPC-H Q5 as the target query scenario.
6. Represent `date` as an integer offset. Date predicates should be cheap
   integer range filters.
7. Use bitmap-style filters where appropriate.
8. Treat `region` and `nation` as low-cardinality dimensions. A region maps to
   a small set of nations, so region/nation filters can be represented as small
   arrays or bitmaps.
9. The query contains hierarchical propagation across multiple tables. The
   bitmap/filter propagation and joins are a star-join-like variant.
10. Run small-data work on CPU when it is cheaper, and large-data work on GPU.

## Clarified Target Query

The target query is TPC-H Q5, "Local Supplier Volume". Its required tables are:

- `region`
- `nation`
- `supplier`
- `customer`
- `orders`
- `lineitem`

The core predicate is:

```sql
c_custkey = o_custkey
and l_orderkey = o_orderkey
and l_suppkey = s_suppkey
and c_nationkey = s_nationkey
and s_nationkey = n_nationkey
and n_regionkey = r_regionkey
and r_name = :region
and o_orderdate >= :date
and o_orderdate < :date + interval '1' year
```

The output is:

```sql
n_name, sum(l_extendedprice * (1 - l_discount)) as revenue
group by n_name
order by revenue desc
```

## Interpretations And Assumptions

1. "Apache Arrow implementation" should mean two things:
   - The custom engine stores columns in an Arrow-compatible layout for
     primitive columns: contiguous buffers, optional validity bitmaps, and
     aligned allocation.
   - Apache Arrow C++/Acero is used as a comparison baseline when available.
2. "NVIDIA native implementation" should mean RAPIDS cuDF as the high-level GPU
   DataFrame baseline, plus the custom CUDA kernels as the low-level native
   implementation.
3. "UVA scheme" is ambiguous. CUDA Unified Virtual Addressing is an address-space
   feature, not automatically a high-performance data-transfer method. The
   implementable experiment should include:
   - explicit `cudaMemcpyAsync` to device memory,
   - mapped pinned host memory via `cudaHostAllocMapped`/`cudaHostGetDevicePointer`
     as a zero-copy UVA-style path,
   - optionally `cudaMallocManaged` with and without prefetch as a managed-memory
     variant.
4. The project should not attempt to implement a full SQL database. It should
   implement a complete, explainable, reproducible query engine for one
   representative OLAP query, because that matches the course's hardware-aware
   in-memory database theme.
5. Strings should be dictionary encoded in the execution engine. `region` and
   `nation` names only need string dictionaries at load/output boundaries; joins
   use integer keys.
6. The project should use official TPC-H dbgen data for final benchmark runs, but
   should also include tiny deterministic fixtures so tests do not depend on
   downloading dbgen.

## External Evidence Used

- Apache Arrow columnar format is a language-agnostic in-memory columnar format
  with contiguous buffers, alignment guidance, zero-copy-oriented layout, and
  vectorization-friendly fixed-width arrays:
  https://arrow.apache.org/docs/format/Columnar.html
- Apache Arrow C++ includes Acero, a streaming execution engine built on Arrow
  compute kernels and execution plans:
  https://arrow.apache.org/docs/cpp/acero.html
- TPC-H Q5 is officially the Local Supplier Volume query over customer, orders,
  lineitem, supplier, nation, and region:
  https://www.tpc.org/TPC_Documents_Current_Versions/pdf/TPC-H_v3.0.1.pdf
- CUDA documents distinguish unified virtual addressing/unified memory from
  explicit memory management, so the project must measure transfer modes rather
  than assume UVA is faster:
  https://docs.nvidia.com/cuda/cuda-programming-guide/
- RAPIDS cuDF is a GPU DataFrame library built on Arrow-style columnar memory and
  supports loading, joining, aggregating, and filtering:
  https://docs.rapids.ai/api/cudf/stable/
- cuDF exposes database-style merge/join and groupby aggregation APIs useful for
  a baseline:
  https://docs.rapids.ai/api/cudf/stable/user_guide/api_docs/api/cudf.merge/
  https://docs.rapids.ai/api/cudf/stable/user_guide/groupby/
- DuckDB's documented vectorized execution model is a useful CPU-design reference
  and optional validation baseline:
  https://duckdb.org/docs/stable/internals/vector

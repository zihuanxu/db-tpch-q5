# GPU Memory Modes, Hybrid Execution, And cuDF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute exact Arrow-backed TPC-H Q5 through explicit-copy, managed, mapped, truly concurrent CPU-GPU hybrid, and cuDF paths.

**Architecture:** Shared Arrow chunk views and a CPU-built Q5 plan feed three explicit CUDA memory strategies. An asynchronous copy engine returns an owning handle so hybrid execution can overlap CUDA stream work with a specialized CPU scan over disjoint batch slices; cuDF consumes the same Arrow IPC tables through `cudf.from_arrow`.

**Tech Stack:** C++17, CUDA 12.x, Apache Arrow CUDA 23.0.1, cuDF 26.06.0, PyArrow 23.0.1, CTest, pytest, compute-sanitizer, Nsight Systems/Compute.

## Global Constraints

- Plan 1 acceptance must pass before this plan changes GPU code.
- GPU kernels accumulate exact scale-4 `int64` revenue and match CPU exact hashes.
- `gpu-copy` allocations are owned by Arrow CUDA buffers; asynchronous transfers use their device addresses with explicit lifetime ownership.
- `gpu-managed` uses `cudaMallocManaged`; CPU fill, prefetch/page migration, kernel, and return migration are separately timed.
- `gpu-mapped` uses `cudaHostAllocMapped`; Arrow-to-pinned preparation and remote PCIe kernel reads are separately timed.
- Hybrid CPU and GPU input slices are disjoint and cover every lineitem row exactly once.
- Hybrid execution claims require an Nsight Systems trace showing CPU scan overlap with H2D or kernel work.
- CUDA errors, unsupported devices, OOM, and no-GPU cases are distinct statuses; only no-GPU test discovery may report `SKIPPED_NO_GPU`.
- The main hybrid experiment uses explicit copy and ratios 75/25, 50/50, and 25/75.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `src/cuda/cuda_status.hpp` | CUDA-to-Arrow error conversion and device capability checks. |
| `src/cuda/cuda_raii.hpp` | Events, streams, managed/pinned allocations, and nonthrowing destructors. |
| `src/cuda/arrow_cuda_buffer.hpp/.cu` | Arrow CUDA allocation plus asynchronous copy ownership. |
| `src/cuda/q5_cuda_kernel.cuh/.cu` | Shared exact lineitem aggregation kernel and launch configuration. |
| `src/cuda/q5_gpu_executor.hpp/.cu` | Copy/managed/mapped sessions and asynchronous copy handle. |
| `src/engine/batch_slice.hpp` | Shared chunk/offset/length slice value type. |
| `src/hybrid/batch_partition.hpp/.cpp` | Deterministic lineitem batch slicing by CPU/GPU ratio. |
| `src/hybrid/q5_hybrid.hpp/.cu` | Concurrent CPU scan, CUDA work, merge, and timing. |
| `baselines/cudf_q5.py` | cuDF query over PyArrow IPC tables via `cudf.from_arrow`. |
| `baselines/arrow_dataset.py` | Shared Python loader/schema/checksum validation. |
| `tests/test_cuda_memory.cpp` | Allocation, copy, lifetime, and failure tests. |
| `tests/test_q5_cuda.cpp` | Exact tiny results for all three CUDA modes. |
| `tests/test_batch_partition.cpp` | Coverage/disjointness/ratio tests. |
| `tests/test_q5_hybrid.cpp` | Exact hybrid results and deterministic ratios. |
| `tests/python/test_cudf_q5.py` | Same-Arrow-input cuDF correctness and skip behavior. |

## Task 1: Introduce CUDA Error And Ownership Primitives

**Files:**
- Create: `src/cuda/cuda_status.hpp`
- Create: `src/cuda/cuda_raii.hpp`
- Create: `src/cuda/arrow_cuda_buffer.hpp`
- Create: `src/cuda/arrow_cuda_buffer.cu`
- Create: `tests/test_cuda_memory.cpp`
- Modify: `CMakeLists.txt`
- Modify: `tests/CMakeLists.txt`

**Interfaces:**
- Consumes: Arrow CUDA `CudaContext`, CUDA runtime, and immutable Arrow host buffers.
- Produces: `CudaStatus`, `CudaStream`, `CudaEvent`, `ArrowCudaAllocation`, `ManagedAllocation<T>`, and `MappedHostAllocation<T>`.

```cpp
arrow::Status CudaStatus(cudaError_t status, std::string_view operation);
arrow::Result<std::shared_ptr<arrow::cuda::CudaContext>> GetArrowCudaContext(
    int device_id);

class ArrowCudaAllocation {
 public:
  static arrow::Result<ArrowCudaAllocation> Allocate(
      const std::shared_ptr<arrow::cuda::CudaContext>& context,
      int64_t bytes);
  uint8_t* mutable_device_data();
  const uint8_t* device_data() const;
  int64_t size() const;
  std::shared_ptr<arrow::cuda::CudaBuffer> owner() const;
  arrow::Status CopyFromHostAsync(const void* source, int64_t bytes,
                                  cudaStream_t stream);
  arrow::Status CopyToHostAsync(void* destination, int64_t bytes,
                                cudaStream_t stream) const;
};

template <typename T>
class ManagedAllocation {
 public:
  static arrow::Result<ManagedAllocation<T>> Allocate(int64_t elements);
  T* data();
  int64_t size() const;
};

template <typename T>
class MappedHostAllocation {
 public:
  static arrow::Result<MappedHostAllocation<T>> Allocate(int64_t elements);
  T* host_data();
  T* device_data();
  int64_t size() const;
};
```

- [ ] **Step 1: Write allocation, async-copy, and no-device tests**

```cpp
int main() {
  int count = 0;
  const cudaError_t status = cudaGetDeviceCount(&count);
  if (status != cudaSuccess || count == 0) {
    std::cout << "SKIPPED_NO_GPU\n";
    return 77;
  }
  auto context = memq5::GetArrowCudaContext(0).ValueOrDie();
  auto allocation = memq5::ArrowCudaAllocation::Allocate(context, 4).ValueOrDie();
  const uint32_t input = 0x12345678;
  uint32_t output = 0;
  memq5::CudaStream stream;
  assert(allocation.CopyFromHostAsync(&input, sizeof(input), stream.get()).ok());
  assert(allocation.CopyToHostAsync(&output, sizeof(output), stream.get()).ok());
  assert(stream.Synchronize().ok());
  assert(output == input);
  return 0;
}
```

Configure CTest `SKIP_RETURN_CODE 77` only for hardware absence.

- [ ] **Step 2: Verify the test fails before primitives exist**

Run:

```bash
cmake --preset gpu-release
cmake --build --preset gpu-release -j
ctest --preset gpu-release -R test_cuda_memory --output-on-failure
```

Expected: build fails on missing ownership types.

- [ ] **Step 3: Implement Arrow-status CUDA checks and RAII**

All constructors use static `Create` methods when allocation can fail. Destructors call `cudaFree`, `cudaFreeHost`, `cudaEventDestroy`, or `cudaStreamDestroy` without throwing. Move operations transfer ownership and null the source.

`GetArrowCudaContext(0)` must call `arrow::cuda::CudaDeviceManager::Instance()->GetContext(0)` and return an Arrow error containing both device id and operation on failure.

- [ ] **Step 4: Implement Arrow CUDA allocation and asynchronous copy**

Allocate through the Arrow CUDA context. Enqueue `cudaMemcpyAsync` against `CudaBuffer::mutable_data()` and retain the shared `CudaBuffer` until stream completion. Validate copy bounds and null pointers before calling CUDA.

- [ ] **Step 5: Run memory tests and inspect linkage**

Run:

```bash
ctest --preset gpu-release -R test_cuda_memory --output-on-failure
ldd build/gpu-release/tests/test_cuda_memory
```

Expected: test passes on GPU; linkage includes Arrow CUDA and CUDA runtime.

- [ ] **Step 6: Commit CUDA ownership primitives**

```bash
git add CMakeLists.txt tests/CMakeLists.txt src/cuda/cuda_status.hpp src/cuda/cuda_raii.hpp src/cuda/arrow_cuda_buffer.hpp src/cuda/arrow_cuda_buffer.cu tests/test_cuda_memory.cpp
git commit -m "feat: add Arrow CUDA buffer ownership"
```

## Task 2: Extract One Exact CUDA Aggregation Kernel

**Files:**
- Create: `src/cuda/q5_cuda_kernel.cuh`
- Create: `src/cuda/q5_cuda_kernel.cu`
- Create: `tests/test_q5_cuda_kernel.cu`
- Modify: `CMakeLists.txt`
- Modify: `tests/CMakeLists.txt`

**Interfaces:**
- Consumes: Flat device pointers for a lineitem slice and CPU-prepared order/supplier nation maps.
- Produces: `LaunchQ5LineitemKernel(const Q5DeviceInputs&, cudaStream_t) -> arrow::Status`.

```cpp
struct Q5DeviceInputs {
  const int32_t* orderkey;
  const int32_t* suppkey;
  const int64_t* extendedprice_cents;
  const int64_t* discount_hundredths;
  int64_t row_count;
  const int32_t* order_nation_by_key;
  int64_t order_map_size;
  const int32_t* supplier_nation_by_key;
  int64_t supplier_map_size;
  unsigned long long* revenue_1e4_by_nation;
  unsigned long long* matched_rows;
  int32_t nation_count;
};
```

- [ ] **Step 1: Write kernel edge tests**

Test zero rows, invalid order/supplier keys, nation mismatch, exact fractional-cent accumulation, and two accepted rows. The expected device revenue for a price of `123.45` and discount `0.06` is `1,160,430` scale-4 units.

- [ ] **Step 2: Run and verify the kernel test fails**

Run:

```bash
cmake --build --preset gpu-release -j
ctest --preset gpu-release -R test_q5_cuda_kernel --output-on-failure
```

Expected: build fails because `LaunchQ5LineitemKernel` is undefined.

- [ ] **Step 3: Implement exact device arithmetic and block-local aggregation**

Validate `discount_hundredths` in `[0,100]`. Compute:

```cpp
const unsigned long long revenue =
    static_cast<unsigned long long>(extendedprice_cents[idx]) *
    static_cast<unsigned long long>(100 - discount_hundredths[idx]);
```

Accumulate into a shared-memory nation array per block and then into global output to reduce global atomic contention. Use a separate atomic counter for matched rows. Reject launch dimensions that exceed device/shared-memory limits before launch.

- [ ] **Step 4: Run the kernel tests and compute-sanitizer**

Run:

```bash
ctest --preset gpu-release -R test_q5_cuda_kernel --output-on-failure
compute-sanitizer --tool memcheck build/gpu-release/tests/test_q5_cuda_kernel
```

Expected: test passes; compute-sanitizer reports zero errors.

- [ ] **Step 5: Commit the shared exact kernel**

```bash
git add CMakeLists.txt tests/CMakeLists.txt src/cuda/q5_cuda_kernel.cuh src/cuda/q5_cuda_kernel.cu tests/test_q5_cuda_kernel.cu
git commit -m "feat: add exact CUDA Q5 aggregation kernel"
```

## Task 3: Implement `gpu-copy` From Arrow Buffers

**Files:**
- Create: `src/engine/batch_slice.hpp`
- Create: `src/cuda/q5_gpu_executor.hpp`
- Create: `src/cuda/q5_gpu_executor.cu`
- Modify: `src/cuda/q5_cuda.hpp`
- Replace: `src/cuda/q5_cuda.cu`
- Modify: `tests/test_q5_cuda.cpp`
- Modify: `src/cli/memq5.cpp`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Consumes: `ArrowTpchDataset`, `Q5PreparedPlan`, and optional lineitem `BatchSlice` list.
- Produces: synchronous `execute_q5_gpu_copy` and asynchronous `StartQ5GpuCopy`.

```cpp
struct BatchSlice {
  int chunk_index;
  int64_t offset;
  int64_t length;
};

struct Q5GpuPartial {
  std::vector<int64_t> revenue_1e4_by_nation;
  int64_t input_rows;
  int64_t matched_rows;
  int64_t h2d_bytes;
  int64_t d2h_bytes;
  double host_prepare_ms;
  double h2d_ms;
  double gpu_kernel_ms;
  double d2h_ms;
};

class Q5GpuExecutionHandle {
 public:
  Q5GpuExecutionHandle(Q5GpuExecutionHandle&&) noexcept;
  arrow::Result<Q5GpuPartial> Finish();
 private:
  std::shared_ptr<void> owned_state_;
};

arrow::Result<Q5GpuExecutionHandle> StartQ5GpuCopy(
    const ArrowTpchDataset& dataset,
    const Q5PreparedPlan& plan,
    const std::vector<BatchSlice>& slices,
    int64_t gpu_chunk_rows,
    cudaStream_t stream);
```

- [ ] **Step 1: Extend tiny CUDA tests to exact Arrow input**

Load the Arrow fixture, run specialized CPU and `gpu-copy`, compare exact hashes, assert `h2d_bytes > 0`, `d2h_bytes > 0`, `gpu_kernel_ms >= 0`, and matched-row equality. Add a two-chunk input test and a zero-length slice.

- [ ] **Step 2: Verify the old custom-store GPU path fails the new test**

Run:

```bash
cmake --build --preset gpu-release -j
ctest --preset gpu-release -R test_q5_cuda --output-on-failure
```

Expected: build fails because the old function accepts `TpchDatabase` and truncates revenue.

- [ ] **Step 3: Stage Arrow Decimal128 chunks into pinned scale-2 arrays**

For each selected slice, validate chunk types and nulls, copy primitive keys directly, convert checked Decimal128 raw values into contiguous `int64_t` pinned arrays, and record `host_prepare_ms`. Reuse staging buffers in resident sessions when row capacity and schema fingerprint match.

- [ ] **Step 4: Allocate Arrow CUDA buffers and enqueue work**

Allocate keys, decimals, plan maps, result array, and matched counter through `ArrowCudaAllocation`. Enqueue H2D copies, zeroing, kernel launches per GPU chunk, and D2H copies on the supplied stream. Record CUDA events without synchronizing in `StartQ5GpuCopy`.

- [ ] **Step 5: Finish while retaining every owner**

`Finish()` synchronizes the stop event, obtains per-stage event durations, converts unsigned totals after range checks, and returns `Q5GpuPartial`. It may be called once; a second call returns `arrow::Status::Invalid`.

- [ ] **Step 6: Run tiny tests and one SF1 smoke run**

Run:

```bash
ctest --preset gpu-release -R 'test_cuda_memory|test_q5_cuda_kernel|test_q5_cuda' --output-on-failure
build/gpu-release/memq5 --engine gpu-copy --dataset /tmp/memq5-sf1-arrow --region ASIA --date 1994-01-01 --format json
```

Expected: exact hash matches `cpu-specialized`; JSON identifies Arrow CUDA allocation and reports bytes/timings.

- [ ] **Step 7: Commit the explicit-copy backend**

```bash
git add CMakeLists.txt src/engine/batch_slice.hpp src/cuda/q5_gpu_executor.hpp src/cuda/q5_gpu_executor.cu src/cuda/q5_cuda.hpp src/cuda/q5_cuda.cu src/cli/memq5.cpp tests/test_q5_cuda.cpp
git commit -m "feat: run gpu-copy from Arrow buffers"
```

## Task 4: Implement Managed And Mapped Memory Modes

**Files:**
- Modify: `src/cuda/q5_gpu_executor.hpp`
- Modify: `src/cuda/q5_gpu_executor.cu`
- Modify: `src/cuda/q5_cuda.hpp`
- Modify: `tests/test_q5_cuda.cpp`
- Modify: `src/cli/memq5.cpp`

**Interfaces:**
- Consumes: The same staged scale-2 arrays and prepared maps as Task 3.
- Produces: `execute_q5_gpu_managed` and `execute_q5_gpu_mapped` with mode-specific timing labels.

- [ ] **Step 1: Add mode-specific correctness and metric tests**

```cpp
const auto cpu = execute_q5_specialized(dataset, params).ValueOrDie();
const auto managed = execute_q5_gpu_managed(dataset, params).ValueOrDie();
const auto mapped = execute_q5_gpu_mapped(dataset, params).ValueOrDie();
assert(result_hash_hex(cpu) == result_hash_hex(managed));
assert(result_hash_hex(cpu) == result_hash_hex(mapped));
assert(managed.timing.host_prepare_ms >= 0.0);
assert(mapped.timing.host_prepare_ms >= 0.0);
assert(mapped.counters.h2d_bytes == 0);
```

Also test zero rows and a mapped allocation larger than available pinned-memory policy, which must return a capacity error instead of falling back.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
cmake --build --preset gpu-release -j
ctest --preset gpu-release -R test_q5_cuda --output-on-failure
```

Expected: compile or hash failure against old cent-truncating paths.

- [ ] **Step 3: Implement managed memory with explicit stage timing**

Allocate all inputs/results with `cudaMallocManaged`; fill from Arrow on CPU; record `host_prepare_ms`; enqueue `cudaMemPrefetchAsync` for every allocation and record it as `h2d_ms`/`h2d_bytes`; launch the shared kernel; prefetch the result to CPU and record `d2h_ms`/`d2h_bytes`.

Expose a profiling-only `--managed-prefetch off` flag, defaulting to `on`; include its value in JSON records.

- [ ] **Step 4: Implement mapped pinned host memory**

Allocate input/maps with `cudaHostAllocMapped`; copy Arrow values into host pointers; obtain UVA device pointers with `cudaHostGetDevicePointer`; keep only result storage on the device. Set input `h2d_bytes` to zero and report `mapped_remote_read_bytes` equal to the logical bytes read by the kernel.

- [ ] **Step 5: Run all mode tests and sanitizer checks**

Run:

```bash
ctest --preset gpu-release -R test_q5_cuda --output-on-failure
compute-sanitizer --tool memcheck build/gpu-release/tests/test_q5_cuda
```

Expected: all exact hashes match; sanitizer reports zero errors.

- [ ] **Step 6: Commit managed and mapped backends**

```bash
git add src/cuda/q5_gpu_executor.hpp src/cuda/q5_gpu_executor.cu src/cuda/q5_cuda.hpp src/cli/memq5.cpp tests/test_q5_cuda.cpp
git commit -m "feat: add managed and mapped Arrow GPU modes"
```

## Task 5: Partition Arrow Batches For Hybrid Execution

**Files:**
- Create: `src/hybrid/batch_partition.hpp`
- Create: `src/hybrid/batch_partition.cpp`
- Create: `tests/test_batch_partition.cpp`
- Modify: `CMakeLists.txt`
- Modify: `tests/CMakeLists.txt`

**Interfaces:**
- Consumes: Chunk lengths and a CPU ratio in `[0,1]`.
- Produces: deterministic, disjoint CPU/GPU `BatchSlice` lists.

```cpp
struct HybridPartition {
  std::vector<BatchSlice> cpu_slices;
  std::vector<BatchSlice> gpu_slices;
  int64_t cpu_rows;
  int64_t gpu_rows;
};

arrow::Result<HybridPartition> PartitionLineitemBatches(
    const arrow::ChunkedArray& anchor_column, double cpu_ratio);
```

- [ ] **Step 1: Write partition property tests**

For chunk lengths `[3, 5, 2]`, test ratios 0, .25, .5, .75, and 1. Assert total coverage 10, no duplicate `(chunk,row)`, no zero-length slices, deterministic output, and `abs(cpu_rows - round(total*ratio)) <= 1`.

- [ ] **Step 2: Run and verify failure**

Run:

```bash
cmake --build --preset cpu-debug -j
ctest --preset cpu-debug -R test_batch_partition --output-on-failure
```

Expected: build fails because the partition API does not exist.

- [ ] **Step 3: Implement deterministic row-prefix partitioning**

Choose `cpu_target = llround(total_rows * cpu_ratio)`. Traverse chunks in order; emit a CPU prefix and GPU suffix, splitting at most one chunk. Validate every lineitem column has the same chunk lengths as the anchor or return a schema error.

- [ ] **Step 4: Run tests and commit**

Run:

```bash
ctest --preset cpu-debug -R test_batch_partition --output-on-failure
```

Expected: all partition properties pass.

```bash
git add CMakeLists.txt tests/CMakeLists.txt src/hybrid/batch_partition.hpp src/hybrid/batch_partition.cpp tests/test_batch_partition.cpp
git commit -m "feat: partition Arrow batches for hybrid Q5"
```

## Task 6: Implement Truly Concurrent `hybrid-arrow`

**Files:**
- Create: `src/hybrid/q5_hybrid.hpp`
- Create: `src/hybrid/q5_hybrid.cu`
- Create: `tests/test_q5_hybrid.cpp`
- Modify: `src/cpu/q5_specialized.hpp`
- Modify: `src/cpu/q5_specialized.cpp`
- Modify: `src/cli/memq5.cpp`
- Modify: `CMakeLists.txt`
- Modify: `tests/CMakeLists.txt`

**Interfaces:**
- Consumes: `PartitionLineitemBatches`, reusable `Q5PreparedPlan`, `StartQ5GpuCopy`, and CPU scan over explicit slices.
- Produces: `execute_q5_hybrid` and overlap metadata.

```cpp
struct HybridOptions {
  double cpu_ratio;
  int cpu_threads;
  int64_t gpu_chunk_rows;
};

arrow::Result<Q5Result> execute_q5_hybrid(
    const ArrowTpchDataset& dataset,
    const Q5Params& params,
    const HybridOptions& options);
```

- [ ] **Step 1: Write exact ratio and determinism tests**

For ratios `.75`, `.50`, and `.25`, compare five repeated hybrid hashes to specialized CPU. Assert `cpu_rows + gpu_rows == input_rows`, matched counts merge exactly, and ratio metadata equals requested values. Run with CPU threads 1 and 4.

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
cmake --build --preset gpu-release -j
ctest --preset gpu-release -R test_q5_hybrid --output-on-failure
```

Expected: build fails because `execute_q5_hybrid` is undefined.

- [ ] **Step 3: Expose CPU scan over explicit slices**

Add:

```cpp
arrow::Result<Q5ScanPartial> scan_q5_specialized_slices(
    const ArrowTpchDataset& dataset,
    const Q5PreparedPlan& plan,
    const std::vector<BatchSlice>& slices,
    int threads);
```

The function builds no plan and sorts no output. It scans only supplied slices and returns local counters/revenue.

- [ ] **Step 4: Launch GPU before CPU scanning and merge after both finish**

Execution order must be:

```cpp
auto gpu_handle_result = StartQ5GpuCopy(dataset, plan, partition.gpu_slices,
                                        options.gpu_chunk_rows, stream.get());
if (!gpu_handle_result.ok()) {
  return gpu_handle_result.status();
}
auto gpu_handle = std::move(gpu_handle_result).ValueOrDie();
Stopwatch overlap_wall;
auto cpu_partial = scan_q5_specialized_slices(
    dataset, plan, partition.cpu_slices, options.cpu_threads);
auto gpu_partial = std::move(gpu_handle).Finish();
result.timing.overlap_wall_ms = overlap_wall.elapsed_ms();
if (!cpu_partial.ok()) {
  return cpu_partial.status();
}
if (!gpu_partial.ok()) {
  return gpu_partial.status();
}
```

Merge with `checked_add_revenue`, sort once, and preserve both partial matched-row counts. If either side fails, still synchronize/clean up the other side before returning the first error.

- [ ] **Step 5: Add double-buffered GPU chunk slots**

Use two Arrow CUDA input slot sets and two CUDA streams/events. While slot A executes its kernel, stage/enqueue the next chunk to slot B. Reuse slots only after their completion event. Record per-chunk H2D/kernel intervals in JSON when `--trace-timeline` is enabled.

- [ ] **Step 6: Run hybrid tests and capture an overlap trace**

Run:

```bash
ctest --preset gpu-release -R 'test_batch_partition|test_q5_hybrid' --output-on-failure
nsys profile --trace=cuda,nvtx,osrt --sample=cpu --output=/tmp/memq5-hybrid-smoke build/gpu-release/memq5 --engine hybrid-arrow --dataset /tmp/memq5-sf1-arrow --cpu-ratio 0.5 --threads 8 --gpu-chunk-rows 1048576 --format json
nsys stats /tmp/memq5-hybrid-smoke.nsys-rep
```

Expected: tests pass; the trace contains overlapping NVTX ranges `hybrid.cpu_scan` and `hybrid.gpu_h2d` or `hybrid.gpu_kernel`. If no overlap exists, the task remains incomplete and timing claims stay unverified.

- [ ] **Step 7: Commit hybrid execution**

```bash
git add CMakeLists.txt tests/CMakeLists.txt src/cpu/q5_specialized.hpp src/cpu/q5_specialized.cpp src/hybrid/q5_hybrid.hpp src/hybrid/q5_hybrid.cu src/cli/memq5.cpp tests/test_q5_hybrid.cpp
git commit -m "feat: overlap CPU and GPU Q5 execution"
```

## Task 7: Refactor cuDF To Consume Canonical Arrow Data

**Files:**
- Create: `baselines/arrow_dataset.py`
- Modify: `baselines/cudf_q5.py`
- Modify: `baselines/common.py`
- Create: `tests/python/test_cudf_q5.py`
- Modify: `scripts/self_check.py`

**Interfaces:**
- Consumes: The same six IPC files and manifest verified by the C++ loader.
- Produces: `load_arrow_dataset(path: Path, verify_checksums: bool = True) -> dict[str, pa.Table]` and `run_q5(tables, region, start_date) -> list[ResultRow]`.

- [ ] **Step 1: Write input-path and correctness tests**

```python
def test_cudf_uses_from_arrow(monkeypatch, tiny_dataset):
    calls = []
    original = cudf.from_arrow
    monkeypatch.setattr(cudf, "from_arrow", lambda table: calls.append(table.schema) or original(table))
    rows = run_dataset(tiny_dataset, "ASIA", "1994-01-01")
    assert len(calls) == 6
    assert rows == [ResultRow("JAPAN", 1900000), ResultRow("INDIA", 900000)]

def test_tbl_argument_is_rejected(cli_runner):
    result = cli_runner(["--data-dir", "tests/fixtures/tpch_q5_tiny"])
    assert result.returncode != 0
    assert "--dataset" in result.stderr
```

Mark runtime tests with `pytest.mark.gpu`; skip with reason `SKIPPED_NO_GPU` only when CUDA device discovery fails.

- [ ] **Step 2: Verify current cuDF baseline fails the contract**

Run:

```bash
pytest -q tests/python/test_cudf_q5.py
```

Expected: failure because current code calls `cudf.read_csv` and accepts `--data-dir`.

- [ ] **Step 3: Implement shared Arrow IPC loading and checksum checks**

Reuse manifest fields from Plan 1, `hashlib.sha256`, `pa.ipc.open_file`, schema equality, row counts, and null checks. Return PyArrow tables without converting them.

- [ ] **Step 4: Implement cuDF Q5 with exact decimal semantics**

Convert each table with `cudf.from_arrow`. Perform filters/merges/same-nation predicate. Convert price and discount Decimal columns to exact integer representations before multiplying, accumulate scale-4 `int64`, group and sort deterministically, and reject any value exceeding signed `int64`.

- [ ] **Step 5: Run tiny and SF1 cuDF comparisons**

Run:

```bash
pytest -q -m gpu tests/python/test_cudf_q5.py
python baselines/cudf_q5.py --dataset /tmp/memq5-sf1-arrow --region ASIA --date 1994-01-01 --format json
python scripts/verify_q5_oracle.py --dataset /tmp/memq5-sf1-arrow --engines cudf --expected 'data/tpch_tools/TPC-H V3.0.1/dbgen/answers/q5.out'
```

Expected: cuDF exact/formatted output matches the official SF1 oracle.

- [ ] **Step 6: Commit the Arrow cuDF baseline**

```bash
git add baselines/arrow_dataset.py baselines/cudf_q5.py baselines/common.py tests/python/test_cudf_q5.py scripts/self_check.py
git commit -m "feat: run cuDF Q5 from canonical Arrow data"
```

## Task 8: Complete GPU Correctness And Safety Gates

**Files:**
- Create: `scripts/run_gpu_validation.py`
- Create: `tests/test_gpu_status.py`
- Modify: `scripts/self_check.py`
- Modify: `docs/GPU_SERVER_RUNBOOK.md`

**Interfaces:**
- Consumes: GPU build, tiny/SF1 Arrow datasets, CUDA device, and official answer.
- Produces: one structured validation directory with CTest, sanitizer, oracle, and Nsight smoke evidence.

- [ ] **Step 1: Write status-classification tests**

Test exact mappings: no device to `SKIPPED_NO_GPU`; invalid value to `ERROR_CUDA_INVALID_VALUE`; allocation failure to `ERROR_CUDA_OOM`; missing Arrow CUDA to `ERROR_ARROW_CUDA_UNAVAILABLE`. Any error status must make validation exit nonzero.

- [ ] **Step 2: Verify tests fail and implement the validator**

Run:

```bash
pytest -q tests/test_gpu_status.py
```

Expected before implementation: import failure. Implement command execution with captured stdout/stderr/return code and no result substitution.

- [ ] **Step 3: Run the complete GPU gate**

Run:

```bash
python scripts/run_gpu_validation.py --preset gpu-release --tiny-dataset /tmp/memq5-tiny-arrow --sf1-dataset /tmp/memq5-sf1-arrow --official-q5 'data/tpch_tools/TPC-H V3.0.1/dbgen/answers/q5.out' --output results/validation/gpu-final
```

Expected: copy, managed, mapped, hybrid `.75/.50/.25`, cuDF, CPU, and Acero hashes match; sanitizer has zero errors; overlap trace check is true.

- [ ] **Step 4: Commit the GPU validation gate**

```bash
git add scripts/run_gpu_validation.py tests/test_gpu_status.py scripts/self_check.py docs/GPU_SERVER_RUNBOOK.md
git commit -m "test: add complete GPU Q5 validation gate"
```

## Plan Acceptance

Run:

```bash
cmake --preset gpu-release
cmake --build --preset gpu-release -j
ctest --preset gpu-release --output-on-failure
pytest -q tests/python/test_cudf_q5.py tests/test_gpu_status.py
compute-sanitizer --tool memcheck build/gpu-release/tests/test_q5_cuda
compute-sanitizer --tool memcheck build/gpu-release/tests/test_q5_hybrid
python scripts/run_gpu_validation.py --preset gpu-release --tiny-dataset /tmp/memq5-tiny-arrow --sf1-dataset /tmp/memq5-sf1-arrow --official-q5 'data/tpch_tools/TPC-H V3.0.1/dbgen/answers/q5.out' --output results/validation/gpu-final
```

Expected:

- All GPU and Python tests pass or the entire gate reports only the explicit no-GPU skip status.
- All formal backends match exact tiny hashes and official SF1 formatted values.
- `gpu-copy` records nonzero H2D bytes, mapped records remote-read bytes, and managed records prefetch/page-migration timing.
- Nsight evidence shows true hybrid overlap; without that evidence, the project may describe hybrid code but not claim measured CPU-GPU collaboration.

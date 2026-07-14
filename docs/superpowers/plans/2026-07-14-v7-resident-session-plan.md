# V7 Resident Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build fixed-query CPU, CUDA, hybrid, and cuDF sessions that load and prepare once, execute repeated requests, and emit auditable setup/request JSONL.

**Architecture:** Reuse the existing exact Q5 plan and kernel, but split one-shot wrappers into setup and execute phases. C++ sessions own persistent plan/buffers through RAII; a separate CLI emits one setup object and one object per request. A V7 Python schema/runner expands one session process into request records without changing V5 evidence.

**Tech Stack:** C++17, Apache Arrow 23.0.1, CUDA 12.6, nlohmann_json, Python 3.11, pytest, CMake/CTest.

## Global Constraints

- Preserve `submission-v6-final`; V6 outputs and V5 evidence are immutable.
- A resident session fixes dataset, region, date, engine, threads, and ratio.
- Three warmups and ten measured requests are the formal default.
- Resident copy input H2D occurs only during setup; managed output migration remains request-visible.
- Every request must produce the same exact result hash or fail the session.
- Use TDD and commit after each independently passing task.

---

### Task 1: Reusable Specialized CPU Plan

**Files:**
- Create: `src/cpu/q5_arrow_scan.hpp`
- Create: `src/cpu/q5_arrow_scan.cpp`
- Create: `src/session/q5_cpu_session.hpp`
- Create: `src/session/q5_cpu_session.cpp`
- Modify: `src/cpu/q5_arrow_cpu.cpp`
- Modify: `CMakeLists.txt`
- Create: `tests/test_q5_cpu_session.cpp`
- Modify: `tests/CMakeLists.txt`

**Interfaces:**
- Consumes: `ArrowQ5Dataset`, `ArrowQ5Plan`, `Q5Params`, `Q5Result`.
- Produces:

```cpp
struct Q5SessionSetup {
  double plan_build_ms = 0.0;
  double host_staging_ms = 0.0;
  double allocation_ms = 0.0;
  double initial_h2d_ms = 0.0;
  double total_ms = 0.0;
  int64_t resident_host_bytes = 0;
  int64_t resident_gpu_bytes = 0;
  int64_t resident_pinned_bytes = 0;
};

arrow::Result<Q5Result> scan_q5_arrow_lineitem(
    const std::shared_ptr<arrow::Table>& lineitem,
    const ArrowQ5Plan& plan, int threads);

class ArrowCpuQ5Session {
 public:
  static arrow::Result<std::unique_ptr<ArrowCpuQ5Session>> Make(
      const ArrowQ5Dataset& dataset, const Q5Params& params);
  arrow::Result<Q5Result> Execute() const;
  const Q5SessionSetup& setup() const;
};
```

- [ ] **Step 1: Write the failing CPU session test**

```cpp
const auto dataset = LoadTinyArrowDataset().ValueOrDie();
Q5Params params = Asia1994Params();
params.threads = 2;
auto session = ArrowCpuQ5Session::Make(dataset, params).ValueOrDie();
const auto first = session->Execute().ValueOrDie();
const auto second = session->Execute().ValueOrDie();
assert(result_hash_hex(first) == "248d10b6ee352953");
assert(result_hash_hex(first) == result_hash_hex(second));
assert(first.timing.build_ms == 0.0);
assert(second.timing.build_ms == 0.0);
assert(session->setup().plan_build_ms >= 0.0);
```

- [ ] **Step 2: Run the focused test and confirm the missing API**

Run: `cmake --build build/arrow-cpu -j2 && ctest --test-dir build/arrow-cpu -R q5_cpu_session --output-on-failure`
Expected: compile failure because `ArrowCpuQ5Session` does not exist.

- [ ] **Step 3: Extract the scan from the one-shot CPU function**

Move only lineitem batch validation, partitioning, worker scan, checked merge, sorting, and counters into `scan_q5_arrow_lineitem`. Keep `build_arrow_q5_plan` outside this function. Make the existing wrapper equivalent to:

```cpp
Stopwatch total;
ARROW_ASSIGN_OR_RAISE(auto plan, build_arrow_q5_plan(dataset, params));
ARROW_ASSIGN_OR_RAISE(auto result,
                      scan_q5_arrow_lineitem(dataset.lineitem, plan,
                                             params.threads));
result.timing.build_ms = plan.build_ms;
result.timing.total_ms = total.elapsed_ms();
return result;
```

- [ ] **Step 4: Implement `ArrowCpuQ5Session`**

Store the lineitem table, immutable plan, thread count, and setup metrics. `Make` rejects non-positive threads. `Execute` starts a fresh timer, calls `scan_q5_arrow_lineitem`, sets `build_ms=0`, and leaves setup values only in `setup()`.

- [ ] **Step 5: Run CPU and existing Arrow regression tests**

Run: `conda run -p /tmp/memq5-arrow-cpu-task1 cmake --build --preset arrow-cpu -j2`
Run: `conda run -p /tmp/memq5-arrow-cpu-task1 ctest --preset arrow-cpu --output-on-failure`
Expected: new session test and all existing 14 tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/cpu src/session tests CMakeLists.txt
git commit -m "feat: add reusable Arrow CPU Q5 session"
```

### Task 2: Persistent CUDA Sessions

**Files:**
- Modify: `src/cuda/q5_arrow_cuda.hpp`
- Modify: `src/cuda/q5_arrow_cuda.cu`
- Create: `tests/test_q5_cuda_session.cpp`
- Modify: `tests/CMakeLists.txt`

**Interfaces:**
- Consumes: `Q5SessionSetup` from Task 1 and the existing exact CUDA kernel.
- Produces:

```cpp
enum class ArrowCudaMemoryMode { kCopy, kManaged, kMapped };

class ArrowCudaQ5Session {
 public:
  static arrow::Result<std::unique_ptr<ArrowCudaQ5Session>> Make(
      const ArrowQ5Dataset& dataset, const Q5Params& params,
      ArrowCudaMemoryMode mode);
  ~ArrowCudaQ5Session();
  arrow::Result<Q5Result> Execute();
  const Q5SessionSetup& setup() const;
 private:
  struct Impl;
  explicit ArrowCudaQ5Session(std::unique_ptr<Impl> impl);
  std::unique_ptr<Impl> impl_;
};
```

- [ ] **Step 1: Add a failing three-mode persistence test**

```cpp
for (const auto mode : {ArrowCudaMemoryMode::kCopy,
                        ArrowCudaMemoryMode::kManaged,
                        ArrowCudaMemoryMode::kMapped}) {
  auto session = ArrowCudaQ5Session::Make(dataset, params, mode).ValueOrDie();
  const auto first = session->Execute().ValueOrDie();
  const auto second = session->Execute().ValueOrDie();
  assert(result_hash_hex(first) == "248d10b6ee352953");
  assert(result_hash_hex(first) == result_hash_hex(second));
  assert(session->setup().resident_gpu_bytes > 0 ||
         session->setup().resident_pinned_bytes > 0);
  if (mode == ArrowCudaMemoryMode::kCopy) {
    assert(session->setup().initial_h2d_ms >= 0.0);
    assert(first.timing.h2d_ms == 0.0);
    assert(second.counters.h2d_bytes == 0);
  }
}
```

- [ ] **Step 2: Confirm the new test fails to compile**

Run: `cmake --build build-arrow-cuda-v3 -j2`
Expected: compile failure because the CUDA session API is missing.

- [ ] **Step 3: Move prepared input and buffers into a CUDA PIMPL**

`Impl` owns `ArrowGpuInput`, nation count, mode-specific input buffers, output buffers, host output scratch, and setup metrics. Allocation and input transfer happen in `Make`; `Execute` performs exactly:

```cpp
reset_output_buffers();
prefetch_managed_output_to_device_if_needed();
const double kernel_ms = launch_existing_kernel();
const double d2h_ms = collect_small_output();
return finish_result_for_request(kernel_ms, d2h_ms);
```

All destructors remain noexcept and release partially constructed mapped allocations. Convert CUDA memory allocation failures to `CapacityError` as in V6.

- [ ] **Step 4: Preserve cold-wrapper behavior**

Implement existing `execute_q5_arrow_gpu_*` wrappers by creating a session, executing once, and folding setup plan/staging/H2D into the returned cold result. Run existing oracle tests to prove hashes and counters did not change.

- [ ] **Step 5: Run real-GPU tests and sanitizer**

Run: `CUDA_VISIBLE_DEVICES=0 ctest --test-dir build-arrow-cuda-v3 --output-on-failure`
Expected: every CUDA test runs, no skip, all pass.
Run: `CUDA_VISIBLE_DEVICES=0 compute-sanitizer --tool memcheck --error-exitcode=99 build-arrow-cuda-v3/tests/test_q5_cuda_session`
Expected: `ERROR SUMMARY: 0 errors`.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/cuda tests CMakeLists.txt
git commit -m "feat: keep Arrow CUDA Q5 inputs resident"
```

### Task 3: Resident Hybrid Session

**Files:**
- Modify: `src/hybrid/q5_hybrid.hpp`
- Modify: `src/hybrid/q5_hybrid.cpp`
- Create: `tests/test_q5_hybrid_session.cpp`
- Modify: `tests/CMakeLists.txt`

**Interfaces:**
- Consumes: `ArrowCpuQ5Session`, `ArrowCudaQ5Session`, `HybridOptions`.
- Produces:

```cpp
class HybridQ5Session {
 public:
  static arrow::Result<std::unique_ptr<HybridQ5Session>> Make(
      const ArrowQ5Dataset& dataset, const Q5Params& params,
      const HybridOptions& options);
  arrow::Result<Q5Result> Execute();
  const Q5SessionSetup& setup() const;
  double cpu_ratio() const;
};
```

- [ ] **Step 1: Write failing repeated hybrid tests for 0.25/0.50/0.75**

For each ratio, create one session, execute twice, require exact hash, exact total input rows, stable CPU/GPU row partition, nonnegative overlap, and zero resident copy input H2D.

- [ ] **Step 2: Confirm the focused test fails**

Run: `cmake --build build-arrow-cuda-v3 -j2`
Expected: compile failure for missing `HybridQ5Session`.

- [ ] **Step 3: Implement setup-time partition and repeated concurrent execution**

Slice lineitem once during `Make`. Construct CPU and GPU child sessions once. Each `Execute` launches the GPU child with `std::async`, executes CPU in the caller, waits for both even after one error, and calls the existing checked merge routine.

- [ ] **Step 4: Run hybrid tests and sanitizer**

Run: `CUDA_VISIBLE_DEVICES=0 ctest --test-dir build-arrow-cuda-v3 -R hybrid --output-on-failure`
Run: `CUDA_VISIBLE_DEVICES=0 compute-sanitizer --tool memcheck --error-exitcode=99 build-arrow-cuda-v3/tests/test_q5_hybrid_session`
Expected: all pass and sanitizer reports zero errors.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/hybrid tests CMakeLists.txt
git commit -m "feat: add resident CPU-GPU hybrid session"
```

### Task 4: Session JSONL CLI

**Files:**
- Create: `src/session/q5_session_record.hpp`
- Create: `src/session/q5_session_io.hpp`
- Create: `src/session/q5_session_io.cpp`
- Create: `src/cli/memq5_arrow_session.cpp`
- Modify: `CMakeLists.txt`
- Create: `tests/test_q5_session_io.cpp`
- Modify: `tests/CMakeLists.txt`

**Interfaces:**
- Consumes: all resident session classes.
- Produces executable:

```text
memq5_arrow_session --dataset PATH --engine ENGINE --region ASIA
  --date 1994-01-01 --threads N --cpu-ratio R --warmup 3 --repeat 10
```

- [ ] **Step 1: Write failing JSONL serialization tests**

Parse each emitted line with `nlohmann::json`. Require one setup record followed by exactly `warmup + repeat` request records. Each request includes `session_id`, `request_index`, `is_warmup`, result hash, timings, counters, and selected ratio.

- [ ] **Step 2: Implement records and strict argument validation**

Reject warmup below 0, repeat below 1, unsupported engine, invalid ratio, bad date, or non-positive threads before loading the dataset. Generate one UUID-like session identifier per process.

- [ ] **Step 3: Implement engine factories and output**

Load Arrow once, construct one session, emit setup, run requests, compare every result hash to the first successful request, and abort nonzero with an error request record on mismatch.

- [ ] **Step 4: Add tiny CLI CTests**

Add CPU specialized and real-GPU copy/managed/mapped/hybrid tiny tests with `--warmup 1 --repeat 2`. A Python validation command checks line count and hash.

- [ ] **Step 5: Run full Arrow+CUDA CTest**

Run: `CUDA_VISIBLE_DEVICES=0 ctest --test-dir build-arrow-cuda-v3 --output-on-failure`
Expected: all tests run and pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add src/session src/cli tests CMakeLists.txt
git commit -m "feat: expose resident Q5 session JSONL CLI"
```

### Task 5: cuDF Resident Process

**Files:**
- Modify: `baselines/cudf_q5.py`
- Create: `baselines/cudf_q5_session.py`
- Modify: `tests/python/test_cudf_q5.py`

**Interfaces:**
- Consumes: `_load_cudf_tables` and exact cuDF Q5 operators.
- Produces: the same setup/request JSONL shape as the C++ CLI.

- [ ] **Step 1: Write a failing tiny test that counts Arrow-to-cuDF conversions**

Patch `cudf.DataFrame.from_arrow`, run a session with one warmup and two measured requests, assert exactly six conversions total and three equal result hashes.

- [ ] **Step 2: Split load from query execution**

Create `CudfQ5Session.__init__(dataset, region, date)` to load six frames once. `execute()` performs only Q5 operators and returns existing `CudfBenchmarkResult`; setup metrics retain conversion time and resident bytes from `memory_usage(deep=True)`.

- [ ] **Step 3: Implement JSONL CLI and no-device handling**

Use `numba.cuda.is_available()` before session creation. Return code 77 and a clear stderr message when no CUDA device exists; do not emit successful request rows.

- [ ] **Step 4: Run RAPIDS tests on GPU**

Run: `CUDA_VISIBLE_DEVICES=0 conda run -n memq5-cudf env PYTHONPATH=/tmp/memq5-pytest311/lib/python3.11/site-packages python -m pytest -q tests/python/test_cudf_q5.py`
Expected: all cuDF tests run, no skip, all pass.

- [ ] **Step 5: Commit Task 5**

```bash
git add baselines/cudf_q5.py baselines/cudf_q5_session.py tests/python/test_cudf_q5.py
git commit -m "feat: add resident cuDF Q5 session"
```

### Task 6: V7 Schema, Runner, and SF1 Smoke

**Files:**
- Create: `scripts/v7_benchmark_schema.py`
- Create: `scripts/resident_protocol.py`
- Create: `scripts/run_v7_benchmarks.py`
- Create: `experiments/v7_resident_sf1_smoke.yml`
- Create: `tests/python/test_v7_benchmark_schema.py`
- Create: `tests/python/test_resident_protocol.py`
- Create: `tests/python/test_run_v7_benchmarks.py`
- Modify: `progress.md`

**Interfaces:**
- Consumes: C++/cuDF JSONL CLIs.
- Produces: setup CSV, warmup CSV, raw request CSV, logs, commands, environment, and strict schema-version-2 records.

- [ ] **Step 1: Write failing schema and parser tests**

Require lifecycle, session ID, setup timings/bytes, request index, selected ratio, exact hash, and bundle-relative log paths. Reject missing setup, duplicate request indexes, wrong request count, hash drift, negative timings, or cold rows labelled resident.

- [ ] **Step 2: Implement immutable schema v2**

Keep `scripts/benchmark_schema.py` unchanged. `V7BenchmarkRecord` adds:

```python
session_id: str
lifecycle: str
dataset_load_ms: float
session_setup_ms: float
tune_ms: float
resident_host_bytes: int
resident_gpu_bytes: int
resident_pinned_bytes: int
selected_cpu_ratio: float
predicted_cpu_ratio: float
request_index: int
```

- [ ] **Step 3: Implement one-process session attribution**

Monitor the process once, parse JSONL after exit, attach the same process RSS/GPU peak to the setup record, and create one benchmark record per request. Preserve stdout/stderr on every outcome.

- [ ] **Step 4: Run strict tiny smoke**

Run all supported resident engines with 1 warmup and 2 measured requests. Require no failure and the tiny hash `248d10b6ee352953` for every measured row.

- [ ] **Step 5: Run SF1 resident smoke**

Use 1 warmup and 2 measurements for CPU specialized, copy, managed, mapped, hybrid 0.5, and cuDF. Verify all hashes against `542abf4003633c7c` and audit request coverage.

- [ ] **Step 6: Update progress and commit Task 6**

```bash
git add scripts experiments tests/python progress.md
git commit -m "feat: add V7 resident benchmark protocol"
```

### Task 7: Resident Milestone Verification

**Files:**
- Modify: `docs/CURRENT_STATUS.md`
- Modify: `progress.md`

- [ ] **Step 1: Run CPU, Arrow, CUDA, RAPIDS, and Python gates**

Expected: CPU 5/5, Arrow suite all pass, CUDA suite all runs on GPU, RAPIDS tests all run, and Python suite has no product failure.

- [ ] **Step 2: Run representative compute-sanitizer checks**

Run CUDA session and hybrid session binaries under memcheck. Expected: zero errors.

- [ ] **Step 3: Record exact evidence and residual limitations**

Record commit, GPU UUID, test counts, hashes, setup/request semantics, and unsupported parameter changes in `progress.md` and current status.

- [ ] **Step 4: Commit the resident milestone**

```bash
git add docs/CURRENT_STATUS.md progress.md
git commit -m "docs: freeze V7 resident session milestone"
```

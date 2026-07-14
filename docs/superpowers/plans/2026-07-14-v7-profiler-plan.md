# V7 CUDA Profiler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional NVTX instrumentation and produce auditable Nsight Systems and Nsight Compute evidence that explains copy, managed, mapped, and hybrid behavior without contaminating ordinary latency results.

**Architecture:** Compile NVTX behind a CMake option, use one small RAII wrapper, and annotate existing lifecycle phases. Python collectors execute one stable measured request, save raw reports and exported tables, validate supported metrics before collection, and write a profiler-only manifest.

**Tech Stack:** C++17, CUDA 12.6, NVTX3, Nsight Systems, Nsight Compute, Python 3.11, pytest.

## Global Constraints

- Instrumentation must compile out cleanly with `MEMQ5_ENABLE_NVTX=OFF`.
- Profiler process times never enter resident latency summaries.
- Profile only after correctness and ordinary resident measurements pass.
- Do not claim overlap, migration behavior, occupancy, or bandwidth unless the exported evidence contains it.
- Store raw profiler reports only when release size is acceptable; always store commands, exports, versions, and hashes.

---

### Task 1: Optional NVTX Range Layer

**Files:**
- Create: `src/common/nvtx_range.hpp`
- Modify: `src/cuda/q5_arrow_cuda.cu`
- Modify: `src/hybrid/q5_hybrid.cpp`
- Modify: `src/session/q5_cpu_session.cpp`
- Modify: `CMakeLists.txt`
- Create: `tests/test_nvtx_build.py`

- [ ] **Step 1: Write failing OFF/ON build tests**

Configure a CPU build with NVTX off and a CUDA build with NVTX on. Require the OFF build to have no NVTX link dependency and the ON binary to contain declared range names.

- [ ] **Step 2: Implement a no-throw RAII range**

```cpp
class NvtxRange {
 public:
  explicit NvtxRange(const char* name) noexcept;
  ~NvtxRange() noexcept;
  NvtxRange(const NvtxRange&) = delete;
  NvtxRange& operator=(const NvtxRange&) = delete;
};
```

When disabled, construction/destruction are empty inline operations. When enabled, use NVTX3 headers supplied by the CUDA toolkit.

- [ ] **Step 3: Annotate stable phase names**

Use `session_setup`, `host_staging`, `allocation`, `initial_h2d`, `request`, `cpu_scan`, `output_reset`, `managed_prefetch`, `q5_kernel`, `d2h`, and `merge`. Keep ranges outside timing start/stop calls where practical.

- [ ] **Step 4: Run OFF/ON builds and result regressions**

Run: `python -m pytest -q tests/test_nvtx_build.py`
Run tiny CPU and all GPU modes in both builds. Expected: identical hashes and no meaningful record-schema difference.

- [ ] **Step 5: Commit Task 1**

```bash
git add src CMakeLists.txt tests/test_nvtx_build.py
git commit -m "feat: instrument Q5 sessions with optional NVTX"
```

### Task 2: Nsight Systems Collector and Parser

**Files:**
- Create: `scripts/profile_nsys.py`
- Create: `scripts/parse_nsys_stats.py`
- Create: `tests/python/test_parse_nsys_stats.py`
- Create: `tests/fixtures/nsys_stats_sample.csv`

**Interfaces:**

```python
parse_nsys_csv(path: Path) -> list[dict[str, object]]
validate_ranges(rows: list[dict], required: set[str]) -> list[str]
collect_nsys(command: list[str], output_dir: Path, metadata: dict) -> dict
```

- [ ] **Step 1: Write failing parser tests from a fixed export fixture**

Cover locale-independent numeric parsing, quoted kernel names, absent range, failed command, empty GPU report, and calculation of CUDA memcpy/kernel totals. Parser tests must not require Nsight installation.

- [ ] **Step 2: Implement explicit command construction**

Use:

```text
nsys profile --force-overwrite=true --trace=cuda,nvtx,osrt --sample=none
nsys stats --force-export=true --report cuda_api_sum,cuda_gpu_kern_sum,cuda_gpu_mem_time_sum,nvtx_sum
```

Save command arrays rather than shell strings, tool versions, stdout/stderr, `.nsys-rep`, exported files, return codes, and SHA256 values.

- [ ] **Step 3: Add a one-request SF1 smoke**

Profile resident copy with zero warmups and one measured request dedicated to profiling. Require `request` and `q5_kernel` ranges plus a nonempty CUDA kernel table.

- [ ] **Step 4: Commit Task 2**

```bash
git add scripts/profile_nsys.py scripts/parse_nsys_stats.py tests/python/test_parse_nsys_stats.py tests/fixtures/nsys_stats_sample.csv
git commit -m "feat: collect and parse Nsight Systems evidence"
```

### Task 3: Nsight Compute Metric Discovery and Collection

**Files:**
- Create: `scripts/profile_ncu.py`
- Create: `scripts/parse_ncu_csv.py`
- Create: `tests/python/test_parse_ncu_csv.py`
- Create: `tests/fixtures/ncu_metrics_sample.csv`

- [ ] **Step 1: Write failing metric-selection and parser tests**

Given a supported-metric fixture, choose exactly one duration metric, DRAM read/throughput metrics, SM throughput, and achieved occupancy. Reject an empty intersection, unexpected multiple Q5 kernels, replay failure, or nonnumeric values.

- [ ] **Step 2: Implement device-specific discovery**

Run `ncu --query-metrics`, select canonical metrics from documented candidate lists, and persist the supported list and final selection before launching a profile. Do not hard-code a metric known only from another GPU generation.

- [ ] **Step 3: Implement isolated one-request capture**

Use kernel-name filtering for the existing Q5 kernel and CSV output. Record replay mode, selected metrics, driver/GPU UUID, command, tool version, return code, stdout/stderr, and report checksum.

- [ ] **Step 4: Run an SF1 smoke and compare CLI timing**

Expected: exact query result remains correct; profiler duration is reported only in profiler output and is not copied into `raw.csv`.

- [ ] **Step 5: Commit Task 3**

```bash
git add scripts/profile_ncu.py scripts/parse_ncu_csv.py tests/python/test_parse_ncu_csv.py tests/fixtures/ncu_metrics_sample.csv
git commit -m "feat: capture device-supported Nsight Compute metrics"
```

### Task 4: Formal SF1/SF10 Profiler Evidence

**Files:**
- Create: `scripts/v7_profiler_bundle.py`
- Create: `tests/python/test_v7_profiler_bundle.py`
- Create at runtime, then commit selected evidence: `docs/artifacts/v7_profiler`
- Modify: `progress.md`

- [ ] **Step 1: Write failing profiler-manifest audit tests**

Require scale factor, engine, session commit, dataset manifest, oracle hash, GPU UUID, command, tool version, raw/export hashes, required NVTX ranges, and selected NCU metrics. A missing report or modified export fails audit.

- [ ] **Step 2: Profile the frozen matrix**

For SF1 and SF10, capture copy, managed, mapped, fixed hybrid best ratio, and hybrid-auto. Use one stable request after unprofiled warmup. If a profiler cannot access counters, record a structured unavailable result and retain Nsight Systems evidence.

- [ ] **Step 3: Export and audit observations**

Derive only directly supported facts: input H2D presence, managed migration/prefetch API activity, mapped absence of explicit input H2D, kernel duration, DRAM traffic, occupancy, and CPU/GPU overlap windows.

- [ ] **Step 4: Keep reports separate from benchmark evidence**

Run both V7 resident bundle audits again and prove profiler rows did not alter `raw.csv`, `summary.csv`, or manifest digests.

- [ ] **Step 5: Commit profiler evidence**

```bash
git add scripts/v7_profiler_bundle.py tests/python/test_v7_profiler_bundle.py docs/artifacts/v7_profiler progress.md
git commit -m "data: freeze V7 CUDA profiler evidence"
```

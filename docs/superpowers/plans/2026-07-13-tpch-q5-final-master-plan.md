# TPC-H Q5 Final Project Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved Apache Arrow CPU-GPU TPC-H Q5 research project, formal experiments, paper, process records, and takeover materials.

**Architecture:** The work is split into four reviewable plans. The Arrow data and correctness plan establishes the only canonical dataset and result semantics; the GPU plan consumes those interfaces; the evidence plan consumes every backend's machine-readable records; the publication plan consumes only verified evidence through the claim ledger.

**Tech Stack:** C++17, CMake, Apache Arrow/Acero/CUDA 23.0.1, CUDA 12.x, PyArrow 23.0.1, cuDF 26.06.0, DuckDB decimal SQL, Python 3.11, pytest, Nsight Systems/Compute, LaTeX.

## Global Constraints

- Apache Arrow C++ and PyArrow are fixed at 23.0.1.
- Arrow C++ must be built with CUDA, Acero, Compute, CSV, and IPC enabled.
- RAPIDS cuDF is fixed at 26.06.0; CUDA is 12.x and exact toolkit/driver versions go into each experiment manifest.
- CPU-only builds must configure and test without CUDA, cuDF, or a GPU driver.
- All formal backends consume the same Arrow IPC dataset and immutable `ArrowTpchDataset` view.
- Canonical price and discount columns are `decimal128(15,2)`; specialized kernels accumulate exact scale-4 revenue without per-line cent truncation.
- Tiny, synthetic, SF1, and SF10 are required; SF1 must match the official `q5.out` and SF10 must match DuckDB decimal output.
- Formal measurements use 3 warmups and 10 measured repetitions; CPU threads are 1, 2, 4, 8, 16, and 32; hybrid ratios are 75/25, 50/50, and 25/75.
- Failures, skips, OOM events, stdout, stderr, commands, environment, and checksums remain in the evidence directory.
- Result claims enter the final paper only when `docs/research/CLAIM_LEDGER.md` marks them `VERIFIED` or `REJECTED` with linked evidence.
- Existing user changes in `docs/FINAL_REPORT.md`, `docs/FINAL_REPORT.docx`, `docs/DEFENSE_CHEATSHEET.md`, and `docs/PROJECT_HANDOVER_GUIDE.md` must be preserved and reviewed before any edit.

---

## Plan Suite

| Order | Plan | Independently Testable Deliverable |
| --- | --- | --- |
| 1 | `2026-07-13-arrow-correctness-cpu-plan.md` | Arrow IPC dataset, exact Q5 semantics, Arrow Acero and specialized CPU backends pass tiny and SF1 oracle gates. |
| 2 | `2026-07-13-gpu-hybrid-baselines-plan.md` | Three CUDA modes, true concurrent hybrid execution, cuDF Arrow baseline, and GPU correctness tests pass. |
| 3 | `2026-07-13-benchmark-evidence-plan.md` | Reproducible cold/resident experiment bundles, statistics, correctness gates, plots, and profiler evidence are generated. |
| 4 | `2026-07-13-paper-learning-release-plan.md` | Claim-controlled paper, Tencent source documents, learning/defense package, open-source metadata, CI, and release audit are complete. |

## Cross-Plan Interfaces

The following names are contracts. A later plan may extend a struct by appending fields, but must not rename or reinterpret these members.

```cpp
namespace memq5 {

struct ArrowTpchDataset {
  std::shared_ptr<arrow::Table> region;
  std::shared_ptr<arrow::Table> nation;
  std::shared_ptr<arrow::Table> supplier;
  std::shared_ptr<arrow::Table> customer;
  std::shared_ptr<arrow::Table> orders;
  std::shared_ptr<arrow::Table> lineitem;
  DatasetManifest manifest;

  static arrow::Result<ArrowTpchDataset> Load(const std::string& dataset_dir,
                                               bool verify_checksums = true);
};

struct Q5ResultRow {
  std::string nation_name;
  int64_t revenue_1e4;
};

struct Q5Timing {
  double load_ms;
  double plan_build_ms;
  double host_prepare_ms;
  double h2d_ms;
  double cpu_scan_ms;
  double gpu_kernel_ms;
  double d2h_ms;
  double overlap_wall_ms;
  double query_total_ms;
};

struct Q5Counters {
  int64_t input_lineitem_rows;
  int64_t matched_lineitem_rows;
  int64_t cpu_input_rows;
  int64_t gpu_input_rows;
  int64_t h2d_bytes;
  int64_t d2h_bytes;
  int64_t mapped_remote_read_bytes;
};

struct Q5Result {
  std::vector<Q5ResultRow> rows;
  Q5Timing timing;
  Q5Counters counters;
};

}  // namespace memq5
```

Every executable backend emits one JSON object per measured sample using this minimum contract:

```json
{
  "schema_version": 1,
  "status": "ok",
  "engine": "cpu-specialized",
  "scenario": "resident",
  "sample_index": 0,
  "region": "ASIA",
  "date": "1994-01-01",
  "threads": 8,
  "cpu_ratio": 1.0,
  "gpu_ratio": 0.0,
  "result_rows": 5,
  "result_hash": "hex-value",
  "oracle_status": "verified",
  "timing": {},
  "counters": {}
}
```

## Approved-Spec Traceability

| Design Requirement | Owning Plan/Task |
| --- | --- |
| Fixed Arrow/CUDA/cuDF environments and CPU-only build | Arrow Plan Task 1 |
| Canonical schemas, streaming `.tbl` conversion, IPC, manifest, checksums | Arrow Plan Tasks 2-3 |
| Exact Decimal128-equivalent revenue and deterministic output | Arrow Plan Task 4 |
| Specialized Arrow-buffer CPU and Arrow Acero | Arrow Plan Tasks 5-6 |
| Tiny, official SF1, DuckDB SF10 oracle hierarchy | Arrow Plan Task 7; Evidence Plan Tasks 5 and 9 |
| Controlled synthetic density/selectivity data | Arrow Plan Task 8 |
| Arrow CUDA copy, managed, and mapped modes | GPU Plan Tasks 1-4 |
| Batch partition, asynchronous hybrid, double buffering, overlap proof | GPU Plan Tasks 5-6 and 8 |
| cuDF from the canonical Arrow tables | GPU Plan Task 7 |
| Unified metrics, cold/resident, memory monitoring | Evidence Plan Tasks 1-3 |
| Evidence directory, failures, checksums, independent audit | Evidence Plan Tasks 4-5 |
| Median/min/max/p95/stddev and traceable figures | Evidence Plan Task 6 |
| SF1/SF10 formal matrix and Nsight profiles | Evidence Plan Tasks 7-9 |
| RQ/H preregistration and claim-state control | Publication Plan Task 1 |
| Tencent source records | Publication Plan Task 2 |
| Progressive learning and takeover material | Publication Plan Task 3 |
| 计算机学报 paper and evidence import | Publication Plan Tasks 4-5 |
| License, citation, environments, Docker, README, data policy | Publication Plan Task 6 |
| CPU CI, GPU runbook, defense, final release audit | Publication Plan Tasks 7-8 |

## Execution Order And Gates

### Gate 0: Preserve Baseline

- [ ] Record `git status --short --branch`, `git log -1 --oneline`, current CPU test output, current CUDA test output, and current SF1 hashes in `results/baseline/pre-arrow/`.
- [ ] Create the implementation worktree with `superpowers:using-git-worktrees`; do not implement in the planning worktree.
- [ ] Confirm only the intended plan task files are staged before every commit.

Expected gate: the old implementation remains reproducible and user-owned document changes are untouched.

### Gate 1: Arrow And CPU

- [ ] Execute every task in `2026-07-13-arrow-correctness-cpu-plan.md` in order.
- [ ] Run CPU-only configure, all CPU tests, tiny all-CPU backend comparison, and SF1 official oracle comparison.
- [ ] Update the claim ledger entries for Arrow storage and CPU backends from `PLANNED` to `VERIFIED` only after the commands pass.

Expected gate: `.tbl` is absent from query backend code paths, and `arrow-acero` plus `cpu-specialized` return official SF1 values.

### Gate 2: GPU And Hybrid

- [ ] Execute every task in `2026-07-13-gpu-hybrid-baselines-plan.md` in order.
- [ ] Run CUDA unit/integration tests, compute-sanitizer, tiny all-backend comparison, and one SF1 smoke run.
- [ ] Capture a representative Nsight Systems trace showing CPU scan overlap with GPU H2D or kernel work.

Expected gate: copy, managed, mapped, cuDF, and all three hybrid ratios match the exact oracle; a trace establishes real overlap.

### Gate 3: Evidence

- [ ] Execute every task in `2026-07-13-benchmark-evidence-plan.md` in order.
- [ ] Run the full SF1 and SF10 matrices without deleting failed samples.
- [ ] Validate every table/figure source against raw CSV and manifest checksums.

Expected gate: each experiment directory has the approved structure and every reported number is traceable.

### Gate 4: Publication And Takeover

- [ ] Execute every task in `2026-07-13-paper-learning-release-plan.md` in order.
- [ ] Build the paper twice, scan the PDF/text for pending-result markers, and run the release audit.
- [ ] Rehearse the 5-minute and 10-minute explanations against the final evidence.

Expected gate: public repository artifacts, process documents, paper PDF, teaching materials, and final audit all agree with implemented behavior.

## Parallel Work Policy

After Gate 0, work may proceed in two tracks:

- Engineering track: Plans 1 through 3, in dependency order.
- Report/learning track: method, background, diagrams, process notes, and question bank from Plan 4 may begin after Plan 1 Task 1.

The report track must not invent experimental values. It records unverified result statements as structured ledger entries and imports numbers only after Plan 3 marks the corresponding evidence valid.

Publication Plan Task 1 is a hard dependency of Evidence Plan Task 9 because final experiment commits update the claim ledger. Publication Plan Tasks 2-4 may otherwise proceed alongside engineering work.

## Required Review Rhythm

Each task uses this cycle:

1. Implementer runs the listed failing test.
2. Implementer writes the minimum change and runs the listed passing test.
3. A spec reviewer checks behavior against the approved design.
4. A code-quality reviewer checks ownership, errors, reproducibility, and tests.
5. The task is committed separately.

Do not batch unrelated plan tasks into one review or one commit.

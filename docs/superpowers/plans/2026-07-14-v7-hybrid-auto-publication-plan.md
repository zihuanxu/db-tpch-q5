# V7 Hybrid Auto and Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a transparent resident hybrid cost model, evaluate its prediction against fixed ratios, then update claims, paper, learning material, defense material, and the V7 release from audited evidence only.

**Architecture:** Keep the mathematical model pure and testable, calibrate CPU and GPU exactly once during session setup, map the continuous ratio to Arrow batch boundaries, and preserve tuning cost separately. Publication scripts read only audited V7 bundles and profiler manifests; generated values flow into LaTeX and figures without hand-entered benchmark numbers.

**Tech Stack:** C++17, Apache Arrow, CUDA, Python 3.11, pytest, LaTeX/XeLaTeX, SVG.

## Global Constraints

- Formal measured requests cannot be used to choose the ratio.
- `hybrid-auto` reports predicted ratio, realized row ratio, calibration inputs, and tune time.
- Negative auto-selection or scaling results remain visible and become rejected hypotheses.
- Do not modify the user's dirty `docs/FINAL_REPORT.md` or `docs/FINAL_REPORT.docx` unless explicitly requested later.
- Every new numeric paper claim must resolve to a V7 bundle field or profiler artifact.

---

### Task 1: Pure Hybrid Cost Model

**Files:**
- Create: `src/hybrid/hybrid_cost_model.hpp`
- Create: `src/hybrid/hybrid_cost_model.cpp`
- Create: `tests/test_hybrid_cost_model.cpp`
- Modify: `tests/CMakeLists.txt`
- Modify: `CMakeLists.txt`

**Interfaces:**

```cpp
struct HybridCalibration {
  int64_t rows = 0;
  double cpu_ms = 0.0;
  double gpu_kernel_ms = 0.0;
  double gpu_fixed_ms = 0.0;
};

struct HybridPrediction {
  double predicted_cpu_ratio = 0.0;
  double predicted_cpu_ms = 0.0;
  double predicted_gpu_ms = 0.0;
};

arrow::Result<HybridPrediction> predict_hybrid_ratio(
    const HybridCalibration& calibration);
```

- [ ] **Step 1: Write failing analytical tests**

Cover equal speeds, faster CPU, faster GPU, dominant GPU fixed cost, zero/negative inputs, nonfinite timing, ratio clamping, and a hand-calculated example. Require deterministic results within a documented epsilon.

- [ ] **Step 2: Implement the closed-form model**

Solve the equal-finish-time equation, evaluate endpoints, and return the ratio minimizing predicted makespan. Keep the function independent of Arrow tables, CUDA, and global state.

- [ ] **Step 3: Run focused and full CPU tests**

Run: `ctest --test-dir build/arrow-cpu -R hybrid_cost_model --output-on-failure`
Expected: all analytical tests pass and existing CPU/Arrow tests remain green.

- [ ] **Step 4: Commit Task 1**

```bash
git add src/hybrid/hybrid_cost_model.* tests/test_hybrid_cost_model.cpp tests/CMakeLists.txt CMakeLists.txt
git commit -m "feat: add transparent hybrid ratio cost model"
```

### Task 2: Batch-Aware Hybrid Auto Session

**Files:**
- Modify: `src/hybrid/batch_partition.hpp`
- Modify: `src/hybrid/batch_partition.cpp`
- Modify: `src/hybrid/q5_hybrid.hpp`
- Modify: `src/hybrid/q5_hybrid.cpp`
- Modify: `src/session/q5_session_record.hpp`
- Modify: `src/session/q5_session_io.cpp`
- Modify: `src/cli/memq5_arrow_session.cpp`
- Create: `tests/test_q5_hybrid_auto.cpp`
- Modify: `tests/CMakeLists.txt`

- [ ] **Step 1: Write failing batch-mapping and calibration tests**

Require nearest feasible batch boundary, exact row conservation, deterministic ties, safe empty input, one CPU calibration and one GPU calibration only, nonnegative tune time, and equal repeated request hashes.

- [ ] **Step 2: Add an explicit auto option**

Extend `HybridOptions` with `selection=fixed|auto`. Fixed behavior stays unchanged. Auto session setup runs CPU-only and resident GPU-only calibration, calls the pure model, maps prediction to a batch boundary, constructs the final child sessions, and records calibration results.

- [ ] **Step 3: Expose full model provenance**

Setup JSON includes model version, row count, CPU/GPU calibration times, derived throughputs, GPU fixed time, predicted ratio, selected boundary, realized ratio, and tune time. Request rows contain selected ratio but not calibration samples.

- [ ] **Step 4: Run tiny, SF1, and sanitizer gates**

Require exact tiny/SF1 hashes, stable repeated partition counters, and compute-sanitizer zero errors for the auto session.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/hybrid src/session src/cli tests
git commit -m "feat: select resident hybrid split from calibration"
```

### Task 3: Model Evaluation and Hypothesis Decision

**Files:**
- Create: `scripts/evaluate_hybrid_model.py`
- Create: `tests/python/test_evaluate_hybrid_model.py`
- Create at runtime, then commit: `docs/artifacts/v7_hybrid_model`
- Modify: `progress.md`

- [ ] **Step 1: Write failing evaluation tests**

Given synthetic summary rows, verify the best fixed ratio, prediction error, latency regret, break-even request count including tune cost, and hypothesis state. Reject evaluation when samples, scale factors, or lifecycle labels are mixed.

- [ ] **Step 2: Implement evidence-only evaluation**

Read audited SF1/SF10 bundles. Compare auto against CPU-only, GPU-only, fixed 0.25/0.50/0.75, and diagnostic 0.125 increments. Report absolute ratio error, relative latency regret, variability, tune cost, and whether auto beats the best untuned fixed baseline.

- [ ] **Step 3: Freeze SF1 and SF10 decisions**

Do not use a universal conclusion when scale factors disagree. Mark each hypothesis `VERIFIED`, `REJECTED`, or `INCONCLUSIVE` with a machine-readable reason and source manifest digest.

- [ ] **Step 4: Commit Task 3**

```bash
git add scripts/evaluate_hybrid_model.py tests/python/test_evaluate_hybrid_model.py docs/artifacts/v7_hybrid_model progress.md
git commit -m "data: evaluate V7 dynamic hybrid selection"
```

### Task 4: V7 Claim Ledger and Paper Data Pipeline

**Files:**
- Modify: `docs/research/CLAIM_LEDGER.md`
- Create: `scripts/import_v7_paper_evidence.py`
- Create: `tests/python/test_v7_paper_evidence.py`
- Create: `scripts/make_v7_report_assets.py`
- Create: `tests/python/test_v7_report_assets.py`
- Create: `docs/paper/generated/v7_results.tex`
- Create: `docs/assets/v7_resident_sf1_sf10.svg`
- Create: `docs/assets/v7_hybrid_prediction.svg`
- Create: `docs/assets/v7_memory_modes_profile.svg`

- [ ] **Step 1: Add planned V7 claims before importing values**

Create claim IDs for resident amortization, SF10 scaling, memory-mode profile differences, CPU/GPU overlap, and hybrid-auto accuracy. Every claim names its falsification rule and evidence path; initial state is `PLANNED`.

- [ ] **Step 2: Write failing audit-gated import tests**

Import must fail for planned claims, invalid bundle checksum, mismatched commit/dataset, profiler-unavailable claims described as verified, or hand-edited generated values.

- [ ] **Step 3: Resolve claims from audited evidence**

Set each claim to `VERIFIED`, `REJECTED`, or `INCONCLUSIVE` according to the frozen bundles. Keep V5 claims unchanged.

- [ ] **Step 4: Generate LaTeX macros and restrained SVG figures**

Create figures from summary/profiler exports. Include units, scale factor, lifecycle, median definition, and evidence digest in generated metadata. Avoid visualizing profiled duration beside ordinary latency as though directly comparable.

- [ ] **Step 5: Run ledger and asset tests**

Run: `python scripts/validate_claim_ledger.py`
Run: `python -m pytest -q tests/python/test_v7_paper_evidence.py tests/python/test_v7_report_assets.py`
Expected: imports are reproducible and all numeric macros trace to audited evidence.

- [ ] **Step 6: Commit Task 4**

```bash
git add docs/research docs/paper/generated docs/assets scripts tests/python
git commit -m "docs: bind V7 claims and figures to evidence"
```

### Task 5: Research Paper and Handover Documentation

**Files:**
- Modify: `docs/paper/paper.tex`
- Modify: `docs/paper/README.md`
- Modify: `docs/CURRENT_STATUS.md`
- Modify: `docs/GPU_SERVER_RUNBOOK.md`
- Modify: `docs/learning/04-CUDA三种内存模式.md`
- Modify: `docs/learning/05-CPU-GPU混合执行.md`
- Modify: `docs/learning/06-实验方法与统计.md`
- Modify: `docs/learning/07-论文与答辩问答.md`
- Modify: `docs/defense/5-minute-script.md`
- Modify: `docs/defense/10-minute-script.md`
- Modify: `docs/defense/high-risk-questions.md`
- Modify: `docs/process/05-实验执行日志.md`
- Modify: `docs/process/06-问题与决策记录.md`
- Modify: `docs/process/07-最终完成情况.md`
- Modify: `progress.md`

- [ ] **Step 1: Add V7 method and result sections**

Explain cold versus resident lifecycle, SF1/SF10 protocol, independent oracle, cost-model derivation, profiler separation, measured results, negative results, threats to validity, and fixed-Q5 limitations. Use generated macros for numeric values.

- [ ] **Step 2: Update the teaching path**

Make the handover explainable in this order: Q5 semantics, Arrow layout, CPU plan, CUDA memory modes, resident lifecycle, hybrid model, experiment protocol, evidence audit, and conclusions. Add commands the student can safely demonstrate without rerunning SF10.

- [ ] **Step 3: Update defense scripts and high-risk answers**

Include clear answers for why resident matters, what managed/mapped really mean, why SF10 is not enough to prove generality, why DuckDB is an independent oracle, what profiler overhead means, and when auto selection fails.

- [ ] **Step 4: Build and visually inspect the paper**

Run: `cd docs/paper && ./build.sh`
Run: `python scripts/check_paper.py --pdf docs/paper/paper.pdf`
Render all pages to images, inspect for clipping, missing Chinese glyphs, broken figures/tables, and inconsistent references. Record page count and PDF checksum.

- [ ] **Step 5: Commit Task 5**

```bash
git add docs/paper docs/CURRENT_STATUS.md docs/GPU_SERVER_RUNBOOK.md docs/learning docs/defense docs/process progress.md
git commit -m "docs: complete V7 research report and handover"
```

### Task 6: Final Verification, Package, and Tag

**Files:**
- Modify: `scripts/package_submission.py`
- Modify: `scripts/release_audit.py`
- Modify: `tests/python/test_package_submission.py`
- Modify: `tests/python/test_release_audit.py`
- Modify: `docs/SUBMISSION_CHECKLIST.md`
- Modify: `docs/DELIVERY_GUIDE.md`
- Modify: `progress.md`
- Runtime output: `dist/memory-db-tpch-q5-v7.tar.gz`

- [ ] **Step 1: Extend package tests for V7 compact artifacts**

Require source, tests, matrices, compact audited evidence, generated figures/macros, paper PDF, learning/defense docs, and V7 design/plans. Exclude raw TPC-H files, Arrow data, build trees, `.nsys-rep` when too large, `.ncu-rep` when too large, caches, and user-dirty final reports unless explicitly selected.

- [ ] **Step 2: Run the complete verification suite**

Run CPU, Arrow, real-GPU CUDA, RAPIDS, Python, sanitizer, both resident bundle audits, profiler audit, claim ledger, paper check, and release audit. Capture exact pass/skip counts and commands.

- [ ] **Step 3: Build and inspect the V7 archive**

List every archive member, extract into a fresh temporary directory, rerun CPU/Arrow tests plus evidence/release audits there, and compute archive SHA256.

- [ ] **Step 4: Update final checklist and progress**

Record residual limitations and any unavailable optional profiler counter. State which claims were rejected or inconclusive. Confirm `submission-v6-final` still points to `14696bd`.

- [ ] **Step 5: Commit, tag, and verify tag target**

```bash
git add scripts tests/python docs/SUBMISSION_CHECKLIST.md docs/DELIVERY_GUIDE.md progress.md dist/memory-db-tpch-q5-v7.tar.gz
git commit -m "release: package V7 resident SF10 research extension"
git tag -a submission-v7-research -m "V7 resident SF10 research extension"
git rev-parse submission-v7-research^{}
```

Expected: tag resolves to the release commit; V6 tag is unchanged.

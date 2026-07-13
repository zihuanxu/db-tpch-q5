# Benchmark, Evidence, And Profiling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate reproducible cold/resident SF1 and SF10 experiment bundles whose raw samples, failures, statistics, correctness, plots, and profiler evidence are fully traceable.

**Architecture:** Every backend emits versioned JSONL samples; one Python runner controls warmups/repetitions and monitors process/GPU memory. An evidence pipeline writes immutable experiment directories, gates correctness before summarization, computes explicit statistics without dropping failures, and creates figures only from accepted raw rows.

**Tech Stack:** Python 3.11, C++ JSONL output, pandas, NumPy, psutil, pynvml, pytest, Nsight Systems/Compute, Git, SHA-256.

## Global Constraints

- Plans 1 and 2 acceptance gates must pass before formal measurements.
- Formal experiments use warmup 3 and measured repeat 10.
- Cold and resident scenarios are separate experiment rows and separate plot series.
- CPU threads are 1, 2, 4, 8, 16, and 32; hybrid ratios are .75, .50, and .25 CPU share.
- Timing fields have one definition across engines; unsupported phases are zero with a documented `not_applicable` list, not silently omitted.
- Failed, skipped, OOM, malformed, and timed-out runs remain in `raw.csv` with logs and return codes.
- Summary statistics are median, min, max, p95 using linear interpolation, sample standard deviation (`ddof=1`), success count, and failure count.
- A configuration with zero successful measured samples has no performance statistic and remains visibly failed.
- Formal figures never read historical top-level CSV files or manual values; they read one evidence directory selected by manifest id.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `src/engine/q5_run_record.hpp/.cpp` | Versioned C++ JSONL serialization. |
| `src/cli/memq5.cpp` | Cold/resident internal lifecycle and measured sample emission. |
| `baselines/common.py` | Same JSONL schema for Python backends. |
| `scripts/benchmark_schema.py` | Typed Python validation and CSV field order. |
| `scripts/process_monitor.py` | Per-process RSS and GPU memory sampling. |
| `scripts/run_benchmarks.py` | Warmup/repeat execution and failure-preserving raw rows. |
| `scripts/experiment_manifest.py` | Git/data/hardware/software/command manifest capture. |
| `scripts/run_experiment_pipeline.py` | Atomic evidence-directory orchestration. |
| `scripts/audit_experiment.py` | Recompute checksums, matrix coverage, correctness, and statistics. |
| `scripts/verify_benchmark_hashes.py` | Oracle and cross-backend gate. |
| `scripts/summarize_benchmarks.py` | Grouped statistics with failure counts. |
| `scripts/make_report_assets.py` | Tables and accessible SVG/PDF figures. |
| `scripts/run_formal_matrix.py` | Approved SF1/SF10 matrix and resume behavior. |
| `scripts/run_profiling.py` | Nsight Systems/Compute representative profiles. |
| `tests/python/test_benchmark_schema.py` | JSONL schema and unit validation. |
| `tests/python/test_process_monitor.py` | RSS/GPU monitor behavior. |
| `tests/python/test_experiment_pipeline.py` | Directory, failure, atomicity, and checksum tests. |
| `tests/python/test_statistics.py` | Exact median/p95/stddev/failure rules. |
| `tests/python/test_report_assets.py` | Figure provenance and failed-row visibility. |

## Task 1: Define One Versioned Run-Record Contract

**Files:**
- Create: `src/engine/q5_run_record.hpp`
- Create: `src/engine/q5_run_record.cpp`
- Create: `scripts/benchmark_schema.py`
- Create: `tests/python/test_benchmark_schema.py`
- Modify: `src/engine/q5_result_io.cpp`
- Modify: `baselines/common.py`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Consumes: `Q5Result`, engine/scenario parameters, lifecycle metadata, and external monitor values.
- Produces: one `schema_version=1` JSON object per measured sample and `validate_record(record) -> BenchmarkRecord`.

Required flat CSV fields are:

```python
RAW_FIELDS = [
    "schema_version", "experiment_id", "run_uuid", "status", "error_class",
    "return_code", "engine", "scenario", "scale_factor", "region", "date",
    "sample_index", "is_warmup", "threads", "cpu_ratio", "gpu_ratio",
    "gpu_chunk_rows", "memory_scope", "mode_options_json",
    "not_applicable_phases", "result_rows", "result_hash", "oracle_status",
    "load_ms", "plan_build_ms", "host_prepare_ms", "h2d_ms", "cpu_scan_ms",
    "gpu_kernel_ms", "d2h_ms", "overlap_wall_ms", "query_total_ms",
    "process_elapsed_ms", "input_lineitem_rows", "matched_lineitem_rows",
    "cpu_input_rows", "gpu_input_rows", "h2d_bytes", "d2h_bytes",
    "mapped_remote_read_bytes", "cpu_peak_rss_bytes",
    "gpu_peak_memory_bytes", "throughput_rows_per_second", "stdout_log",
    "stderr_log", "started_at_utc", "finished_at_utc"
]
```

- [ ] **Step 1: Write schema validation tests**

```python
def test_ok_record_requires_hash_and_metrics():
    record = valid_record(status="ok")
    assert validate_record(record).result_hash == record["result_hash"]

def test_failed_record_keeps_error_and_logs():
    record = valid_record(status="error", result_hash="", oracle_status="not_run")
    record.update(error_class="ERROR_CUDA_OOM", return_code=1,
                  stderr_log="logs/run.stderr.txt")
    assert validate_record(record).error_class == "ERROR_CUDA_OOM"

def test_units_are_nonnegative():
    record = valid_record(status="ok", h2d_bytes=-1)
    with pytest.raises(ValueError, match="h2d_bytes"):
        validate_record(record)
```

- [ ] **Step 2: Run and verify tests fail**

Run:

```bash
pytest -q tests/python/test_benchmark_schema.py
```

Expected: import failure because the schema module does not exist.

- [ ] **Step 3: Implement typed validation**

Use a frozen dataclass with explicit conversions. Accept statuses only from `ok`, `error`, `timeout`, `oom`, and `skipped_no_gpu`. Require hashes/oracle values only for `ok`; require error class and log paths for all other statuses. Reject NaN, infinity, negative timings/bytes, ratios outside `[0,1]`, and ratios that do not sum to one within `1e-9`.

- [ ] **Step 4: Emit equivalent C++ and Python JSONL**

Add `Q5RunRecord::ToJson()` using nlohmann-json and update Python `emit_benchmark_jsonl` to use the same field names/types. The C++ serializer must use JSON numbers, not quoted numbers.

- [ ] **Step 5: Round-trip C++ output through Python validation**

Run:

```bash
build/cpu-release/memq5 --engine cpu-specialized --dataset /tmp/memq5-tiny-arrow --scenario cold --warmup 0 --repeat 1 --format benchmark-jsonl > /tmp/memq5-record.jsonl
python scripts/benchmark_schema.py validate /tmp/memq5-record.jsonl
pytest -q tests/python/test_benchmark_schema.py
```

Expected: one valid sample and all tests pass.

- [ ] **Step 6: Commit the run-record contract**

```bash
git add CMakeLists.txt src/engine/q5_run_record.hpp src/engine/q5_run_record.cpp src/engine/q5_result_io.cpp baselines/common.py scripts/benchmark_schema.py tests/python/test_benchmark_schema.py
git commit -m "feat: standardize benchmark run records"
```

## Task 2: Implement Comparable Cold And Resident Lifecycles

**Files:**
- Modify: `src/cli/memq5.cpp`
- Modify: `baselines/cudf_q5.py`
- Modify: `baselines/arrow_q5.py`
- Modify: `baselines/duckdb_oracle.py`
- Create: `tests/test_cli_lifecycle.py`

**Interfaces:**
- Consumes: `--scenario cold|resident`, `--warmup N`, `--repeat N`.
- Produces: exactly `repeat` measured JSONL records and no warmup records on stdout.

- [ ] **Step 1: Write lifecycle contract tests**

```python
def test_resident_loads_once_and_emits_three_samples(run_memq5):
    records, stderr = run_memq5("cpu-specialized", scenario="resident", warmup=2, repeat=3)
    assert len(records) == 3
    assert [r["sample_index"] for r in records] == [0, 1, 2]
    assert records[0]["load_ms"] >= 0
    assert records[1]["load_ms"] == 0
    assert records[2]["load_ms"] == 0

def test_cold_rejects_internal_repeat_greater_than_one(run_memq5):
    result = run_raw(scenario="cold", repeat=2)
    assert result.returncode != 0
    assert "cold scenario requires --repeat 1" in result.stderr
```

- [ ] **Step 2: Verify current CLIs fail the lifecycle tests**

Run:

```bash
pytest -q tests/test_cli_lifecycle.py
```

Expected: failures because scenario/warmup/repeat are unsupported.

- [ ] **Step 3: Implement C++ resident sessions**

Load and checksum the Arrow dataset once, build reusable plan/device staging where the engine supports it, execute warmups without stdout, then execute measured samples. Reset result arrays and counters between iterations. Do not reuse query output or skip backend work.

- [ ] **Step 4: Implement Python resident sessions**

Load PyArrow tables once; for cuDF resident mode, call `cudf.from_arrow` before warmups and retain device DataFrames; for cold mode, the outer runner launches one process per sample. DuckDB registers tables once for resident oracle checks.

- [ ] **Step 5: Run lifecycle tests across CPU and available GPU engines**

Run:

```bash
pytest -q tests/test_cli_lifecycle.py
```

Expected: all CPU tests pass; GPU cases pass when present or report only `SKIPPED_NO_GPU`.

- [ ] **Step 6: Commit lifecycle handling**

```bash
git add src/cli/memq5.cpp baselines/cudf_q5.py baselines/arrow_q5.py baselines/duckdb_oracle.py tests/test_cli_lifecycle.py
git commit -m "feat: separate cold and resident query lifecycles"
```

## Task 3: Monitor Process And GPU Memory Without Losing Failures

**Files:**
- Create: `scripts/process_monitor.py`
- Create: `tests/python/test_process_monitor.py`
- Modify: `scripts/run_benchmarks.py`

**Interfaces:**
- Consumes: A child process command, timeout, polling interval, stdout/stderr paths, and optional GPU index.
- Produces: `MonitoredProcessResult(return_code, timed_out, elapsed_ms, peak_rss_bytes, peak_gpu_bytes, started_at_utc, finished_at_utc)`.

- [ ] **Step 1: Write monitor tests**

Use child fixtures that allocate 32 MiB, sleep past timeout, exit 7, and write both streams:

```python
def test_monitor_records_peak_rss(tmp_path):
    result = run_monitored(PYTHON_ALLOCATE_32_MIB, timeout_s=5, poll_ms=10,
                           stdout_path=tmp_path / "out", stderr_path=tmp_path / "err")
    assert result.return_code == 0
    assert result.peak_rss_bytes >= 32 * 1024 * 1024

def test_timeout_keeps_logs(tmp_path):
    result = run_monitored(PYTHON_SLEEP, timeout_s=.1, poll_ms=10,
                           stdout_path=tmp_path / "out",
                           stderr_path=tmp_path / "err")
    assert result.timed_out is True
    assert (tmp_path / "out").exists()
    assert (tmp_path / "err").exists()
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest -q tests/python/test_process_monitor.py
```

Expected: import failure.

- [ ] **Step 3: Implement RSS and GPU polling**

Launch with `subprocess.Popen`, poll the process and recursive children through psutil, sum RSS, and retain the maximum. Query NVML compute-process lists for the child PID and descendants; when NVML is unavailable, set GPU peak to zero and append `gpu_memory_monitor` to `not_applicable` only for CPU engines.

On timeout, send SIGTERM to the process group, wait two seconds, then SIGKILL; preserve both logs and classify `timeout`.

- [ ] **Step 4: Integrate monitored execution into the benchmark runner**

For cold runs, launch one process per warmup and one per measured sample. For resident runs, launch one process per configuration with internal warmups/repeats and apply the process-level peak to each emitted sample, with a `memory_scope=resident_process` metadata field.

- [ ] **Step 5: Run monitor and benchmark smoke tests**

Run:

```bash
pytest -q tests/python/test_process_monitor.py
python scripts/run_benchmarks.py --experiment-id smoke --dataset /tmp/memq5-tiny-arrow --scale-factor tiny --engines cpu-specialized --scenarios cold,resident --thread-list 1,2 --warmup 1 --repeat 2 --output /tmp/memq5-smoke-raw.csv
```

Expected: 8 measured rows, no warmup rows, log paths exist, and memory/timing fields validate.

- [ ] **Step 6: Commit monitoring**

```bash
git add scripts/process_monitor.py scripts/run_benchmarks.py tests/python/test_process_monitor.py
git commit -m "feat: monitor benchmark process and GPU memory"
```

## Task 4: Create Atomic Evidence Directories And Manifests

**Files:**
- Create: `scripts/experiment_manifest.py`
- Create: `scripts/audit_experiment.py`
- Modify: `scripts/run_experiment_pipeline.py`
- Create: `tests/python/test_experiment_pipeline.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: Experiment id, data manifest, build executable, matrix parameters, and tool commands.
- Produces: the approved directory tree with immutable manifest and per-command logs.

- [ ] **Step 1: Write evidence-structure tests**

```python
def test_pipeline_creates_required_tree(fake_backend, tiny_dataset, tmp_path):
    run_dir = run_pipeline(backend=fake_backend, dataset=tiny_dataset,
                           output_root=tmp_path, experiment_id="exp-1")
    for relative in ["manifest.json", "environment.json", "commands.txt", "raw.csv",
                     "summary.csv", "correctness.json", "logs", "profile", "figures"]:
        assert (run_dir / relative).exists()

def test_existing_id_is_never_deleted(tmp_path):
    existing = tmp_path / "exp-1"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        create_run_dir(tmp_path, "exp-1")
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest -q tests/python/test_experiment_pipeline.py
```

Expected: failures because the current pipeline uses a different layout and destructive `--force` behavior.

- [ ] **Step 3: Implement the manifest**

Record:

```json
{
  "manifest_version": 1,
  "experiment_id": "sf1-final-20260713T120000Z",
  "git": {"commit": "d9ff2de", "branch": "codex/tpch-q5-arrow-hybrid-final", "dirty": false, "diff_sha256": ""},
  "dataset": {"manifest_sha256": "0000000000000000000000000000000000000000000000000000000000000000", "scale_factor": "1"},
  "matrix": {"engines": [], "scenarios": [], "threads": [], "hybrid_cpu_ratios": []},
  "protocol": {"warmup": 3, "repeat": 10, "timeout_seconds": 1800},
  "hardware": {},
  "software": {},
  "started_at_utc": "2026-07-13T12:00:00Z",
  "completed_at_utc": "2026-07-13T13:00:00Z",
  "status": "complete"
}
```

Capture full `git diff --binary` only when dirty and hash it. Formal final runs require `dirty=false`; smoke runs may be dirty but must say so.

- [ ] **Step 4: Make directory publication atomic**

For experiment id `sf1-final-20260713`, write under `sf1-final-20260713.in-progress`; never delete an existing id. On successful pipeline completion, fsync and rename to `sf1-final-20260713`. On failure, rename to `sf1-final-20260713.failed` and preserve all generated files. Apply the same suffix rules to every validated id.

- [ ] **Step 5: Record exact commands and environment**

`commands.txt` contains shell-escaped commands in execution order. `environment.json` includes CPU model/topology, RAM, NUMA, GPU names/UUID/memory, PCIe link width/speed, OS/kernel, compiler, CMake, Arrow, PyArrow, CUDA toolkit/driver, cuDF, DuckDB, Python, power mode, and relevant environment variables.

`audit_experiment.py` must reopen the manifest, hash every listed artifact, validate `raw.csv` through `benchmark_schema.py`, recompute matrix coverage and grouped statistics, compare recomputed `summary.csv`, validate figure provenance, and exit nonzero on any mismatch.

- [ ] **Step 6: Run pipeline tests and a smoke pipeline**

Run:

```bash
pytest -q tests/python/test_experiment_pipeline.py
python scripts/run_experiment_pipeline.py --id tiny-smoke-$(date -u +%Y%m%dT%H%M%SZ) --dataset /tmp/memq5-tiny-arrow --scale-factor tiny --engines cpu-specialized,arrow-acero --scenarios cold,resident --thread-list 1,2 --warmup 1 --repeat 2
```

Expected: tests pass; smoke evidence has every required file and no destructive overwrite option.

- [ ] **Step 7: Commit evidence-directory orchestration**

```bash
git add scripts/experiment_manifest.py scripts/run_experiment_pipeline.py scripts/audit_experiment.py tests/python/test_experiment_pipeline.py .gitignore
git commit -m "feat: preserve reproducible experiment evidence"
```

## Task 5: Gate Correctness Before Statistics

**Files:**
- Modify: `scripts/verify_benchmark_hashes.py`
- Create: `tests/python/test_correctness_gate.py`
- Modify: `scripts/run_experiment_pipeline.py`

**Interfaces:**
- Consumes: `raw.csv`, expected tiny/SF1 file or DuckDB oracle, and backend groups.
- Produces: `correctness.json` with per-configuration status and pipeline exit behavior.

- [ ] **Step 1: Write correctness-gate tests**

Cover all-match, one hash mismatch, official two-decimal mismatch despite equal formatted hash, failed backend row, no successful samples, and a non-core SF10 OOM.

```python
def test_hash_mismatch_blocks_summary(tmp_path):
    report = verify(raw_with_hashes("aaa", "bbb"), expected=EXPECTED)
    assert report["ok"] is False
    assert report["configurations"][1]["status"] == "HASH_MISMATCH"
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest -q tests/python/test_correctness_gate.py
```

Expected: failures because the old script checks only a single global hash set.

- [ ] **Step 3: Implement per-configuration oracle checks**

Group by scale, region, date, engine, scenario, threads, ratio, and chunk rows. Require all successful repeats in one group to share an exact hash. Compare one representative exact output artifact to the configured oracle. Mark non-core SF10 OOM as `EXPECTED_RECORDED_FAILURE` only when the manifest labels that engine non-core; all core backend failures block acceptance.

- [ ] **Step 4: Block summaries and plots on correctness failure**

The pipeline writes `correctness.json` first. Summary/plot steps execute only when `correctness.ok` is true; on false, preserve raw/log files and finish the evidence directory with status `correctness_failed`.

- [ ] **Step 5: Run tests and smoke mismatch injection**

Run:

```bash
pytest -q tests/python/test_correctness_gate.py
python scripts/verify_benchmark_hashes.py /tmp/memq5-smoke-raw.csv --expected tests/fixtures/q5_expected/tiny.csv --output /tmp/memq5-correctness.json
```

Expected: tests pass and smoke report is valid.

- [ ] **Step 6: Commit correctness gating**

```bash
git add scripts/verify_benchmark_hashes.py scripts/run_experiment_pipeline.py tests/python/test_correctness_gate.py
git commit -m "test: gate benchmark evidence on Q5 correctness"
```

## Task 6: Compute Transparent Statistics And Provenance-Safe Figures

**Files:**
- Modify: `scripts/summarize_benchmarks.py`
- Modify: `scripts/make_report_assets.py`
- Create: `tests/python/test_statistics.py`
- Create: `tests/python/test_report_assets.py`

**Interfaces:**
- Consumes: Validated `raw.csv` and `correctness.json`.
- Produces: `summary.csv`, a figure provenance JSON, CSV tables, SVG figures, and PDF figures.

- [ ] **Step 1: Write exact-statistic tests**

For values `[1,2,3,4,100]`, assert min 1, max 100, median 3, p95 80.8 under linear interpolation, sample standard deviation from NumPy `ddof=1`, success count 5, and explicit failure count. Test a group with one success and one failure and a group with no successes.

- [ ] **Step 2: Write figure provenance tests**

Assert every figure has a sidecar entry containing experiment id, raw CSV SHA-256, correctness JSON SHA-256, filters, grouping, metric, generation command, and generated-at timestamp. Assert a failed configuration appears in a failure table and is never represented as a zero-height successful bar.

- [ ] **Step 3: Verify current scripts fail**

Run:

```bash
pytest -q tests/python/test_statistics.py tests/python/test_report_assets.py
```

Expected: failures because p95/stddev/provenance/failure rules are incomplete.

- [ ] **Step 4: Implement deterministic grouped statistics**

Use pandas with explicit numeric coercion after schema validation. Group by all configuration dimensions. Calculate throughput as `input_lineitem_rows / (query_total_ms / 1000)` only for successful rows with positive query time. Write stable column order and sort order.

- [ ] **Step 5: Implement figures and source tables**

Generate at least:

- total query time by backend and scale;
- copy/managed/mapped phase breakdown;
- specialized CPU thread scaling;
- hybrid ratio versus pure CPU/GPU;
- cold versus resident;
- cuDF versus hand-written CUDA;
- memory usage;
- failure/skip table.

Use non-single-hue colors, clear units, error bars from min/max or p95, and labels that fit print width. Every plotted point must map to one summary row.

- [ ] **Step 6: Run tests and regenerate smoke assets**

Run:

```bash
pytest -q tests/python/test_statistics.py tests/python/test_report_assets.py
python scripts/summarize_benchmarks.py /tmp/memq5-smoke-raw.csv --correctness /tmp/memq5-correctness.json --output /tmp/memq5-summary.csv
python scripts/make_report_assets.py /tmp/memq5-summary.csv --raw /tmp/memq5-smoke-raw.csv --correctness /tmp/memq5-correctness.json --output-dir /tmp/memq5-figures
```

Expected: tests pass; summary/provenance and figures are generated.

- [ ] **Step 7: Commit statistics and figures**

```bash
git add scripts/summarize_benchmarks.py scripts/make_report_assets.py tests/python/test_statistics.py tests/python/test_report_assets.py
git commit -m "feat: summarize and plot traceable benchmarks"
```

## Task 7: Encode The Formal SF1/SF10 Matrix

**Files:**
- Create: `experiments/formal_matrix.yml`
- Create: `scripts/run_formal_matrix.py`
- Create: `tests/python/test_formal_matrix.py`
- Modify: `docs/BENCHMARK_PROTOCOL.md`

**Interfaces:**
- Consumes: Dataset registry, approved matrix YAML, build paths, and output root.
- Produces: one experiment bundle per scale/scenario group with resumable configuration ids.

- [ ] **Step 1: Write matrix-expansion tests**

Assert exact configuration counts and no meaningless GPU thread sweep:

```python
def test_formal_matrix_dimensions():
    configs = expand_matrix(load_matrix("experiments/formal_matrix.yml"))
    assert cpu_threads(configs, "cpu-specialized") == {1, 2, 4, 8, 16, 32}
    assert cpu_threads(configs, "arrow-acero") == {1, 2, 4, 8, 16, 32}
    assert cpu_threads(configs, "gpu-copy") == {1}
    assert hybrid_ratios(configs) == {.75, .50, .25}
    assert all(c.warmup == 3 and c.repeat == 10 for c in configs)
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest -q tests/python/test_formal_matrix.py
```

Expected: import/file failure.

- [ ] **Step 3: Define the matrix explicitly**

The YAML contains datasets tiny/synthetic/SF1/SF10; engines Arrow Acero, specialized CPU, three CUDA modes, hybrid, cuDF; scenarios cold/resident; CPU thread list; hybrid ratios; fixed `record_batch_rows=1048576`; fixed `gpu_chunk_rows=1048576`; warmup/repeat; per-scale timeout; core/non-core labels; and oracle paths.

Tiny is correctness-only and not included in performance plots. Synthetic dimensions include key density, selectivity, and row count in its manifest.

- [ ] **Step 4: Implement validated expansion and resume behavior**

Compute a stable configuration id as SHA-256 of canonical JSON. Resume only missing configuration ids into a new continuation evidence directory; never append to or mutate a completed raw CSV. Merge runs through a separate `derived_from` manifest that lists source experiment ids and checksums.

- [ ] **Step 5: Dry-run and smoke-run the matrix**

Run:

```bash
python scripts/run_formal_matrix.py --matrix experiments/formal_matrix.yml --dry-run
python scripts/run_formal_matrix.py --matrix experiments/formal_matrix.yml --only-scale tiny --output-root results/experiments
```

Expected: dry-run prints all stable ids and commands; tiny matrix completes correctness without performance claims.

- [ ] **Step 6: Commit the formal matrix**

```bash
git add experiments/formal_matrix.yml scripts/run_formal_matrix.py tests/python/test_formal_matrix.py docs/BENCHMARK_PROTOCOL.md
git commit -m "feat: define the formal Q5 experiment matrix"
```

## Task 8: Capture Representative Nsight Evidence

**Files:**
- Create: `experiments/profiling.yml`
- Create: `scripts/run_profiling.py`
- Create: `scripts/check_hybrid_overlap.py`
- Create: `tests/python/test_profile_commands.py`
- Modify: `src/cuda/q5_gpu_executor.cu`
- Modify: `src/hybrid/q5_hybrid.cu`

**Interfaces:**
- Consumes: Representative formal configurations and NVTX ranges.
- Produces: `.nsys-rep`, Nsight CSV summaries, `.ncu-rep`, selected metric CSV, and machine-readable overlap checks.

- [ ] **Step 1: Write profile-command and overlap-parser tests**

Use fixture CSV rows with CPU NVTX start/end and CUDA interval start/end. Assert overlap duration `max(0, min(end)-max(start))`, overlap ratio, and false result for serial intervals.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest -q tests/python/test_profile_commands.py
```

Expected: import failure.

- [ ] **Step 3: Add stable NVTX ranges**

Name ranges exactly:

```text
q5.load
q5.plan_build
q5.host_prepare
q5.h2d
q5.cpu_scan
q5.gpu_kernel
q5.d2h
hybrid.cpu_scan
hybrid.gpu_h2d
hybrid.gpu_kernel
```

Include configuration id and chunk index as NVTX payload metadata, not in the range name.

- [ ] **Step 4: Define representative profiles**

Profile SF1 and SF10 for gpu-copy, managed with/without prefetch, mapped, and best candidate hybrid ratio. Nsight Compute collects achieved occupancy, DRAM throughput, L2 hit rate, atomic throughput/contention-related metrics available on the target GPU, and kernel duration.

- [ ] **Step 5: Implement profile execution and overlap checks**

Capture exact commands and tool versions. Export Nsight Systems SQLite/CSV data, calculate overlap duration/ratio, and write `profile/overlap.json`. A hybrid profile passes only when overlap duration is positive and both CPU/GPU ranges process nonzero rows.

- [ ] **Step 6: Run profile tests and one smoke profile**

Run:

```bash
pytest -q tests/python/test_profile_commands.py
python scripts/run_profiling.py --config experiments/profiling.yml --only hybrid-sf1-smoke --output /tmp/memq5-profile
python scripts/check_hybrid_overlap.py /tmp/memq5-profile/nsys.sqlite --output /tmp/memq5-profile/overlap.json
```

Expected: parser tests pass and overlap JSON records a positive overlap or clearly fails the collaboration claim.

- [ ] **Step 7: Commit profiling automation**

```bash
git add experiments/profiling.yml scripts/run_profiling.py scripts/check_hybrid_overlap.py tests/python/test_profile_commands.py src/cuda/q5_gpu_executor.cu src/hybrid/q5_hybrid.cu
git commit -m "feat: capture Nsight Q5 profiling evidence"
```

## Task 9: Run Formal Experiments

**Files:**
- Create through scripts: `results/experiments/sf1-final-20260713/` and `results/experiments/sf10-final-20260713/`
- Modify through verification: `docs/research/CLAIM_LEDGER.md`

**Interfaces:**
- Consumes: Clean Git commit, validated SF1/SF10 datasets, GPU server, formal matrix, and profile config.
- Produces: Complete accepted evidence bundles and ledger evidence links.

- [ ] **Step 1: Freeze machine state and verify clean source**

Run:

```bash
git status --porcelain
nvidia-smi -q
python scripts/capture_environment.py --output /tmp/memq5-preflight-environment.json
python scripts/self_check.py --preset gpu-release --tiny-dataset /tmp/memq5-tiny-arrow --sf1-dataset /tmp/memq5-sf1-arrow --official-q5 'data/tpch_tools/TPC-H V3.0.1/dbgen/answers/q5.out'
```

Expected: no source diff for formal runs, GPU state captured, full self-check passes.

- [ ] **Step 2: Generate and validate SF10 Arrow IPC**

Run:

```bash
python scripts/prepare_arrow_dataset.py --input data/tpch_sf10 --output data/arrow/sf10-rb1048576 --scale-factor 10 --batch-rows 1048576 --source-command 'dbgen -s 10' --validate-foreign-keys
python baselines/duckdb_oracle.py --dataset data/arrow/sf10-rb1048576 --region ASIA --date 1994-01-01 --format json > /tmp/memq5-sf10-oracle.json
```

Expected: manifest/checksums validate and DuckDB emits five exact nation results.

- [ ] **Step 3: Run controlled synthetic sensitivity experiments**

Run:

```bash
python scripts/generate_synthetic_tpch_q5.py --output /tmp/memq5-synthetic-tbl --customers 100000 --orders 500000 --lineitems 5000000 --suppliers 50000 --key-density 0.5 --match-selectivity 0.4 --asia-selectivity 0.6 --seed 7 --arrow-output /tmp/memq5-synthetic-arrow --batch-rows 1048576
python scripts/run_formal_matrix.py --matrix experiments/formal_matrix.yml --only-scale synthetic --experiment-id synthetic-sensitivity-20260713 --output-root results/experiments
```

Expected: generator manifest records requested/observed controls and the synthetic experiment passes its DuckDB oracle.

- [ ] **Step 4: Run SF1 and SF10 matrices**

Run:

```bash
python scripts/run_formal_matrix.py --matrix experiments/formal_matrix.yml --only-scale sf1 --experiment-id sf1-final-20260713 --output-root results/experiments
python scripts/run_formal_matrix.py --matrix experiments/formal_matrix.yml --only-scale sf10 --experiment-id sf10-final-20260713 --output-root results/experiments
```

Expected: each scale produces complete or failure-preserving evidence; all core backends have ten successful measured rows per required configuration.

- [ ] **Step 5: Run representative profiles**

Run:

```bash
python scripts/run_profiling.py --config experiments/profiling.yml --output-root results/experiments
```

Expected: profile files, exported metrics, commands, and overlap JSON are attached to the matching experiment manifests.

- [ ] **Step 6: Verify evidence independently**

Run:

```bash
python scripts/audit_experiment.py results/experiments/sf1-final-20260713
python scripts/audit_experiment.py results/experiments/sf10-final-20260713
```

Expected: checksums, row counts, matrix coverage, correctness, statistics, figures, and profiler links all pass; `commands.txt` contains the exact expanded commands.

- [ ] **Step 7: Commit publishable evidence**

Stage only manifests, raw/summary/correctness CSV/JSON, text logs required for interpretation, figures, and compact profiler exports. Do not stage large `.nsys-rep`, `.ncu-rep`, Arrow IPC, or `.tbl`; record their checksums and external artifact location.

```bash
git add results/experiments docs/research/CLAIM_LEDGER.md
git commit -m "data: add final Q5 experiment evidence"
```

## Plan Acceptance

Run:

```bash
pytest -q tests/python/test_benchmark_schema.py tests/python/test_process_monitor.py tests/python/test_experiment_pipeline.py tests/python/test_correctness_gate.py tests/python/test_statistics.py tests/python/test_report_assets.py tests/python/test_formal_matrix.py tests/python/test_profile_commands.py
python scripts/audit_experiment.py results/experiments/sf1-final-20260713
python scripts/audit_experiment.py results/experiments/sf10-final-20260713
```

Expected:

- Every required configuration is present with ten successful measured samples for core backends.
- Warmups are recorded in the manifest/logs but excluded from raw measured rows.
- Cold and resident rows use distinct lifecycle definitions.
- Summary values recompute exactly from raw CSV.
- Failed non-core runs remain visible and are not plotted as successes.
- Every figure has provenance and every paper-ready value can be traced to experiment id, summary row, raw rows, and Git commit.
- Hybrid overlap evidence is machine-checked, not inferred from total times.

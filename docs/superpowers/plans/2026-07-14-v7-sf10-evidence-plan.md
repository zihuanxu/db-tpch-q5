# V7 SF10 and Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate an auditable TPC-H SF10 Arrow dataset, establish an independent DuckDB oracle, run the resident correctness/performance matrix, and freeze separate SF1/SF10 V7 evidence bundles.

**Architecture:** Keep data outside Git, make each preparation stage idempotent, and record hashes at every boundary. A scalable DuckDB SQL path reads pipe-delimited files directly instead of inserting Python rows. The V7 evidence layer reuses V5 audit principles but has its own schema, setup records, request coverage, and immutable bundle digest.

**Tech Stack:** Python 3.11, dbgen 3.0.1, Apache Arrow 23.0.1, DuckDB, YAML, pytest, SHA256.

## Global Constraints

- Do not modify or overwrite `data/tpch_sf1`, `data/tpch_sf1_q5`, or `data/tpch_sf1_q5_arrow`.
- Require at least 40 GiB free before dbgen and recheck free space after each stage.
- Raw TPC-H data, prepared tables, and Arrow files stay gitignored and out of release archives.
- Performance collection starts only after every enabled backend matches DuckDB exactly.
- SF1 and SF10 are separate experiment IDs and separate bundle digests.

---

### Task 1: Idempotent SF10 Preparation Orchestrator

**Files:**
- Create: `scripts/prepare_v7_sf10.py`
- Create: `tests/python/test_prepare_v7_sf10.py`
- Modify: `.gitignore`
- Modify: `progress.md`

**Interfaces:**

```python
@dataclass(frozen=True)
class Sf10Paths:
    raw: Path
    q5: Path
    arrow: Path

preflight(paths: Sf10Paths, minimum_free_bytes: int) -> dict[str, object]
stage_state(paths: Sf10Paths) -> dict[str, str]
prepare_sf10(args: argparse.Namespace) -> dict[str, object]
```

- [ ] **Step 1: Write failing preflight and stage-state tests**

Use temporary directories and fake manifests. Require rejection for less than 40 GiB, nonempty unmanifested output, wrong scale factor, incomplete six-table output, and attempts to reuse an SF1 manifest.

- [ ] **Step 2: Confirm focused tests fail**

Run: `python -m pytest -q tests/python/test_prepare_v7_sf10.py`
Expected: import failure because `scripts.prepare_v7_sf10` is missing.

- [ ] **Step 3: Implement dry-run and resumable stages**

The command accepts explicit `--dbgen`, `--raw-dir`, `--q5-dir`, `--arrow-dir`, and `--minimum-free-gib`. It emits one JSON summary and runs only missing stages:

```text
dbgen -s 10 -f
python scripts/prepare_tpch_q5_data.py --source-dir data/tpch_sf10 --output-dir data/tpch_sf10_q5 --scale-factor 10
python scripts/prepare_arrow_dataset.py --input data/tpch_sf10_q5 --output data/tpch_sf10_q5_arrow --scale-factor 10 --batch-rows 1048576 --source-command "dbgen -s 10 -f"
```

Do not infer completion from directory existence; require valid manifests and six expected tables. Capture command, return code, elapsed time, byte counts, and free space.

- [ ] **Step 4: Run dry-run and unit tests**

Run: `python scripts/prepare_v7_sf10.py --dry-run --dbgen 'data/tpch_tools/TPC-H V3.0.1/dbgen/dbgen'`
Run: `python -m pytest -q tests/python/test_prepare_v7_sf10.py`
Expected: dry-run prints three stages, tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add .gitignore scripts/prepare_v7_sf10.py tests/python/test_prepare_v7_sf10.py progress.md
git commit -m "feat: add resumable SF10 preparation pipeline"
```

### Task 2: Scalable Independent DuckDB Oracle

**Files:**
- Modify: `baselines/duckdb_q5.py`
- Create: `scripts/write_q5_oracle.py`
- Modify: `tests/python/test_baseline_exact_mvp.py`
- Create: `tests/python/test_write_q5_oracle.py`

**Interfaces:**

```python
create_tpch_views(con, data_dir: Path) -> None
run_q5(data_dir: Path, region: str, start_date: str) -> list[ResultRow]
write_oracle(data_dir: Path, output: Path, region: str, start_date: str) -> dict
```

- [ ] **Step 1: Write failing tiny direct-scan oracle tests**

Require the tiny result hash `248d10b6ee352953`, exact ordered rows, no Python `executemany`, rejection for missing files, and an oracle JSON containing SQL text hash, source-table hashes, result rows, result hash, DuckDB version, region, and date.

- [ ] **Step 2: Replace row insertion with typed DuckDB CSV views**

Use `read_csv` with `delim='|'`, `header=false`, declared column types, and projected TPC-H columns. Keep revenue integer exact by converting extended price and discount to scaled decimals inside SQL. Do not use floating-point revenue.

- [ ] **Step 3: Implement the oracle writer and round-trip verification**

Write atomically to a temporary file and rename only after the result can be read back and its hash recomputed. Include the exact SQL/query-version identifier.

- [ ] **Step 4: Run tiny and SF1 oracle regression**

Run: `python -m pytest -q tests/python/test_baseline_exact_mvp.py tests/python/test_write_q5_oracle.py`
Run: `python scripts/write_q5_oracle.py --data-dir data/tpch_sf1_q5 --output /tmp/sf1-q5-oracle.json`
Expected: hash `542abf4003633c7c` and five rows.

- [ ] **Step 5: Commit Task 2**

```bash
git add baselines/duckdb_q5.py scripts/write_q5_oracle.py tests/python
git commit -m "feat: add scalable DuckDB Q5 oracle"
```

### Task 3: Generate and Audit SF10 Data

**Files:**
- Modify: `progress.md`
- Runtime output only: `data/tpch_sf10`, `data/tpch_sf10_q5`, `data/tpch_sf10_q5_arrow`
- Runtime output only: `data/tpch_sf10_q5/oracle.json`

- [ ] **Step 1: Record the preflight**

Run: `df -B1 /home/xuzihuan/db-tpch-q5`
Run: `sha256sum 'data/tpch_tools/TPC-H V3.0.1/dbgen/dbgen'`
Expected: at least 40 GiB free; record dbgen digest and version source.

- [ ] **Step 2: Run the preparation pipeline**

Run: `python scripts/prepare_v7_sf10.py --dbgen 'data/tpch_tools/TPC-H V3.0.1/dbgen/dbgen' --raw-dir data/tpch_sf10 --q5-dir data/tpch_sf10_q5 --arrow-dir data/tpch_sf10_q5_arrow`
Expected: all three stages `complete`, six Arrow files and manifest.

- [ ] **Step 3: Validate source and Arrow manifests**

Run existing source and Arrow validators with scale factor 10. Check exact expected TPC-H row counts, Arrow schema, batch counts, total bytes, and all SHA256 values.

- [ ] **Step 4: Generate the independent SF10 oracle**

Run: `python scripts/write_q5_oracle.py --data-dir data/tpch_sf10_q5 --output data/tpch_sf10_q5/oracle.json`
Expected: five ordered rows and a stable nonempty result hash.

- [ ] **Step 5: Rerun preparation to prove idempotence**

Expected: no dbgen or conversion command runs; manifests and oracle digest remain unchanged.

- [ ] **Step 6: Record measured sizes and commit the audit note**

Update `progress.md` with raw/Q5/Arrow bytes, row counts, manifest hashes, oracle hash, elapsed preparation time, and remaining disk. Do not commit generated data.

```bash
git add progress.md
git commit -m "docs: record audited SF10 dataset preparation"
```

### Task 4: SF10 Cross-Backend Correctness Gate

**Files:**
- Create: `scripts/v7_correctness_gate.py`
- Create: `tests/python/test_v7_correctness_gate.py`
- Create: `experiments/v7_sf10_correctness.yml`
- Modify: `progress.md`

**Interfaces:**

```python
compare_rows(expected: list[dict], actual: list[dict]) -> list[str]
verify_correctness(directory: Path, matrix: Path, oracle: Path) -> dict
```

- [ ] **Step 1: Write failing mismatch tests**

Cover missing backend, wrong hash, same hash but wrong row value, duplicate nation, failed process, skipped GPU, and a fully passing fixture.

- [ ] **Step 2: Implement strict row and hash comparison**

The gate requires CPU specialized, Acero, copy, managed, mapped, fixed hybrid, hybrid-auto when available, and cuDF. It records `passed`, `failed`, or `unavailable`; only all `passed` allows formal collection.

- [ ] **Step 3: Run SF10 smoke sessions**

Use one warmup and two measured requests per backend. Pin one GPU UUID. Preserve stdout/stderr and process metrics even on failure.

- [ ] **Step 4: Audit against DuckDB**

Run: `python scripts/v7_correctness_gate.py --runs docs/artifacts/v7_sf10_correctness --matrix experiments/v7_sf10_correctness.yml --oracle data/tpch_sf10_q5/oracle.json`
Expected: `ok=true`, one observed result hash, no skip.

- [ ] **Step 5: Commit Task 4**

```bash
git add scripts/v7_correctness_gate.py tests/python/test_v7_correctness_gate.py experiments/v7_sf10_correctness.yml progress.md
git commit -m "test: gate V7 SF10 results with DuckDB"
```

### Task 5: V7 Evidence Bundle and Formal Matrices

**Files:**
- Create: `scripts/v7_evidence_bundle.py`
- Create: `scripts/summarize_v7_records.py`
- Create: `tests/python/test_v7_evidence_bundle.py`
- Create: `tests/python/test_summarize_v7_records.py`
- Create: `experiments/v7_formal_sf1.yml`
- Create: `experiments/v7_formal_sf10.yml`

- [ ] **Step 1: Write failing coverage, checksum, and summary tests**

Require exactly one setup row per session, 3 warmups and 10 measured requests, contiguous indexes, stable session hash, correct lifecycle, expected configuration keys, no profiler rows in latency summaries, and bundle-relative logs. Tampering with any artifact must fail audit.

- [ ] **Step 2: Implement V7 summary statistics**

Group by scale factor, engine, threads, fixed/auto ratio, and lifecycle. Report median, mean, minimum, maximum, population standard deviation, p25, p75, p95, setup cost, resident bytes, and amortized 1/10/100 request cost.

- [ ] **Step 3: Implement finalize and audit commands**

Bundle files include `setups.csv`, `warmups.csv`, `raw.csv`, `summary.csv`, `summary.md`, `correctness.json`, `environment.json`, `commands.txt`, `matrix.yml`, dataset/oracle references, logs, `manifest.json`, and `manifest.sha256`. Audit recomputes summaries and every checksum.

- [ ] **Step 4: Freeze formal matrices**

SF1 and SF10 each specify 3 warmups, 10 measured requests, CPU/Acero thread sweeps, three CUDA modes, cuDF, fixed hybrid 0.25/0.50/0.75, diagnostic hybrid ratios, and hybrid-auto. Matrix validation rejects unknown fields or duplicate configurations.

- [ ] **Step 5: Run all Python tests**

Run: `python -m pytest -q tests/python`
Expected: no product failure; GPU-dependent tests run in their declared environment.

- [ ] **Step 6: Commit Task 5**

```bash
git add scripts experiments tests/python
git commit -m "feat: add auditable V7 evidence bundles"
```

### Task 6: Collect and Freeze SF1/SF10 Resident Evidence

**Files:**
- Create at runtime, then commit audited compact evidence: `docs/artifacts/v7_sf1_resident`
- Create at runtime, then commit audited compact evidence: `docs/artifacts/v7_sf10_resident`
- Modify: `progress.md`
- Modify: `docs/CURRENT_STATUS.md`

- [ ] **Step 1: Capture the fixed environment**

Record git commit, clean/dirty paths, compiler/Arrow/CUDA/cuDF/DuckDB versions, CPU model, memory, NUMA, GPU name/UUID/driver, clocks/power policy when readable, and exact commands.

- [ ] **Step 2: Run SF1 formal resident matrix**

Run one session process per configuration with 3 warmups and 10 measured requests. Do not reuse calibration or query samples between configuration keys.

- [ ] **Step 3: Run SF10 formal resident matrix**

Use the same protocol and pinned GPU. Stop formal collection if the correctness gate changes state.

- [ ] **Step 4: Finalize and independently audit both bundles**

Expected: complete coverage, no failed measured row, one exact hash per scale factor, summaries reproducible from raw records, and checksum audit clean.

- [ ] **Step 5: Record negative results honestly**

Mark hypotheses supported or rejected from medians and variability. Preserve slow or memory-heavy configurations; do not silently omit them.

- [ ] **Step 6: Commit the evidence milestone**

```bash
git add docs/artifacts/v7_sf1_resident docs/artifacts/v7_sf10_resident docs/CURRENT_STATUS.md progress.md
git commit -m "data: freeze V7 SF1 and SF10 resident evidence"
```

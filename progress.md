# V2-V6 Development Progress

Last updated: 2026-07-14 (Asia/Shanghai)

This file is the durable execution ledger for the staged course-project release.
Each version remains independently buildable and is tagged only after its own
acceptance checks pass. User-owned legacy drafts in `docs/FINAL_REPORT.md` and
`docs/FINAL_REPORT.docx` are preserved and excluded from stage commits.

## Version Map

| Version | Scope | Status |
| --- | --- | --- |
| V1 | Minimal exact Q5 submission and SF1 evidence | FROZEN (`submission-v1-minimal`) |
| V2 | Canonical Arrow IPC data and Arrow-native C++ CPU queries | COMPLETED (`submission-v2-arrow-cpu`) |
| V3 | Arrow-native `gpu-copy`, `gpu-managed`, and `gpu-mapped` | IN PROGRESS |
| V4 | Concurrent CPU-GPU hybrid query and canonical Arrow cuDF baseline | PENDING |
| V5 | Reproducible benchmark/evidence pipeline and formal experiment bundle | PENDING |
| V6 | Claim-controlled paper, takeover material, CI, package, and release audit | PENDING |

## V2 - Arrow Data And CPU

### V2.1 Arrow IPC loader

- [x] Add an optional Arrow 23.0.1 CMake build that leaves the default build
  dependency-free.
- [x] Load all six Q5 Arrow IPC tables in C++.
- [x] Validate manifest format, filenames, sizes, SHA-256 values, row counts,
  record-batch counts, exact schemas, and required non-null columns.
- [x] Add a committed tiny Arrow fixture, C++ tests, and `memq5_arrow_check`.
- [x] Validate both the tiny fixture and the local official SF1 Arrow dataset.
- [x] Run the default CPU/CUDA self-check after adding the optional Arrow path.

Evidence recorded on 2026-07-14:

- Arrow build: CMake/Ninja with Arrow 23.0.1 succeeded.
- Arrow CTest: 6/6 passed, including corruption detection.
- Tiny Arrow CLI: six tables validated with checksums.
- SF1 Arrow CLI: six tables validated; `lineitem` has 6,001,215 rows.
- Default `python3 scripts/self_check.py`: CPU build/test, CUDA build/test on the
  real GPU, data validation, and tiny pipeline all passed.
- Review round 1 found permissive JSON conversion and missing rejection-path
  coverage. Strict types, duplicate-key rejection, canonical SHA-256 validation,
  constant-time digest comparison, and negative tests were added with TDD.
- Review round 2 found integer narrowing and Release-assert test hazards. Integer
  target-range checks and side-effect-free assertions were added with TDD.
- A clean `Release` Arrow build under `/tmp/memq5-v21-release-build` compiled and
  passed 6/6 tests, including every strict manifest rejection case.
- V2.1 was committed as `39b31dd`, tagged `submission-v2.1-arrow-loader`, and
  packaged as `dist/memory-db-tpch-q5-v2.1.tar.gz`.
- The extracted V2.1 package passed the default CPU/CUDA self-check and a clean
  Arrow Release build with 6/6 tests.

### V2.2 Arrow-native CPU query

- [x] Expose typed canonical Arrow tables while retaining the validated map view.
- [x] Add a specialized CPU backend that consumes zero-copy Arrow batch views.
- [x] Add an Acero implementation for dictionary-boundary decode, filters, and
  hash joins, followed by checked exact revenue accumulation.
- [x] Add `memq5_arrow_query` and explicit result counters for both CPU engines.
- [x] Prove tiny equality and official SF1 oracle equality.

Evidence recorded on 2026-07-14:

- Arrow Debug/Release CTest: 13/13 passed, including both engines, both CLI
  smoke tests, and three invalid-argument rejection tests.
- Specialized and Acero SF1 outputs both have result hash `542abf4003633c7c`.
- Both SF1 CSV outputs passed `scripts/verify_q5_oracle.py` against the official
  TPC-H V3.0.1 `q5.out`.
- Specialized scan partitions global row ranges across Arrow batches and checks
  duplicate keys, Decimal128 scale, multiplication, local sums, and merge sums.
- Acero performs the relational filter/join path; final exact scale-4
  accumulation remains checked C++ code rather than an overstated Acero decimal
  aggregate claim.
- Review hardening: `verify_q5_oracle.py` now parses signed int64 exact values,
  checks scale-4-to-scale-2 formatting, recomputes the C++ FNV-1a result hash,
  and rejects negative/fractional counters or invalid timings. Its focused test
  suite passes 14/14, including int64 bounds, duplicate metadata, and row-count
  consistency checks added after independent review.
- Review hardening: all legacy CUDA modes now count matched rows in the shared
  kernel and report input rows plus H2D/D2H/mapped-read bytes. A test-first
  assertion failed on the previous zero counters, then passed on the real GPU
  for copy, managed, and mapped after the implementation.
- Full V2 gate: oracle Python tests 14/14, Arrow/baseline Python tests 12/12,
  default CPU/CUDA/tiny self-check, clean Arrow Release build, and both SF1
  engines with the strengthened oracle all passed.
- V2 feature work was committed as `b21a42d`. The source archive
  `dist/memory-db-tpch-q5-v2.tar.gz` was extracted into a clean directory; the
  extracted package passed the full CPU/CUDA/tiny self-check on the real GPU and
  a clean Arrow Release build with 13/13 tests.

## V3 - Arrow-native CUDA Modes

- [x] Establish CUDA ownership/error primitives for Arrow-backed buffers.
- [x] Use one exact scale-4 aggregation kernel for every CUDA memory mode.
- [x] Implement and verify `gpu-copy` from Arrow buffers.
- [x] Implement and verify `gpu-managed` and `gpu-mapped` from Arrow data.
- [x] Run correctness, CUDA tests, and representative sanitizer checks.

Evidence recorded on 2026-07-14:

- The shared Arrow plan prevents CPU/GPU filter semantics from drifting; the
  lineitem staging path handles the tiny fixture's three RecordBatches and a
  zero-row table.
- Arrow+CUDA Release CTest passed 18/18 on the real GPU, including all three
  internal paths and all three CLI paths.
- `gpu-copy`, `gpu-managed`, and `gpu-mapped` each produced SF1 hash
  `542abf4003633c7c` and passed the official strengthened oracle.
- NVIDIA compute-sanitizer memcheck completed with zero errors.
- A new three-mode overflow test initially exposed a Release-only host crash.
  GDB showed the crash in test fixture construction, while compute-sanitizer
  reported no device error. Root cause: CUDA test flags passed `-UNDEBUG` only
  to the host compiler, so nvcc removed assertion expressions (including builder
  side effects). Tests now pass `-UNDEBUG` directly to nvcc, contain a compile
  guard against `NDEBUG`, and keep setup work outside assertions. The real-GPU
  overflow test and compute-sanitizer then passed.
- The deliverable explicitly records that Arrow CUDA extension classes are not
  available in this environment; the canonical Arrow tables are staged into
  native CUDA device, managed, or mapped buffers without claiming otherwise.

## V4 - Hybrid And cuDF

- [ ] Partition Arrow batches without dropping or duplicating rows.
- [ ] Execute CPU and GPU partitions concurrently and merge exact results.
- [ ] Verify 75/25, 50/50, and 25/75 partitions.
- [ ] Make cuDF consume the canonical Arrow dataset.
- [ ] Record overlap evidence or explicitly reject the overlap claim.

## V5 - Evidence And Experiments

- [ ] Define and validate one versioned run-record schema.
- [ ] Separate cold-start and resident scenarios.
- [ ] Preserve stdout, stderr, failures, environment, memory, and checksums.
- [ ] Add correctness gating, statistics, matrix definitions, and traceable plots.
- [ ] Run the feasible formal SF1/SF10 matrix and preserve honest failures.

## V6 - Publication And Release

- [ ] Maintain a claim ledger linked to evidence.
- [ ] Finish process records and progressive takeover lessons.
- [ ] Import only verified evidence into the CjC paper and rebuild the PDF.
- [ ] Add open-source metadata, CPU CI, GPU runbook, and release audit.
- [ ] Produce and independently self-check the final V6 package.

## Current Action

Implement one Arrow-backed CUDA execution interface and prove copy, managed, and
mapped mode correctness before moving directly to V4.

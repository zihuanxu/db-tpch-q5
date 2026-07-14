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
| V3 | Arrow-native `gpu-copy`, `gpu-managed`, and `gpu-mapped` | COMPLETED (`submission-v3-arrow-cuda`) |
| V4 | Concurrent CPU-GPU hybrid query and canonical Arrow cuDF baseline | COMPLETED |
| V5 | Reproducible benchmark/evidence pipeline and formal experiment bundle | COMPLETED |
| V6 | Claim-controlled paper, takeover material, CI, package, and release audit | PENDING |
| V7 | Resident sessions, SF10 evidence, profiling, and hybrid-auto | IN PROGRESS |

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
- V3 was committed as `b0667b9`. The archive
  `dist/memory-db-tpch-q5-v3.tar.gz` was extracted into a clean directory and
  rebuilt as Arrow+CUDA Release; all 18 tests passed on the real GPU.

## V4 - Hybrid And cuDF

- [x] Partition Arrow batches without dropping or duplicating rows.
- [x] Execute CPU and GPU partitions concurrently and merge exact results.
- [x] Verify 75/25, 50/50, and 25/75 partitions.
- [x] Make cuDF consume the canonical Arrow dataset.
- [x] Record duration-overlap evidence and explicitly reject the stronger
  kernel-overlap claim because no Nsight/NVTX timeline has been captured.

Evidence recorded on 2026-07-14:

- A deterministic batch partitioner covers every row once and splits at most
  one batch boundary; focused partition tests pass.
- `hybrid-arrow` launches `gpu-copy` asynchronously, executes the CPU prefix in
  the calling thread, waits for both paths even on a partial error, and merges
  revenue and counters with checked int64 addition.
- Tiny correctness passed for CPU ratios 0.25, 0.50, and 0.75. Official SF1
  runs at all three ratios produced hash `542abf4003633c7c`, matched 7,243 rows,
  and passed the strengthened official oracle.
- SF1 partition rows were 1,500,304/4,500,911, 3,000,608/3,000,607, and
  4,500,911/1,500,304 for CPU/GPU respectively.
- The cuDF 26.06.0 test observed six Arrow-to-cuDF conversions and passed the
  tiny oracle. Its official SF1 output passed the same oracle as C++ engines.
- A separate Python 3.11 pytest target under `/tmp/memq5-pytest311` was used
  because the RAPIDS environment did not include pytest. Reusing Python 3.13
  site-packages polluted NumPy and produced an invalid skip, so that method was
  rejected.
- Hybrid compute-sanitizer memcheck reported zero errors. The complete
  Arrow+CUDA Release suite passed 21/21 before the final review-hardening pass.
- Review follow-up maps CUDA allocation failures to `CapacityError`, releases
  mapped pinned memory on constructor failure, counts mapped logical reads
  along the kernel's short-circuit path, and standardizes no-device CTest skips
  on return code 77. The mapped regression first failed at 556 versus 120
  bytes, then passed at the expected 120 bytes on the real GPU; all seven GPU
  tests are cleanly skipped in a no-device environment.
- The final post-review gate rebuilt successfully and passed Arrow+CUDA CTest
  21/21, oracle tests 14/14, Arrow/baseline tests 12/12, cuDF test 1/1, and
  hybrid compute-sanitizer with zero errors. All three hybrid SF1 ratios and
  the cuDF SF1 output were rerun and passed the official oracle. Independent
  re-review approved the four CUDA hardening fixes with no remaining finding.

## V5 - Evidence And Experiments

- [x] Define and validate one versioned run-record schema.
- [x] Separate cold-start records from resident requests; reject unsupported
  resident measurement instead of substituting cold processes.
- [x] Preserve stdout, stderr, failures, environment, CPU memory, and checksums.
- [x] Add hash correctness gating, explicit statistics, and a frozen SF1 matrix.
- [x] Run the feasible formal SF1 matrix and preserve honest failures.

Evidence recorded so far on 2026-07-14:

- The current focused V5/base suite passes 43/43 tests, including schema,
  launch/timeout/RSS monitoring, strict backend-output attribution, statistics,
  exact formal-matrix expansion, evidence coverage, checksum, and official
  oracle tests. Existing Arrow/baseline tests pass 12/12 and cuDF GPU tests
  pass 2/2.
- A strict tiny smoke across specialized CPU, Acero, copy, managed, mapped,
  hybrid, and cuDF produced seven valid measured rows with zero failures and
  the expected hash `248d10b6ee352953`.
- A mixed cold/resident CPU smoke produced eight successful cold rows and eight
  explicit `ERROR_RESIDENT_UNSUPPORTED` rows. All 16 rows passed schema
  validation, demonstrating that unsupported data is retained rather than
  silently dropped or relabeled.
- The V5 scope intentionally omits true resident sessions, NVML GPU peak-memory
  sampling, SF10 generation, and profiler-derived plots. These omissions are
  documented rather than represented by synthetic results.
- Three review rounds found and closed incomplete-matrix certification,
  process-launch loss, permissive CSV attribution, cuDF timing/counter mismatch,
  external-log references, non-recomputed summaries, and unhashed matrix
  inputs. Final re-review reported no Critical, Important, or Minor finding and
  approved the V5 evidence tooling for the formal run.
- Formal SF1 completed all 19 exact configurations with 3 warmups and 10
  measured cold-process samples per configuration: 57/57 warmups and 190/190
  measurements succeeded, with zero failures and the only result hash
  `542abf4003633c7c`.
- The evidence directory `docs/artifacts/v5_sf1` contains 503 checksummed
  artifacts, including every command and 494 per-process stdout/stderr logs.
  Finalize and a fresh audit both passed with no coverage, checksum, matrix,
  summary, or manifest-digest error. Run records use bundle-relative log paths,
  so the evidence remains auditable after relocation. The finalized manifest
  SHA-256 is
  `e3337842d367b541b10f3ecb425d1ba0c7a0378555a87f6c42669ed831b5e660`.
- Median internal query times show a valid negative hybrid result: specialized
  CPU at 16 threads was 61.414 ms, cuDF query time was 116.427 ms, and the best
  hybrid ratio (75% CPU) was 222.832 ms. The implementation is concurrent and
  correct but does not outperform the specialized CPU at SF1.
- Median CUDA-mode internal times were 314.151 ms for explicit copy, 358.158 ms
  for managed, and 412.264 ms for mapped. Mapped avoided explicit H2D timing but
  was not transfer-free and was the slowest of the three in this experiment.
- Final portability hardening first reproduced the absolute-log-path defect,
  then added a regression test and changed new run records to bundle-relative
  paths. The migrated bundle passed a fresh 190-record/57-warmup audit. The
  dependency-free Python suite passed 44/44, while the PyArrow/cuDF suite passed
  14/14 in the Python 3.11 RAPIDS environment. An attempted all-in-one run in
  the base interpreter failed collection because optional `pyarrow` and `cudf`
  packages are intentionally absent there; no product test failed.

## V6 - Publication And Release

- [x] Maintain a claim ledger linked to evidence.
- [x] Finish process records and progressive takeover lessons.
- [x] Import only verified evidence into the CjC paper and rebuild the PDF.
- [x] Add open-source metadata, CPU CI, GPU runbook, and release audit.
- [x] Produce and independently self-check the final V6 package.

Evidence recorded so far on 2026-07-14:

- Added `docs/research/CLAIM_LEDGER.md` with ten explicit claim states. Verified
  claims link code, tests, V5 evidence, paper locations, and limitations;
  unsupported hybrid speedup and resident-mode conclusions remain rejected.
- The claim parser first accepted only two malformed rows. A regression test
  requiring all ten rows exposed the issue; the table and validator now pass
  four focused tests and verify the finalized evidence manifest digest.
- Added a deterministic evidence-to-LaTeX importer. Six focused publication
  tests prove that it refuses incomplete evidence or unsupported claims and
  emits values from `docs/artifacts/v5_sf1`, rather than handwritten numbers.
- Reworked `docs/paper/paper.tex` around the canonical Arrow input, 19 frozen
  configurations, 3 warmups, 10 measurements, current result hash, query versus
  process timing, and the negative hybrid result. The CjC-template PDF builds
  successfully as a three-page paper; a final PDF content check is still due.
- Added seven reconstructed process records, each marked as reconstructed from
  repository evidence, plus an explicit Tencent-document external-action file.
  The process validator passes and names the formal `v5-sf1-final` run.
- Added seven progressive takeover lessons covering Q5, Arrow, CPU, CUDA memory
  modes, hybrid execution, experiments, and defense. Every code link, exercise
  answer, and required section passes the learning-material validator.
- Added 5-minute and 10-minute defense scripts, high-risk questions, and a final
  architecture explanation. Updated the current status, defense cheat sheet,
  GPU runbook, and handover guide to use the V5 hash and timings.
- Added Apache-2.0 metadata, citation/contribution/changelog files, CPU/GPU Conda
  environments, CMake presets, a CPU Dockerfile, a tiny oracle fixture, and a
  GitHub Actions CPU workflow. Release-file and CI static tests pass 4/4.
- Rebuilt the evidence-linked CjC paper as a three-page A4 PDF. Text extraction
  found the frozen hash and current medians, while the log had no overfull box,
  undefined reference, or LaTeX error; the first page was also visually checked.
- Added `check_paper.py`, `release_audit.py`, and a deterministic package builder
  with an internal file manifest and external archive SHA256. The audit reports
  six engineering categories passing and keeps Tencent/GitHub actions external.
- The first Arrow CPU CI run exposed mixed system/Conda OpenSSL libraries. A
  failing regression preceded the fix: CI now requires `CONDA_PREFIX`, starts
  from a fresh cache, and points CMake at that environment's OpenSSL. The full
  gate then passed 14/14 CTest and 75 Python tests with two expected skips.
- The final source package audit exposed two portability bugs in sequence. Git's
  quoted non-ASCII paths omitted all Chinese process/learning filenames; after
  switching to NUL-delimited paths, the no-`.git` extracted tree exposed a test
  assumption. Both now have regressions and the corrected package has 686 files.
- A completely new package extraction passed release audit, a clean Arrow
  Release build, 14/14 CTest, 79 Python tests with two expected device skips,
  and both specialized/Acero tiny oracle checks.
- Arrow+CUDA rebuilt successfully. CTest reported zero failures across 21 tests;
  seven CUDA runtime tests skipped because this final sandbox has no NVIDIA
  device node. Earlier V4 real-RTX-4090 21/21 and sanitizer evidence, plus the
  complete V5 matrix, remain the runtime proof. RAPIDS/PyArrow tests passed 14/14.
- A Docker build was attempted after the static release checks, but the local
  daemon socket denied access even outside the file sandbox. The image is not
  claimed as runtime-verified; Conda/CMake remains the tested reproduction path.
- Independent final review found three release-audit defects. Regressions now
  prove that a missing archive payload returns an error instead of a traceback,
  required files cannot be replaced by directories, and `.dockerignore` is
  covered by the release gate. All three fixes passed in the extracted package.
- Paper provenance now binds SHA256 values for `paper.tex`, generated results,
  and the built PDF. The final auditable package contains 687 files and passed
  both root and clean-extraction release audits with no engineering failure.
- A post-freeze audit found that cuDF tests checked package availability but not
  CUDA device availability. In the final sandbox this produced two
  `cudaErrorNoDevice` failures while C++ CUDA tests correctly skipped. The test
  prerequisite now uses `numba.cuda.is_available()`: the no-device rerun passes
  12 tests and explicitly skips 2, while the earlier real-GPU 14/14 evidence is
  retained as the runtime result.

## Current Action

Continue V7 from the manifest-validated, resumable SF10 preparation pipeline.

## V7 - SF10 Preparation

- [x] Add a manifest-validated SF10 preparation orchestrator that rejects
  incomplete, unmanifested, wrong-scale, and SF1-reused data before reuse.
- [x] Require at least 40 GiB before generation, record each stage command,
  return code, elapsed time, output bytes, and post-stage free space.
- [x] Add focused temporary-directory tests and a dry-run that reports only
  stages whose valid manifests are absent.

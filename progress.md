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

Continue V7 from the audited SF10 dataset and resident hybrid implementation;
finish the session CLI, real cross-backend correctness smoke, profiler captures,
hybrid-auto integration, formal evidence, and publication update.

## V7 - SF10 Preparation

- [x] Generate official TPC-H SF10 source tables with the bundled dbgen.
- [x] Project the six Q5 tables and convert them to canonical Arrow IPC.
- [x] Validate source foreign keys, exact row counts, Arrow schemas, batches,
  file sizes, and checksums.
- [x] Generate an independent exact DuckDB Q5 oracle.
- [ ] Complete all V7 resident/profiler/formal evidence and publication tasks.

Audited SF10 evidence recorded on 2026-07-14:

- Source validation passed with 5 regions, 25 nations, 100,000 suppliers,
  1,500,000 customers, 15,000,000 orders, and 59,986,052 lineitems. The Q5
  date window contains 2,275,919 orders.
- Generation produced 11,232,137,500 bytes of raw `.tbl` data in 108.167 s;
  Q5 projection produced 9,783,951,987 bytes in 130.260 s; Arrow conversion
  produced 2,592,337,190 bytes in 308.414 s. Final free space was
  300,731,887,616 bytes.
- The Arrow manifest SHA-256 is
  `44ddfec1e929264542ce28276a900b0dac3968ee102cae3475574a933e7af8ea`.
  It records 229 lineitem batches and 58 orders batches at 262,144 rows per
  batch. `memq5_arrow_check` re-read every table and verified all six file
  checksums.
- A full preparation rerun returned all three stages as `complete` with an
  empty command list, proving that the resumable pipeline did not regenerate or
  reconvert already audited data.
- DuckDB 1.4.5 produced exact ordered hash `b1351a421ba8dcfd`; oracle file
  SHA-256 is
  `a397078a8896f22ce16d41ad17f1de0dc04fd1ef1de26ed78f8b01de62a351ec`.
  Revenue values in scale-4 integer units are INDIA 5,368,625,879,995;
  CHINA 5,353,508,299,282; VIETNAM 5,322,693,887,176; JAPAN
  5,267,668,371,444; and INDONESIA 5,231,768,523,189.
- The correctness gate now recomputes hashes from exact rows, binds every run
  to the SF10 Arrow manifest/query identity, rejects extra retry records, and
  classifies unavailable GPU-backed hybrid sessions without treating them as
  valid evidence. Formal collection still requires every backend to pass.
- Resident cuDF verification ran on GPU 0 with no device skips: all 14 focused
  tests passed, including one-time six-table conversion, synchronized setup and
  request timing, stable repeated hashes, strict dates, and no-device handling.
- One non-formal SF10 cold correctness smoke covered specialized CPU, Acero,
  `gpu-copy`, `gpu-managed`, `gpu-mapped`, fixed 50/50 hybrid, and cuDF. Every
  backend returned the five exact DuckDB rows and hash `b1351a421ba8dcfd`.
  The smoke retained diagnostic counters: copy moved 1,440,121,048 input bytes,
  managed reported 1,440,121,260 H2D bytes including managed output movement,
  mapped reported zero H2D and 960,652,652 logical remote-read bytes, and the
  50/50 hybrid conserved 29,993,026 CPU plus 29,993,026 GPU rows. These
  single-run timings are not formal performance claims.
- A separate SF10 cuDF resident smoke loaded the dataset once (8,521.566 ms),
  retained 1,452,521,599 GPU bytes, then produced three identical exact rows
  and hashes. The one warmup request took 392.866 ms and the two diagnostic
  measured requests took 288.542 and 274.886 ms. These values validate the
  lifecycle implementation only; they remain outside the formal bundle.
- The merged C++ resident implementation was rebuilt from current HEAD. All
  37 Arrow+CUDA CTests ran on GPU 0 and passed; both the CUDA-session and
  hybrid-session binaries then completed compute-sanitizer memcheck with zero
  errors. This supersedes the transient 35/37 result observed while two agents
  were editing the build concurrently.
- Current-head SF10 resident diagnostics used one warmup and two measured
  requests. Specialized CPU measured 25.430/25.348 ms, copy 29.864/28.849 ms,
  managed 38.377/38.433 ms, mapped 502.818/481.885 ms, and 50/50 hybrid
  17.397/14.375 ms. All requests emitted the same five exact rows and hash.
  Setup records now separate dataset load, plan/build/allocation/initial H2D,
  and resident host/GPU/pinned bytes. These two-sample values are diagnostic,
  not the formal median results used for paper claims.
- The V7 resident runner then completed the checked-in SF10 smoke matrix with
  6 setups, 6 warmups, 12 measured requests, and zero process failures. Every
  request matched `b1351a421ba8dcfd`. Its 1+2 protocol remains diagnostic and
  is not reused as the later 3+10 formal experiment.
- The strict SF10 correctness gate passed all seven fixed implementations:
  specialized CPU, Arrow Acero, copy, managed, mapped, fixed hybrid, and cuDF.
  Acero is explicitly recorded as a cold execution because it has no resident
  session interface; the other six records come from the resident smoke. The
  gate recomputed each exact row hash and observed only
  `b1351a421ba8dcfd`.
- Current profiler and resident Python regressions passed independently: 80
  profiler/parser tests and 55 protocol/schema/runner/correctness-gate tests.
  The profiler bundle now rejects path escapes, ambiguous profile identities,
  unverified dataset manifests, and unsupported metric claims; raw-report to
  exported-stat linkage remains documented as a non-cryptographic residual.
- Hybrid-auto now calibrates one CPU-only and one resident GPU-only request
  during session setup, evaluates the closed-form model, and maps the predicted
  split to the nearest Arrow batch boundary. The implementation commit is
  `7ff9ff3`; focused CPU tests, three real-GPU hybrid tests, and both CUDA builds
  passed before formal collection.
- A current-head SF10 3+10 validation predicted a 0.372540 CPU ratio and
  realized 0.371457 at the 22,282,240-row batch boundary. All ten measured
  requests returned `b1351a421ba8dcfd`, with request totals from 9.631 to
  10.076 ms. Session setup was 4,974.422 ms and `tune_ms` was 2,892.435 ms, so
  any publication claim must report the large one-time calibration cost rather
  than presenting request latency alone.
- Independent reviews blocked formal collection until two evidence-boundary
  fixes are complete. The resident runner must preserve partial failures,
  reject truncated/mislabeled configurations, distinguish unavailable metrics
  from true zero, and bind binary/environment/GPU identity. The profiler bundle
  must enforce its fixed SF1/SF10 matrix and prove command, result, device,
  metric-discovery, and Q5-kernel provenance. Constructive bypass tests are
  being added for each issue before new captures are accepted.

- [x] Add a manifest-validated SF10 preparation orchestrator that rejects
  incomplete, unmanifested, wrong-scale, and SF1-reused data before reuse.
- [x] Require at least 40 GiB before generation, record each stage command,
  return code, elapsed time, output bytes, and post-stage free space.
- [x] Add focused temporary-directory tests and a dry-run that reports only
  stages whose valid manifests are absent.

## V7 - Formal Resident Evidence (2026-07-14)

- [x] Freeze and independently audit the SF1 18-configuration resident matrix.
- [x] Freeze and independently audit the SF10 18-configuration resident matrix.
- [x] Recompute every warmup and measured result against the canonical oracle.
- [x] Evaluate hybrid-auto against the seven-point fixed-ratio sweep.
- [x] Complete the ten-profile NSYS/NCU matrix.
- [x] Complete the V7 publication, learning, and release refresh.

The formal source commit is `021becd1b10f84aaed87858e45eb15a915cf6adc`.
Both bundles use one RTX 4090 (`GPU-3bbdf12f-4f01-2280-3744-e42f3544e76e`),
driver 595.71.05, Arrow resident sessions, cuDF 26.06, three warmups, and ten
measured requests per configuration. Each bundle contains 18 setups, 54
warmups, and 180 measured requests. All eight correctness backends passed and
all requests reproduced the independent hash (`542abf4003633c7c` for SF1 and
`b1351a421ba8dcfd` for SF10). The copied bundle audits both return `ok=true`.

Selected resident medians are:

- SF1 specialized CPU (16 threads) 3.201 ms, copy 1.267 ms, managed 1.440 ms,
  mapped 22.971 ms, cuDF 12.773 ms, and Acero (32 threads) 311.461 ms.
- SF10 specialized CPU (32 threads) 14.955 ms, copy 15.416 ms, managed
  15.066 ms, mapped 358.582 ms, cuDF 27.906 ms, and Acero (32 threads)
  3124.388 ms.
- The best fixed hybrid split was 0.125 CPU at SF1 (1.160 ms) and 0.375 CPU at
  SF10 (10.054 ms). Hybrid-auto selected 0.262091 and 0.288425 respectively,
  giving 1.553 ms at SF1 and 10.980 ms at SF10. Its measured regret was
  33.89% and 9.21%, so the auto model is useful but not optimal.
- Setup cost is substantial and remains separate from resident request time.
  For example, SF10 hybrid-auto setup was 7844.079 ms, while its request median
  was 10.980 ms. The report must state both values and the amortization scope.

The first SF10 formal attempt was interrupted externally during the fifth
configuration and left one truncated Acero JSON line. Its partial directory
was not finalized or used. A new empty directory was run to completion in one
persistent terminal session; only that rerun is copied into the audited
artifact tree.

## V7 - Formal Profiler Evidence (2026-07-14)

- [x] Capture SF1/SF10 copy, managed, mapped, fixed 0.5 hybrid, and hybrid-auto.
- [x] Preserve NSYS raw reports, exports, application JSONL, and tool output.
- [x] Preserve NCU selected metrics, replay mode, report CSV, and GPU provenance.
- [x] Finalize and audit the complete 10-profile bundle with `ok=true`.
- [x] Export a 4.3 MiB compact publication copy with 183 checksum-covered files.

The complete bundle is `/tmp/memq5-v7-profiler-80dba7a-rerun2`; its source
manifest SHA-256 is
`fb24875ef52cd00c80cc94985018290d32fbe1b6d8af2eb05d7bfaccbea186a7`.
The collector run exposed two trust-boundary bugs after all ten captures were
already present. NSYS progress text was mixed with the profiled application's
JSONL, and NSYS replay rewrote `profile.sqlite` beside the original report.
Both bugs received failing regressions before the fixes. Application JSONL and
tool stdout are now separate, and replay occurs from a temporary copy so an
audit cannot modify captured evidence.

The compact copy is `docs/artifacts/v7_profiler`. It intentionally omits the
2.7 GiB dataset hard links, SQLite sidecars, the large supported-metric
universe, and huge collector metadata. It retains source identity, commands,
tool versions, raw NSYS reports, four NSYS CSV exports, selected NCU reports,
parsed observations, and deterministic checksums. It is a publication copy,
not a replacement for full-bundle audit.

Selected profiler observations are kept separate from ordinary latency:

- SF10 NSYS Q5-kernel totals were 15.34 ms (copy), 15.59 ms (managed), and
  135.25 ms (mapped). NCU durations were 16.72, 16.93, and 129.81 ms.
- Copy and managed reported about 545 MB of device-DRAM reads at SF10; mapped
  reported only 2.89 MB because most input traffic came remotely from mapped
  host pages rather than device DRAM.
- The SF10 fixed-0.5 hybrid NCU kernel was 8.16 ms with about 273 MB of DRAM
  reads. NSYS placed a 12.31 ms CPU scan and 8.27 ms GPU request inside a
  12.51 ms measured request, supporting overlap without treating profiler
  wall time as benchmark latency.

The compact exporter received a separate trust-boundary review. It now rejects
unsafe profile identifiers and symlink output escapes, verifies
`orchestration.json` and profile identities against the source manifest,
hashes the exact bytes it parses or copies, allows only named optional logs,
and publishes directories as 0755 and files as 0644. Its 19 focused tests and
148 profiler regressions passed. A fresh export from the real complete bundle
produced 183 files; every line in `checksums.sha256` verified.

## V7 - Final Publication Verification (2026-07-14)

- [x] Rebuild the four-page CjC paper with evidence-bound provenance.
- [x] Validate the 21-claim ledger, process records, and learning links.
- [x] Pass the default Python suite excluding environment-specific modules:
  417 passed and 6 skipped.
- [x] Pass the Arrow/cuDF Python 3.11 suite: 22 passed.
- [x] Pass all 45 CTest entries with zero failures in the current environment;
  15 CUDA runtime entries were explicitly skipped because the final shell no
  longer exposed a CUDA device.
- [x] Build `/tmp/memq5-v7-final.tar.gz` with 1034 files, verify its external
  SHA-256, extract it into a new directory, and pass release audit there.

The first final CTest exposed one missing no-device guard in
`test_q5_hybrid_auto_cuda`: it attempted GPU setup before reaching its local
skip check and aborted. The guard was moved to the CUDA test entry point, the
target was rebuilt, and both the focused test and the complete 45-test CTest
run then passed. This affects only test behavior without a visible GPU; it does
not alter the formal benchmark implementation or evidence.

The final publication review also closed five evidence-gate bypasses. Release
audit now validates compact profiler schema, canonical coverage, identity, and
required captures in addition to checksums; package audit repeats that check
inside the archive. Paper checking regenerates all macros from both audited
bundles, the hybrid model, setups, and the claim ledger. Model regret,
predicted/selected/realized ratios, samples, statuses, fixed curves, binary and
software identities are validated rather than trusted from generated files.

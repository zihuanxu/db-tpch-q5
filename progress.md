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
| V2 | Canonical Arrow IPC data and Arrow-native C++ CPU queries | IN PROGRESS |
| V3 | Arrow-native `gpu-copy`, `gpu-managed`, and `gpu-mapped` | PENDING |
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

### V2.2 Arrow-native CPU query

- [ ] Expose the canonical `ArrowTpchDataset` interface.
- [ ] Refactor the specialized CPU backend to consume Arrow buffers directly.
- [ ] Add an Arrow Acero implementation of Q5.
- [ ] Extend the CLI and JSON result contract for both CPU engines.
- [ ] Prove tiny equality and official SF1 oracle equality.

## V3 - Arrow-native CUDA Modes

- [ ] Establish CUDA ownership/error primitives for Arrow-backed buffers.
- [ ] Use one exact scale-4 aggregation kernel for every CUDA memory mode.
- [ ] Implement and verify `gpu-copy` from Arrow buffers.
- [ ] Implement and verify `gpu-managed` and `gpu-mapped` from Arrow data.
- [ ] Run correctness, CUDA tests, and representative sanitizer checks.

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

Finish V2.1 packaging and review, then implement V2.2 without waiting for a
stage confirmation.

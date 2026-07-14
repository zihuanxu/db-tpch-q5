# V7 Research Extension Execution Index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this index. Do not wait for user approval between milestones; stop only for a correctness blocker, unsafe operation, or unavailable required resource.

**Goal:** Execute the approved V7 design as four auditable milestones while preserving the V6 fallback release.

## Plans

1. `2026-07-14-v7-resident-session-plan.md`
2. `2026-07-14-v7-sf10-evidence-plan.md`
3. `2026-07-14-v7-profiler-plan.md`
4. `2026-07-14-v7-hybrid-auto-publication-plan.md`

## Dependency Order

```text
resident CPU session
  -> resident CUDA session
  -> resident hybrid/session CLI/cuDF
  -> V7 schema and SF1 smoke
  -> SF10 preparation + DuckDB oracle
  -> hybrid cost model + auto session
  -> SF10 correctness gate
  -> SF1/SF10 formal resident evidence
  -> NVTX + profiler captures
  -> model evaluation + claim resolution
  -> paper/handover + release
```

The SF10 preparation orchestrator and scalable DuckDB oracle may be implemented while CUDA resident work is in progress because their write sets are disjoint. Profiler parser work may also proceed early, but formal captures wait for the final resident and hybrid-auto binaries.

## Milestone Gates

### M1 Resident

- Repeated tiny/SF1 requests have stable exact hashes.
- Copy setup owns initial H2D and resident requests report zero input H2D.
- CPU, CUDA, hybrid, cuDF, schema, and process-attribution tests pass.
- Real GPU tests run rather than skip; representative sanitizer reports zero errors.

### M2 SF10 and Hybrid Auto

- SF10 source/Arrow manifests and DuckDB oracle are independently audited.
- Every backend agrees with DuckDB before formal timing.
- The auto ratio uses only setup calibration and exposes model provenance.
- Both formal matrices have exactly 3 warmups and 10 measured requests per configuration.

### M3 Profiler and Evidence

- Nsight Systems exports contain required NVTX ranges and CUDA activity.
- Nsight Compute metrics are discovered on the actual GPU or marked structurally unavailable.
- Profiler times are absent from ordinary latency summaries.
- SF1, SF10, and profiler bundle checksum audits pass.

### M4 Publication and Release

- New claims are resolved from audited evidence, including negative results.
- Paper values and figures are generated rather than hand-entered.
- PDF builds and passes structural plus visual checks.
- Fresh archive extraction passes CPU/Arrow and evidence/release audits.
- `submission-v7-research` points to the final release commit and `submission-v6-final` remains unchanged.

## Execution Discipline

- Use test-driven development for each feature or bug fix.
- After each task, review the diff and run its focused tests before committing.
- Update `progress.md` with commands, observed counts, hashes, failures, decisions, and the next task.
- Do not stage `docs/FINAL_REPORT.md` or `docs/FINAL_REPORT.docx`; they contain user-owned changes.
- Never replace failed evidence with fabricated or estimated numbers.

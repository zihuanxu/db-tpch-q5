# Changelog

## 7.0.0 - 2026-07-14

- Added resident sessions that separate setup from repeated Q5 requests.
- Froze audited SF1 and SF10 matrices with 18 configurations, 54 warmups, and
  180 measured requests per scale factor.
- Added fixed-ratio and model-selected hybrid execution; retained the measured
  33.89%/9.21% auto-policy regret as a negative result.
- Added optional NVTX ranges, ten NSYS/NCU profiles, strict full-bundle audit,
  and a 4.3 MiB compact publication copy with deterministic checksums.
- Updated the CjC paper and handover entry points to distinguish V5 cold,
  V7 setup, V7 resident request, and profiler-only timings.

## 6.0.0 - 2026-07-14

- Added an evidence-backed CjC paper, claim ledger, process record, takeover
  lessons, defense scripts, CPU CI, release audit, and open-source metadata.
- Froze the V5 SF1 matrix: 19 configurations, 190 measured cold runs, 57
  warmups, one official result hash, and a portable checksummed evidence bundle.

## 5.0.0 - 2026-07-14

- Added strict run records, process monitoring, formal matrix expansion,
  summary recomputation, and evidence finalization/audit.

## 4.0.0 - 2026-07-14

- Added Arrow batch CPU-GPU hybrid execution and an Arrow-fed cuDF baseline.

## 3.0.0 - 2026-07-14

- Added Arrow `gpu-copy`, `gpu-managed`, and `gpu-mapped` execution.

## 2.0.0 - 2026-07-14

- Added canonical Arrow IPC data, C++ validation, specialized CPU execution,
  and Arrow Acero execution.

## 1.0.0 - 2026-07-14

- Preserved the minimal fixed-Q5 CPU/CUDA course prototype and exact-result fix.

# Contributing

This is a course research prototype. Keep changes small, evidence-backed, and
compatible with the fixed Q5 result contract.

1. Do not commit TPC-H tools, generated `.tbl` files, large Arrow datasets,
   build directories, credentials, or profiler binaries.
2. Add or update a focused test before changing query semantics or benchmark
   records.
3. Run `bash scripts/ci_cpu.sh` for CPU/Arrow changes.
4. Run the commands in `docs/GPU_SERVER_RUNBOOK.md` for CUDA/cuDF changes.
5. If a paper result changes, create a new evidence bundle, audit it, update
   `docs/research/CLAIM_LEDGER.md`, and regenerate `docs/paper/generated`.
6. Preserve negative results and failed runs. Do not relabel unsupported
   resident measurements as cold-process results.

Third-party code and template files are outside the top-level Apache-2.0 grant;
see `NOTICE` before modifying or redistributing them.

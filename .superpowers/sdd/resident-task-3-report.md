# Resident Task 3 Report

Status: DONE

Implemented `HybridQ5Session` for resident CPU-GPU Arrow Q5 execution.
`Make` validates the fixed split, creates the two lineitem slices once, then
constructs one CPU session and one copy-mode CUDA session. It records the
combined phase timings and resident-byte counters. `Execute` starts the GPU
request asynchronously, runs the CPU scan on the caller, waits for both
results before returning an error, and merges successful results with the
existing checked merge routine. The one-shot `execute_q5_hybrid` path remains
unchanged.

TDD evidence:

- Added `test_q5_hybrid_session` first for ratios `0.25`, `0.50`, and `0.75`.
- The initial build failed because `memq5::HybridQ5Session` was missing.
- After correcting the test include for the existing setup type, the RED
  build failed solely on the missing `HybridQ5Session` API.
- The completed test executes each session twice and checks the exact result
  hash, total and stable partition counters, nonnegative overlap, resident
  setup counters, and zero per-request copy-mode input H2D bytes.

Verification:

- `cmake --build build-arrow-cuda-v3 -j2`: passed.
- Sandbox CUDA discovery returned code 77 despite visible host GPUs; this was
  treated as unavailable, not as a test pass.
- Real GPU: `CUDA_VISIBLE_DEVICES=0 ctest --test-dir build-arrow-cuda-v3 -R hybrid --output-on-failure`: 4/4 passed, no skips.
- Real GPU: `CUDA_VISIBLE_DEVICES=0 ctest --test-dir build-arrow-cuda-v3 --output-on-failure`: 25/25 passed, no skips.
- Real GPU: `CUDA_VISIBLE_DEVICES=0 compute-sanitizer --tool memcheck --error-exitcode=99 build-arrow-cuda-v3/tests/test_q5_hybrid_session`: `ERROR SUMMARY: 0 errors`.

This report is committed with the owned Task 3 source and test paths under
`feat: add resident CPU-GPU hybrid session`.

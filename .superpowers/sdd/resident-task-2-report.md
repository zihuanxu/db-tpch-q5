# Resident Task 2 Report: Persistent CUDA Sessions

Status: DONE

## Scope

Implemented `ArrowCudaQ5Session` for the copy, managed, and mapped Arrow CUDA
Q5 paths. The PIMPL retains prepared input, mode-specific input/output buffers,
host output scratch, setup metrics, and safe RAII ownership for all allocations.
Existing cold wrappers now make one session, execute once, and fold setup work
back into the returned cold result.

Files owned by this task:

- `src/cuda/q5_arrow_cuda.hpp`
- `src/cuda/q5_arrow_cuda.cu`
- `tests/test_q5_cuda_session.cpp`
- `tests/CMakeLists.txt`
- `.superpowers/sdd/resident-task-2-report.md`

`CMakeLists.txt` was not changed because the existing CUDA target already links
the implementation and the new test belongs in `tests/CMakeLists.txt`.

## RED / GREEN

RED:

1. Added `test_q5_cuda_session` before the production API.
2. Ran `cmake -S . -B build-arrow-cuda-v3` and
   `cmake --build build-arrow-cuda-v3 -j2`.
3. The test failed to compile as expected because `memq5::Q5SessionSetup`,
   `memq5::ArrowCudaMemoryMode`, and `memq5::ArrowCudaQ5Session` were absent.

GREEN:

1. Added the public mode enum and RAII/PIMPL session API.
2. `Make` performs plan/input preparation, allocation, mode-specific staging,
   and initial transfer/prefetch while recording `Q5SessionSetup` bytes and
   timings.
3. `Execute` resets outputs, conditionally re-prefetches managed outputs,
   launches the existing exact kernel, collects the small output, and builds a
   request-only result. Copy session requests report zero input H2D timing and
   bytes.
4. The new test repeats each mode twice, verifies the required hash
   `248d10b6ee352953`, verifies equal repeat hashes, and checks setup residency
   and mode timing/counter behavior.

## Verification

- `cmake --build build-arrow-cuda-v3 -j2`: exit 0.
- Sandbox GPU attempt:
  `CUDA_VISIBLE_DEVICES=0 build-arrow-cuda-v3/tests/test_q5_cuda_session`
  returned 77 with `no CUDA-capable device is detected`. This was treated as a
  sandbox visibility blocker, not as a passing GPU test.
- Real GPU 0 focused test (unsandboxed):
  `CUDA_VISIBLE_DEVICES=0 ctest --test-dir build-arrow-cuda-v3 --output-on-failure -R '^test_q5_cuda_session$'`
  passed 1/1.
- Real GPU 0 full CTest (unsandboxed):
  `CUDA_VISIBLE_DEVICES=0 ctest --test-dir build-arrow-cuda-v3 --output-on-failure`
  passed 23/23, including all CUDA, Arrow CUDA, hybrid, and CLI tests.
- Real GPU 0 sanitizer (unsandboxed):
  `CUDA_VISIBLE_DEVICES=0 compute-sanitizer --tool memcheck --error-exitcode=99 build-arrow-cuda-v3/tests/test_q5_cuda_session`
  exited 0 with `ERROR SUMMARY: 0 errors`.

## Self-Review

- Setup allocations are fully owned by `std::unique_ptr` PIMPL members; CUDA
  allocation failures and byte overflows remain translated to `CapacityError`.
- `MappedHostBuffer` continues to free a successfully allocated host mapping if
  device-pointer acquisition fails, and all buffer destructors are noexcept.
- Output state is clean from setup for the first request and explicitly reset
  after every completed request, preventing accumulation across repeated calls.
- Cold wrappers preserve the legacy h2d counters and fold initial setup timing
  into the one-shot result; existing cold CUDA oracle tests pass on GPU 0.
- No unrelated reports, plan correction, scripts, or concurrent Python-test
  changes were edited or staged.

## Concerns

- The normal sandbox exposes `nvidia-smi` but not a CUDA runtime device. Real
  GPU validation therefore required the approved unsandboxed commands above.
- Ninja emitted `premature end of file; recovering` for the shared build
  directory, but every build completed with exit 0 and the final full CTest and
  sanitizer runs passed. No source compiler warnings were emitted.

## Commit

`c6042fc` - `feat: keep Arrow CUDA Q5 inputs resident`

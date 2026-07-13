# MVP Exact Revenue Report

Date: 2026-07-13
Worktree: `/home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation`
Branch: `codex/tpch-q5-arrow-implementation`
Commit SHA: `d7e8821`

## Scope

Implemented the exact-revenue MVP in the owned files only:

- `src/common/fixed_point.hpp`
- `src/io/tpch_schema.hpp`
- `src/io/tpch_loader.cpp`
- `src/engine/q5_result.hpp`
- `src/engine/q5_result_io.cpp`
- `src/cpu/q5_cpu.cpp`
- `src/cuda/q5_cuda.cu`
- `tests/test_common.cpp`
- `tests/test_q5_cpu.cpp`
- `tests/test_q5_cuda.cpp`
- `tests/test_result_io.cpp`

## Summary

- Renamed lineitem discount storage from basis points to hundredths.
- Renamed result revenue storage from cents to scale-1e4 raw integers.
- Changed CPU revenue math to exact `extendedprice_cents * (100 - discount_hundredths)`.
- Added CPU overflow detection for the multiplication step.
- Made decimal parsing reject extra fractional digits.
- Changed CSV/JSON/raw hash handling to use exact `revenue_1e4` values.
- Kept display output at two decimal places with half-away-from-zero rounding after aggregation.
- Updated tiny fixture expectations to `JAPAN=1900000`, `INDIA=900000`.

## RED

Tests were edited first, before any production changes.

### RED configure command

```bash
conda run -p /tmp/memq5-arrow-cpu-task1 cmake -S /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation -B /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/build-red-cpu -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=/tmp/memq5-arrow-cpu-task1 -DMEMQ5_ENABLE_ARROW=ON -DMEMQ5_ENABLE_TESTS=ON -DMEMQ5_ENABLE_CUDA=OFF
```

Output:

```text
-- Arrow version: 23.0.1
-- Found the Arrow shared library: /tmp/memq5-arrow-cpu-task1/lib/libarrow.so.2300.1.0
-- Found the ArrowCompute shared library: /tmp/memq5-arrow-cpu-task1/lib/libarrow_compute.so.2300.1.0
-- Found the ArrowAcero shared library: /tmp/memq5-arrow-cpu-task1/lib/libarrow_acero.so.2300.1.0
-- Found OpenSSL: /tmp/memq5-arrow-cpu-task1/lib/libcrypto.so (found version "3.5.7")
-- Found nlohmann_json: /tmp/memq5-arrow-cpu-task1/share/cmake/nlohmann_json/nlohmann_jsonConfig.cmake (found version "3.12.0")
-- Configuring done
-- Generating done
-- Build files have been written to: /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/build-red-cpu
```

### RED build command

```bash
conda run -p /tmp/memq5-arrow-cpu-task1 cmake --build /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/build-red-cpu --target test_common test_q5_cpu test_result_io
```

Output:

```text
[1/11] Building CXX object tests/CMakeFiles/test_result_io.dir/test_result_io.cpp.o
FAILED: [code=1] tests/CMakeFiles/test_result_io.dir/test_result_io.cpp.o
/home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/tests/test_result_io.cpp:23:29: error: 'memq5::Q5ResultRow' has no member named 'revenue_1e4'; did you mean 'revenue_cents'?

[2/11] Building CXX object tests/CMakeFiles/test_common.dir/test_common.cpp.o
FAILED: [code=1] tests/CMakeFiles/test_common.dir/test_common.cpp.o
/home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/tests/test_common.cpp:50:30: error: 'compute_revenue_1e4' is not a member of 'memq5'; did you mean 'compute_revenue_cents'?

ninja: build stopped: subcommand failed.
```

Observed RED matched the intended missing exact-revenue API and result-field semantics.

## GREEN

### CPU build commands

```bash
conda run -p /tmp/memq5-arrow-cpu-task1 cmake --build /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/build-red-cpu --target test_common test_q5_cpu test_result_io
conda run -p /tmp/memq5-arrow-cpu-task1 cmake --build /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/build-red-cpu --target test_loader test_q5_plan test_arrow_link
```

Output:

```text
[1/8] Building CXX object tests/CMakeFiles/test_common.dir/test_common.cpp.o
[2/8] Building CXX object CMakeFiles/memq5_core.dir/src/engine/q5_result_io.cpp.o
[3/8] Building CXX object CMakeFiles/memq5_core.dir/src/cpu/q5_cpu.cpp.o
[4/8] Building CXX object CMakeFiles/memq5_core.dir/src/io/tpch_loader.cpp.o
[5/8] Linking CXX static library libmemq5_core.a
[6/8] Linking CXX executable tests/test_common
[7/8] Linking CXX executable tests/test_result_io
[8/8] Linking CXX executable tests/test_q5_cpu

[1/6] Building CXX object tests/CMakeFiles/test_loader.dir/test_loader.cpp.o
[2/6] Linking CXX executable tests/test_loader
[3/6] Building CXX object tests/CMakeFiles/test_q5_plan.dir/test_q5_plan.cpp.o
[4/6] Linking CXX executable tests/test_q5_plan
[5/6] Building CXX object tests/CMakeFiles/test_arrow_link.dir/test_arrow_link.cpp.o
[6/6] Linking CXX executable tests/test_arrow_link
```

### CPU CTest command

```bash
conda run -p /tmp/memq5-arrow-cpu-task1 ctest --test-dir /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/build-red-cpu --output-on-failure
```

Output:

```text
Test project /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/build-red-cpu
    Start 1: test_common
1/6 Test #1: test_common ......................   Passed    0.00 sec
    Start 2: test_loader
2/6 Test #2: test_loader ......................   Passed    0.00 sec
    Start 3: test_q5_plan
3/6 Test #3: test_q5_plan .....................   Passed    0.00 sec
    Start 4: test_q5_cpu
4/6 Test #4: test_q5_cpu ......................   Passed    0.00 sec
    Start 5: test_result_io
5/6 Test #5: test_result_io ...................   Passed    0.00 sec
    Start 6: test_arrow_link
6/6 Test #6: test_arrow_link ..................   Passed    0.02 sec

100% tests passed, 0 tests failed out of 6
```

### CUDA attempt

Device visibility check:

```bash
nvidia-smi
```

Observed: GPUs are visible on the machine.

CUDA configure attempt:

```bash
conda run -n memq5-cudf cmake -S /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation -B /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/build-red-gpu -G Ninja -DCMAKE_BUILD_TYPE=Release "-DCMAKE_PREFIX_PATH=/tmp/memq5-arrow-cpu-task1;/home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation/dist/arrow-cuda-prefix" -DMEMQ5_ENABLE_ARROW=ON -DMEMQ5_ENABLE_TESTS=ON -DMEMQ5_ENABLE_CUDA=ON
```

Output:

```text
-- Arrow version: 23.0.1
-- Found the Arrow shared library: /tmp/memq5-arrow-cpu-task1/lib/libarrow.so.2300.1.0
-- Found the ArrowCompute shared library: /tmp/memq5-arrow-cpu-task1/lib/libarrow_compute.so.2300.1.0
-- Found the ArrowAcero shared library: /tmp/memq5-arrow-cpu-task1/lib/libarrow_acero.so.2300.1.0
-- Found OpenSSL: /tmp/memq5-arrow-cpu-task1/lib/libcrypto.so (found version "3.5.7")
-- Found nlohmann_json: /tmp/memq5-arrow-cpu-task1/share/cmake/nlohmann_json/nlohmann_jsonConfig.cmake (found version "3.12.0")
CMake Error at CMakeLists.txt:51 (message):
  ArrowCUDA 23.0.1 must provide target ArrowCUDA::arrow_cuda_shared for
  MEMQ5_ENABLE_CUDA=ON
```

Result: CUDA tests could not be built or run from this checkout because the local Arrow CUDA prefix lacks the target required by the project CMake guard. This is a configuration blocker, not a device-access blocker.

## Verification

### Diff check

```bash
git -C /home/xuzihuan/db-tpch-q5/.worktrees/arrow-implementation diff --check
```

Output:

```text
(no output)
```

## Files changed

- `src/common/fixed_point.hpp`
- `src/io/tpch_schema.hpp`
- `src/io/tpch_loader.cpp`
- `src/engine/q5_result.hpp`
- `src/engine/q5_result_io.cpp`
- `src/cpu/q5_cpu.cpp`
- `src/cuda/q5_cuda.cu`
- `tests/test_common.cpp`
- `tests/test_q5_cpu.cpp`
- `tests/test_q5_cuda.cpp`
- `tests/test_result_io.cpp`
- `.superpowers/sdd/mvp-exact-revenue-report.md`

## Concerns

1. CUDA correctness could not be re-verified in this session because the available Arrow CUDA package metadata does not satisfy the repository's `ArrowCUDA::arrow_cuda_shared` requirement.
2. The CUDA implementation now matches the raw 1e4 arithmetic but still relies on unchecked `int64` multiplication on device, which is acceptable for the stated course SF1 scope and should not be treated as arbitrary-scale proof.

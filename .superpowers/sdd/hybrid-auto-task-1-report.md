# Hybrid-Auto Task 1 Report

## Scope

Implemented the pure C++ hybrid cost model only. The implementation has no
Arrow table, CUDA, or global-state dependency; `arrow::Result` is used solely
to return validation errors.

## RED / GREEN

1. RED: added analytical tests and the Arrow test target. The target failed to
   compile because `hybrid/hybrid_cost_model.hpp` did not exist.
2. GREEN: added the public calibration/prediction API and the closed-form
   evaluator. `ctest --test-dir build/arrow-cpu -R hybrid_cost_model
   --output-on-failure` passed.
3. RED: added a maximal-but-finite timing regression. The focused test failed
   because an overflowing GPU-only endpoint returned an error before the valid
   CPU-only endpoint could be selected.
4. GREEN: normalized the closed-form ratio arithmetic and ignored non-finite
   nonwinning endpoint candidates. The focused test passed, followed by the
   full Arrow CPU suite.

## Model

For CPU ratio `r`, the model evaluates `cpu_ms = r * cpu_ms` and, when the GPU
receives work, `gpu_ms = gpu_fixed_ms + (1-r) * gpu_kernel_ms`. CPU-only is an
explicit endpoint with zero GPU time. The balance candidate is:

```text
r = clamp((gpu_fixed_ms + gpu_kernel_ms) / (cpu_ms + gpu_kernel_ms), 0, 1)
```

The model evaluates GPU-only, CPU-only, and the clamped balance candidate,
selecting the smallest finite makespan. Strict comparison makes ties
deterministic and retains CPU-only as the tie preference. `rows` must be
positive to establish a valid calibration, although it cancels because every
timing is calibrated over the same row count.

## Validation

- `rows`, `cpu_ms`, and `gpu_kernel_ms` must be finite and positive.
- `gpu_fixed_ms` must be finite and non-negative; zero is valid for a
  no-fixed-overhead calibration.
- Tests cover equal speeds, faster CPU/GPU, dominant fixed GPU cost, clamping,
  hand calculation, deterministic `1e-12` epsilon comparisons, invalid
  zero/negative inputs, non-finite timing, and finite extreme inputs.
- Focused: `ctest --test-dir build/arrow-cpu -R hybrid_cost_model
  --output-on-failure` (1/1 passed).
- Full Arrow CPU: `ctest --test-dir build/arrow-cpu --output-on-failure`
  (16/16 passed).

## Self-Review

- The public interface exactly matches the Task 1 brief.
- The source is linked only into `memq5_arrow`; no CUDA CMake target changed.
- Endpoint evaluation avoids accidental GPU fixed cost for CPU-only execution.
- The ratio arithmetic scales finite inputs before addition, preventing an
  intermediate overflow from discarding a valid endpoint.
- Scoped diff check is clean.

## Concerns

This is intentionally a linear calibration model. It does not account for
filter selectivity, Arrow batch-boundary quantization, NUMA effects, or shared
memory bandwidth; Task 2 maps the predicted continuous ratio to batch
boundaries and records its calibration provenance. No benchmark claim follows
from this unit-tested model alone.

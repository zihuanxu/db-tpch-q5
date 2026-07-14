#pragma once

#include <cstdint>

#include <arrow/result.h>

namespace memq5 {

struct HybridCalibration {
  int64_t rows = 0;
  double cpu_ms = 0.0;
  double gpu_kernel_ms = 0.0;
  double gpu_fixed_ms = 0.0;
};

struct HybridPrediction {
  double predicted_cpu_ratio = 0.0;
  double predicted_cpu_ms = 0.0;
  double predicted_gpu_ms = 0.0;
};

// cpu_ms and gpu_kernel_ms must be finite and positive; gpu_fixed_ms must be
// finite and non-negative.
arrow::Result<HybridPrediction> predict_hybrid_ratio(
    const HybridCalibration& calibration);

}  // namespace memq5

#include "hybrid/hybrid_cost_model.hpp"

#include <algorithm>
#include <cmath>
#include <optional>

#include <arrow/status.h>

namespace memq5 {
namespace {

struct Candidate {
  HybridPrediction prediction;
  double makespan_ms = 0.0;
};

std::optional<Candidate> evaluate_ratio(const HybridCalibration& calibration,
                                        double cpu_ratio) {
  HybridPrediction prediction;
  prediction.predicted_cpu_ratio = cpu_ratio;
  prediction.predicted_cpu_ms = cpu_ratio * calibration.cpu_ms;
  prediction.predicted_gpu_ms =
      cpu_ratio == 1.0
          ? 0.0
          : calibration.gpu_fixed_ms +
                (1.0 - cpu_ratio) * calibration.gpu_kernel_ms;
  const double makespan_ms =
      std::max(prediction.predicted_cpu_ms, prediction.predicted_gpu_ms);
  if (!std::isfinite(prediction.predicted_cpu_ms) ||
      !std::isfinite(prediction.predicted_gpu_ms) ||
      !std::isfinite(makespan_ms)) {
    return std::nullopt;
  }
  return Candidate{prediction, makespan_ms};
}

}  // namespace

arrow::Result<HybridPrediction> predict_hybrid_ratio(
    const HybridCalibration& calibration) {
  if (calibration.rows <= 0) {
    return arrow::Status::Invalid("hybrid calibration rows must be positive");
  }
  if (!std::isfinite(calibration.cpu_ms) || calibration.cpu_ms <= 0.0) {
    return arrow::Status::Invalid(
        "hybrid calibration cpu_ms must be finite and positive");
  }
  if (!std::isfinite(calibration.gpu_kernel_ms) ||
      calibration.gpu_kernel_ms <= 0.0) {
    return arrow::Status::Invalid(
        "hybrid calibration gpu_kernel_ms must be finite and positive");
  }
  if (!std::isfinite(calibration.gpu_fixed_ms) ||
      calibration.gpu_fixed_ms < 0.0) {
    return arrow::Status::Invalid(
        "hybrid calibration gpu_fixed_ms must be finite and non-negative");
  }

  const double scale = std::max(
      {calibration.cpu_ms, calibration.gpu_kernel_ms, calibration.gpu_fixed_ms});
  const double candidate_ratio = std::clamp(
      (calibration.gpu_fixed_ms / scale + calibration.gpu_kernel_ms / scale) /
          (calibration.cpu_ms / scale + calibration.gpu_kernel_ms / scale),
      0.0, 1.0);

  Candidate best = *evaluate_ratio(calibration, 1.0);
  for (const double ratio : {0.0, candidate_ratio}) {
    const auto candidate = evaluate_ratio(calibration, ratio);
    if (candidate.has_value() && candidate->makespan_ms < best.makespan_ms) {
      best = *candidate;
    }
  }
  return best.prediction;
}

}  // namespace memq5

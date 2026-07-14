#include <cassert>
#include <cmath>
#include <cstdint>
#include <limits>

#include "hybrid/hybrid_cost_model.hpp"

namespace {

// The model uses only basic arithmetic; 1e-12 is stable across supported CPUs.
constexpr double kEpsilon = 1e-12;

void assert_near(double actual, double expected) {
  assert(std::abs(actual - expected) <= kEpsilon);
}

memq5::HybridPrediction predict(const memq5::HybridCalibration& calibration) {
  const auto result = memq5::predict_hybrid_ratio(calibration);
  assert(result.ok());
  return result.ValueOrDie();
}

void assert_invalid(const memq5::HybridCalibration& calibration) {
  assert(!memq5::predict_hybrid_ratio(calibration).ok());
}

}  // namespace

int main() {
  const memq5::HybridCalibration equal_speeds{100, 10.0, 10.0, 0.0};
  const auto equal = predict(equal_speeds);
  assert_near(equal.predicted_cpu_ratio, 0.5);
  assert_near(equal.predicted_cpu_ms, 5.0);
  assert_near(equal.predicted_gpu_ms, 5.0);

  const auto faster_cpu =
      predict(memq5::HybridCalibration{100, 10.0, 20.0, 0.0});
  assert_near(faster_cpu.predicted_cpu_ratio, 2.0 / 3.0);
  assert_near(faster_cpu.predicted_cpu_ms, 20.0 / 3.0);
  assert_near(faster_cpu.predicted_gpu_ms, 20.0 / 3.0);

  const auto faster_gpu =
      predict(memq5::HybridCalibration{100, 20.0, 10.0, 0.0});
  assert_near(faster_gpu.predicted_cpu_ratio, 1.0 / 3.0);
  assert_near(faster_gpu.predicted_cpu_ms, 20.0 / 3.0);
  assert_near(faster_gpu.predicted_gpu_ms, 20.0 / 3.0);

  const auto dominant_gpu_fixed =
      predict(memq5::HybridCalibration{100, 10.0, 1.0, 100.0});
  assert_near(dominant_gpu_fixed.predicted_cpu_ratio, 1.0);
  assert_near(dominant_gpu_fixed.predicted_cpu_ms, 10.0);
  assert_near(dominant_gpu_fixed.predicted_gpu_ms, 0.0);

  const auto hand_calculated =
      predict(memq5::HybridCalibration{100, 10.0, 20.0, 4.0});
  assert_near(hand_calculated.predicted_cpu_ratio, 0.8);
  assert_near(hand_calculated.predicted_cpu_ms, 8.0);
  assert_near(hand_calculated.predicted_gpu_ms, 8.0);

  const auto clamped =
      predict(memq5::HybridCalibration{100, 1.0, 1.0, 10.0});
  assert_near(clamped.predicted_cpu_ratio, 1.0);

  const double largest = std::numeric_limits<double>::max();
  const auto finite_endpoint =
      predict(memq5::HybridCalibration{1, largest, largest, largest});
  assert_near(finite_endpoint.predicted_cpu_ratio, 1.0);
  assert_near(finite_endpoint.predicted_cpu_ms, largest);
  assert_near(finite_endpoint.predicted_gpu_ms, 0.0);

  const auto first = predict(memq5::HybridCalibration{100, 7.0, 11.0, 3.0});
  const auto second = predict(memq5::HybridCalibration{100, 7.0, 11.0, 3.0});
  assert_near(first.predicted_cpu_ratio, second.predicted_cpu_ratio);
  assert_near(first.predicted_cpu_ms, second.predicted_cpu_ms);
  assert_near(first.predicted_gpu_ms, second.predicted_gpu_ms);

  assert_invalid(memq5::HybridCalibration{0, 1.0, 1.0, 0.0});
  assert_invalid(memq5::HybridCalibration{-1, 1.0, 1.0, 0.0});
  assert_invalid(memq5::HybridCalibration{1, 0.0, 1.0, 0.0});
  assert_invalid(memq5::HybridCalibration{1, -1.0, 1.0, 0.0});
  assert_invalid(memq5::HybridCalibration{1, 1.0, 0.0, 0.0});
  assert_invalid(memq5::HybridCalibration{1, 1.0, -1.0, 0.0});
  assert_invalid(memq5::HybridCalibration{1, 1.0, 1.0, -1.0});
  assert_invalid(memq5::HybridCalibration{
      1, std::numeric_limits<double>::infinity(), 1.0, 0.0});
  assert_invalid(memq5::HybridCalibration{
      1, 1.0, std::numeric_limits<double>::quiet_NaN(), 0.0});
  assert_invalid(memq5::HybridCalibration{
      1, 1.0, 1.0, std::numeric_limits<double>::infinity()});

  return 0;
}

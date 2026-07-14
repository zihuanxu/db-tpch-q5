#pragma once

#include <cstdint>
#include <memory>
#include <string>

#include <arrow/result.h>

#include "engine/q5_params.hpp"
#include "engine/q5_result.hpp"
#include "io/arrow_q5_loader.hpp"
#include "session/q5_cpu_session.hpp"

namespace memq5 {

class ArrowCudaQ5Session;

enum class HybridSelection { kFixed, kAuto };

struct HybridAutoTuning {
  bool enabled = false;
  std::string model_version;
  int64_t calibration_rows = 0;
  int cpu_calibration_requests = 0;
  int gpu_calibration_requests = 0;
  double cpu_calibration_ms = 0.0;
  double gpu_calibration_ms = 0.0;
  double gpu_kernel_calibration_ms = 0.0;
  double cpu_rows_per_ms = 0.0;
  double gpu_kernel_rows_per_ms = 0.0;
  double gpu_fixed_ms = 0.0;
  double predicted_cpu_ratio = 0.0;
  int64_t selected_batch_boundary_rows = 0;
  double realized_cpu_ratio = 0.0;
  // Calibration and model selection time, included in session setup total.
  double tune_ms = 0.0;
};

struct HybridOptions {
  HybridSelection selection = HybridSelection::kFixed;
  double cpu_ratio = 0.5;
  int cpu_threads = 1;
};

class HybridQ5Session {
 public:
  static arrow::Result<std::unique_ptr<HybridQ5Session>> Make(
      const ArrowQ5Dataset& dataset, const Q5Params& params,
      const HybridOptions& options);
  ~HybridQ5Session();

  arrow::Result<Q5Result> Execute();
  const Q5SessionSetup& setup() const;
  double cpu_ratio() const;
  const HybridAutoTuning& auto_tuning() const;

 private:
  HybridQ5Session(std::unique_ptr<ArrowCpuQ5Session> cpu_session,
                  std::unique_ptr<ArrowCudaQ5Session> gpu_session,
                  Q5SessionSetup setup, double cpu_ratio,
                  HybridAutoTuning auto_tuning);

  std::unique_ptr<ArrowCpuQ5Session> cpu_session_;
  std::unique_ptr<ArrowCudaQ5Session> gpu_session_;
  const Q5SessionSetup setup_;
  const double cpu_ratio_;
  const HybridAutoTuning auto_tuning_;
};

arrow::Status validate_hybrid_calibration_results(const Q5Result& cpu,
                                                   const Q5Result& gpu,
                                                   int64_t calibration_rows);

arrow::Result<Q5Result> execute_q5_hybrid(
    const ArrowQ5Dataset& dataset, const Q5Params& params,
    const HybridOptions& options);

}  // namespace memq5

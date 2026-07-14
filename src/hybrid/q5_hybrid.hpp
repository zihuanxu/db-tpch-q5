#pragma once

#include <memory>

#include <arrow/result.h>

#include "engine/q5_params.hpp"
#include "engine/q5_result.hpp"
#include "io/arrow_q5_loader.hpp"
#include "session/q5_cpu_session.hpp"

namespace memq5 {

class ArrowCudaQ5Session;

struct HybridOptions {
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

 private:
  HybridQ5Session(std::unique_ptr<ArrowCpuQ5Session> cpu_session,
                  std::unique_ptr<ArrowCudaQ5Session> gpu_session,
                  Q5SessionSetup setup, double cpu_ratio);

  std::unique_ptr<ArrowCpuQ5Session> cpu_session_;
  std::unique_ptr<ArrowCudaQ5Session> gpu_session_;
  const Q5SessionSetup setup_;
  const double cpu_ratio_;
};

arrow::Result<Q5Result> execute_q5_hybrid(
    const ArrowQ5Dataset& dataset, const Q5Params& params,
    const HybridOptions& options);

}  // namespace memq5

#pragma once

#include <cstdint>
#include <memory>

#include <arrow/result.h>
#include <arrow/table.h>

#include "engine/arrow_q5_plan.hpp"
#include "engine/q5_params.hpp"
#include "engine/q5_result.hpp"
#include "io/arrow_q5_loader.hpp"

namespace memq5 {

struct Q5SessionSetup {
  double plan_build_ms = 0.0;
  double host_staging_ms = 0.0;
  double allocation_ms = 0.0;
  double initial_h2d_ms = 0.0;
  double total_ms = 0.0;
  int64_t resident_host_bytes = 0;
  int64_t resident_gpu_bytes = 0;
  int64_t resident_pinned_bytes = 0;
};

class ArrowCpuQ5Session {
 public:
  static arrow::Result<std::unique_ptr<ArrowCpuQ5Session>> Make(
      const ArrowQ5Dataset& dataset, const Q5Params& params);

  arrow::Result<Q5Result> Execute() const;
  const Q5SessionSetup& setup() const;

 private:
  ArrowCpuQ5Session(std::shared_ptr<arrow::Table> lineitem, ArrowQ5Plan plan,
                    int threads, Q5SessionSetup setup);

  std::shared_ptr<arrow::Table> lineitem_;
  const ArrowQ5Plan plan_;
  const int threads_;
  const Q5SessionSetup setup_;
};

}  // namespace memq5

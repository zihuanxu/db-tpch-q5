#pragma once

#include <arrow/result.h>

#include "engine/q5_params.hpp"
#include "engine/q5_result.hpp"
#include "io/arrow_q5_loader.hpp"

namespace memq5 {

struct HybridOptions {
  double cpu_ratio = 0.5;
  int cpu_threads = 1;
};

arrow::Result<Q5Result> execute_q5_hybrid(
    const ArrowQ5Dataset& dataset, const Q5Params& params,
    const HybridOptions& options);

}  // namespace memq5

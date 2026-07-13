#pragma once

#include <arrow/result.h>

#include "engine/q5_params.hpp"
#include "engine/q5_result.hpp"
#include "io/arrow_q5_loader.hpp"

namespace memq5 {

arrow::Result<Q5Result> execute_q5_arrow_gpu_copy(const ArrowQ5Dataset& dataset,
                                                  const Q5Params& params);
arrow::Result<Q5Result>
execute_q5_arrow_gpu_managed(const ArrowQ5Dataset& dataset,
                             const Q5Params& params);
arrow::Result<Q5Result>
execute_q5_arrow_gpu_mapped(const ArrowQ5Dataset& dataset,
                            const Q5Params& params);

}  // namespace memq5

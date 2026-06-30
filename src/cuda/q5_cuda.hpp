#pragma once

#include "engine/q5_params.hpp"
#include "engine/q5_result.hpp"
#include "io/tpch_schema.hpp"

namespace memq5 {

Q5Result execute_q5_gpu_copy(const TpchDatabase& db, const Q5Params& params);
Q5Result execute_q5_gpu_managed(const TpchDatabase& db, const Q5Params& params);
Q5Result execute_q5_gpu_mapped(const TpchDatabase& db, const Q5Params& params);

}  // namespace memq5

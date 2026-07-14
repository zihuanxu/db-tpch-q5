#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include <arrow/result.h>

#include "engine/q5_params.hpp"
#include "io/arrow_q5_loader.hpp"

namespace memq5 {

struct ArrowQ5Plan {
  std::vector<int32_t> supplier_nation_by_key;
  std::vector<int32_t> order_nation_by_key;
  std::vector<std::string> nation_name_by_key;
  int32_t max_nation_key = -1;
  double build_ms = 0.0;
};

arrow::Result<ArrowQ5Plan> build_arrow_q5_plan(const ArrowQ5Dataset& dataset,
                                               const Q5Params& params);

}  // namespace memq5

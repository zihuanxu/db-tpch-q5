#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "engine/q5_params.hpp"
#include "io/tpch_schema.hpp"

namespace memq5 {

struct Q5PreparedPlan {
  std::vector<int32_t> supplier_nation_by_key;
  std::vector<int32_t> order_nation_by_key;
  std::vector<std::string> nation_name_by_key;
  int32_t max_nation_key = -1;
  double build_ms = 0.0;
};

Q5PreparedPlan build_q5_plan_cpu(const TpchDatabase& db, const Q5Params& params);

bool valid_key(const std::vector<int32_t>& values, int32_t key);

}  // namespace memq5

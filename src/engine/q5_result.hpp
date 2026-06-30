#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace memq5 {

struct Q5Timing {
  double build_ms = 0.0;
  double h2d_ms = 0.0;
  double kernel_ms = 0.0;
  double d2h_ms = 0.0;
  double scan_ms = 0.0;
  double total_ms = 0.0;
};

struct Q5ResultRow {
  std::string nation_name;
  int64_t revenue_cents = 0;
};

struct Q5Result {
  std::vector<Q5ResultRow> rows;
  Q5Timing timing;
};

}  // namespace memq5

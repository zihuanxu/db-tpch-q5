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
  double cpu_ms = 0.0;
  double gpu_ms = 0.0;
  double overlap_ms = 0.0;
};

struct Q5ResultRow {
  std::string nation_name;
  int64_t revenue_1e4 = 0;
};

struct Q5Counters {
  int64_t input_lineitem_rows = 0;
  int64_t matched_lineitem_rows = 0;
  int64_t cpu_input_rows = 0;
  int64_t gpu_input_rows = 0;
  int64_t h2d_bytes = 0;
  int64_t d2h_bytes = 0;
  int64_t mapped_remote_read_bytes = 0;
};

struct Q5Result {
  std::vector<Q5ResultRow> rows;
  Q5Timing timing;
  Q5Counters counters;
};

}  // namespace memq5

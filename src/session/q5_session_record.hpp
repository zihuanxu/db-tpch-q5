#pragma once

#include <cstdint>
#include <optional>
#include <string>

#include "engine/q5_result.hpp"
#include "session/q5_cpu_session.hpp"

namespace memq5 {

struct Q5SessionSetupRecord {
  std::string engine;
  std::string dataset;
  std::string region;
  std::string date;
  int threads = 1;
  int warmup = 3;
  int repeat = 10;
  double dataset_load_ms = 0.0;
  Q5SessionSetup setup;
  double tune_ms = 0.0;
  double selected_cpu_ratio = 0.0;
  double predicted_cpu_ratio = 0.0;
};

struct Q5SessionRequestRecord {
  std::string status = "ok";
  std::string error_class;
  std::string error_message;
  int64_t request_index = 0;
  bool is_warmup = false;
  double selected_cpu_ratio = 0.0;
  std::optional<Q5Result> result;
};

}  // namespace memq5

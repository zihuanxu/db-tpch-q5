#pragma once

#include <chrono>

namespace memq5 {

class Stopwatch {
 public:
  Stopwatch() { reset(); }

  void reset() { start_ = Clock::now(); }

  double elapsed_ms() const {
    const auto elapsed = Clock::now() - start_;
    return std::chrono::duration<double, std::milli>(elapsed).count();
  }

 private:
  using Clock = std::chrono::steady_clock;
  Clock::time_point start_;
};

}  // namespace memq5

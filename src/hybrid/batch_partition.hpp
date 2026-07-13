#pragma once

#include <cstdint>
#include <vector>

#include <arrow/result.h>

namespace memq5 {

struct BatchSlice {
  int32_t batch_index = 0;
  int64_t offset = 0;
  int64_t length = 0;
};

inline bool operator==(const BatchSlice& left, const BatchSlice& right) {
  return left.batch_index == right.batch_index && left.offset == right.offset &&
         left.length == right.length;
}

struct HybridPartition {
  std::vector<BatchSlice> cpu_slices;
  std::vector<BatchSlice> gpu_slices;
  int64_t cpu_rows = 0;
  int64_t gpu_rows = 0;
};

arrow::Result<HybridPartition> partition_batch_lengths(
    const std::vector<int64_t>& batch_lengths, double cpu_ratio);

}  // namespace memq5

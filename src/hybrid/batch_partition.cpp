#include "hybrid/batch_partition.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>

namespace memq5 {

arrow::Result<HybridPartition> partition_batch_lengths(
    const std::vector<int64_t>& batch_lengths, double cpu_ratio) {
  if (!std::isfinite(cpu_ratio) || cpu_ratio < 0.0 || cpu_ratio > 1.0) {
    return arrow::Status::Invalid("cpu_ratio must be between 0 and 1");
  }
  if (batch_lengths.size() >
      static_cast<std::size_t>(std::numeric_limits<int32_t>::max())) {
    return arrow::Status::CapacityError("batch count exceeds int32 range");
  }

  int64_t total_rows = 0;
  for (const int64_t length : batch_lengths) {
    if (length < 0) {
      return arrow::Status::Invalid("batch length must be non-negative");
    }
    if (total_rows > std::numeric_limits<int64_t>::max() - length) {
      return arrow::Status::CapacityError("batch row count overflow");
    }
    total_rows += length;
  }
  const int64_t cpu_target =
      static_cast<int64_t>(std::llround(static_cast<long double>(total_rows) *
                                        cpu_ratio));

  HybridPartition partition;
  int64_t cpu_remaining = cpu_target;
  for (std::size_t index = 0; index < batch_lengths.size(); ++index) {
    const int64_t length = batch_lengths[index];
    if (length == 0) {
      continue;
    }
    const int32_t batch_index = static_cast<int32_t>(index);
    const int64_t cpu_length = std::min(cpu_remaining, length);
    if (cpu_length > 0) {
      partition.cpu_slices.push_back(BatchSlice{batch_index, 0, cpu_length});
      partition.cpu_rows += cpu_length;
      cpu_remaining -= cpu_length;
    }
    const int64_t gpu_length = length - cpu_length;
    if (gpu_length > 0) {
      partition.gpu_slices.push_back(
          BatchSlice{batch_index, cpu_length, gpu_length});
      partition.gpu_rows += gpu_length;
    }
  }
  if (partition.cpu_rows != cpu_target ||
      partition.cpu_rows + partition.gpu_rows != total_rows) {
    return arrow::Status::Invalid("partition coverage invariant failed");
  }
  return partition;
}

arrow::Result<HybridPartition> partition_batch_lengths_at_boundary(
    const std::vector<int64_t>& batch_lengths, double cpu_ratio) {
  if (!std::isfinite(cpu_ratio) || cpu_ratio < 0.0 || cpu_ratio > 1.0) {
    return arrow::Status::Invalid("cpu_ratio must be between 0 and 1");
  }
  if (batch_lengths.size() >
      static_cast<std::size_t>(std::numeric_limits<int32_t>::max())) {
    return arrow::Status::CapacityError("batch count exceeds int32 range");
  }

  int64_t total_rows = 0;
  for (const int64_t length : batch_lengths) {
    if (length < 0) {
      return arrow::Status::Invalid("batch length must be non-negative");
    }
    if (total_rows > std::numeric_limits<int64_t>::max() - length) {
      return arrow::Status::CapacityError("batch row count overflow");
    }
    total_rows += length;
  }

  const long double target_rows =
      static_cast<long double>(total_rows) * cpu_ratio;
  int64_t boundary_rows = 0;
  int64_t cumulative_rows = 0;
  long double best_distance = std::fabs(target_rows);
  for (const int64_t length : batch_lengths) {
    cumulative_rows += length;
    const long double distance =
        std::fabs(static_cast<long double>(cumulative_rows) - target_rows);
    if (distance < best_distance) {
      boundary_rows = cumulative_rows;
      best_distance = distance;
    }
  }

  HybridPartition partition;
  int64_t assigned_cpu_rows = 0;
  for (std::size_t index = 0; index < batch_lengths.size(); ++index) {
    const int64_t length = batch_lengths[index];
    if (length == 0) {
      continue;
    }
    const BatchSlice slice{static_cast<int32_t>(index), 0, length};
    if (assigned_cpu_rows < boundary_rows) {
      partition.cpu_slices.push_back(slice);
      partition.cpu_rows += length;
      assigned_cpu_rows += length;
    } else {
      partition.gpu_slices.push_back(slice);
      partition.gpu_rows += length;
    }
  }
  if (partition.cpu_rows != boundary_rows ||
      partition.cpu_rows + partition.gpu_rows != total_rows) {
    return arrow::Status::Invalid("partition coverage invariant failed");
  }
  return partition;
}

}  // namespace memq5

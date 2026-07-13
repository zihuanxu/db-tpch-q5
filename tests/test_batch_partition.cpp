#include <cassert>
#include <cmath>
#include <cstdint>
#include <set>
#include <utility>
#include <vector>

#include "hybrid/batch_partition.hpp"

namespace {

void assert_partition(double ratio) {
  const std::vector<int64_t> lengths{3, 5, 2};
  const auto first =
      memq5::partition_batch_lengths(lengths, ratio).ValueOrDie();
  const auto second =
      memq5::partition_batch_lengths(lengths, ratio).ValueOrDie();
  assert(first.cpu_slices == second.cpu_slices);
  assert(first.gpu_slices == second.gpu_slices);
  assert(first.cpu_rows + first.gpu_rows == 10);
  assert(std::abs(first.cpu_rows - std::llround(10.0 * ratio)) <= 1);

  std::set<std::pair<int32_t, int64_t>> covered;
  for (const auto* slices : {&first.cpu_slices, &first.gpu_slices}) {
    for (const auto& slice : *slices) {
      assert(slice.length > 0);
      assert(slice.batch_index >= 0);
      assert(static_cast<std::size_t>(slice.batch_index) < lengths.size());
      assert(slice.offset >= 0);
      assert(slice.offset + slice.length <=
             lengths[static_cast<std::size_t>(slice.batch_index)]);
      for (int64_t row = slice.offset; row < slice.offset + slice.length;
           ++row) {
        assert(covered.insert({slice.batch_index, row}).second);
      }
    }
  }
  assert(covered.size() == 10);
}

}  // namespace

int main() {
  for (double ratio : {0.0, 0.25, 0.5, 0.75, 1.0}) {
    assert_partition(ratio);
  }
  assert(!memq5::partition_batch_lengths({3, 5, 2}, -0.01).ok());
  assert(!memq5::partition_batch_lengths({3, 5, 2}, 1.01).ok());
  assert(!memq5::partition_batch_lengths({3, -1, 2}, 0.5).ok());
  return 0;
}

#include "session/q5_cpu_session.hpp"

#include <arrow/api.h>

#include <cassert>
#include <cstdint>
#include <memory>
#include <string>
#include <unordered_set>

#include "engine/arrow_q5_plan.hpp"
#include "engine/q5_params.hpp"
#include "engine/q5_result_io.hpp"
#include "io/arrow_q5_loader.hpp"

namespace {

arrow::Result<memq5::ArrowQ5Dataset> LoadTinyArrowDataset() {
  return memq5::load_arrow_q5_dataset(MEMQ5_ARROW_FIXTURE_DIR);
}

memq5::Q5Params Asia1994Params() {
  memq5::Q5Params params;
  params.region_name = "ASIA";
  memq5::set_q5_date(&params, "1994-01-01");
  return params;
}

std::shared_ptr<arrow::Buffer> RootBuffer(
    std::shared_ptr<arrow::Buffer> buffer) {
  while (buffer != nullptr && buffer->parent() != nullptr) {
    buffer = buffer->parent();
  }
  return buffer;
}

int64_t LogicalArrayBufferBytes(
    const std::shared_ptr<arrow::ArrayData>& data,
    std::unordered_set<const arrow::Buffer*>* seen) {
  int64_t bytes = 0;
  for (const auto& buffer : data->buffers) {
    const auto root = RootBuffer(buffer);
    if (root != nullptr && seen->insert(root.get()).second) {
      assert(root->size() >= 0);
      bytes += root->size();
    }
  }
  for (const auto& child : data->child_data) {
    bytes += LogicalArrayBufferBytes(child, seen);
  }
  if (data->dictionary != nullptr) {
    bytes += LogicalArrayBufferBytes(data->dictionary, seen);
  }
  return bytes;
}

int64_t LogicalTableBufferBytes(const arrow::Table& table) {
  int64_t bytes = 0;
  std::unordered_set<const arrow::Buffer*> seen;
  for (const auto& column : table.columns()) {
    for (const auto& chunk : column->chunks()) {
      bytes += LogicalArrayBufferBytes(chunk->data(), &seen);
    }
  }
  return bytes;
}

int64_t LogicalPlanPayloadBytes(const memq5::ArrowQ5Plan& plan) {
  int64_t bytes = static_cast<int64_t>(
      (plan.supplier_nation_by_key.size() +
       plan.order_nation_by_key.size()) *
      sizeof(int32_t));
  for (const auto& name : plan.nation_name_by_key) {
    bytes += static_cast<int64_t>(name.size());
  }
  return bytes;
}

int64_t ExpectedResidentHostBytes(const memq5::ArrowQ5Dataset& dataset,
                                  const memq5::Q5Params& params) {
  const auto plan = memq5::build_arrow_q5_plan(dataset, params).ValueOrDie();
  // CPU resident bytes are logical retained payload: root Arrow buffers once
  // plus plan vector/string contents, excluding metadata and allocator padding.
  return LogicalTableBufferBytes(*dataset.lineitem) +
         LogicalPlanPayloadBytes(plan);
}

}  // namespace

int main() {
  const auto dataset = LoadTinyArrowDataset().ValueOrDie();
  memq5::Q5Params params = Asia1994Params();
  params.threads = 2;
  auto session = memq5::ArrowCpuQ5Session::Make(dataset, params).ValueOrDie();
  const int64_t expected_resident_host_bytes =
      ExpectedResidentHostBytes(dataset, params);
  assert(expected_resident_host_bytes > 0);
  assert(session->setup().resident_host_bytes ==
         expected_resident_host_bytes);

  memq5::ArrowQ5Dataset sliced_dataset = dataset;
  sliced_dataset.lineitem = dataset.lineitem->Slice(1, 1);
  sliced_dataset.tables["lineitem"] = sliced_dataset.lineitem;
  auto sliced_session =
      memq5::ArrowCpuQ5Session::Make(sliced_dataset, params).ValueOrDie();
  assert(sliced_session->setup().resident_host_bytes ==
         ExpectedResidentHostBytes(sliced_dataset, params));

  const auto first = session->Execute().ValueOrDie();
  const auto second = session->Execute().ValueOrDie();
  assert(memq5::result_hash_hex(first) == "248d10b6ee352953");
  assert(memq5::result_hash_hex(first) == memq5::result_hash_hex(second));
  assert(first.timing.build_ms == 0.0);
  assert(second.timing.build_ms == 0.0);
  assert(session->setup().plan_build_ms >= 0.0);

  params.threads = 0;
  const auto invalid = memq5::ArrowCpuQ5Session::Make(dataset, params);
  assert(!invalid.ok());
  assert(invalid.status().IsInvalid());
  return 0;
}

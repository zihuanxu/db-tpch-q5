#include <cuda_runtime.h>

#include <arrow/api.h>

#include <cassert>
#include <iostream>
#include <limits>
#include <map>

#include "cpu/q5_arrow_cpu.hpp"
#include "cuda/q5_arrow_cuda.hpp"
#include "engine/q5_params.hpp"
#include "engine/q5_result_io.hpp"
#include "io/arrow_q5_loader.hpp"

#ifdef NDEBUG
#error "CUDA tests require assertions"
#endif

namespace {

void assert_same_result(const memq5::Q5Result& expected,
                        const memq5::Q5Result& actual) {
  assert(memq5::result_hash_hex(expected) == memq5::result_hash_hex(actual));
  assert(expected.rows.size() == actual.rows.size());
  assert(expected.counters.input_lineitem_rows ==
         actual.counters.input_lineitem_rows);
  assert(expected.counters.matched_lineitem_rows ==
         actual.counters.matched_lineitem_rows);
}

std::shared_ptr<arrow::Table>
replace_int32_value(const std::shared_ptr<arrow::Table>& table,
                    int column_index, int64_t target_row, int32_t replacement) {
  std::vector<std::shared_ptr<arrow::Array>> chunks;
  int64_t global_row = 0;
  for (const auto& chunk : table->column(column_index)->chunks()) {
    const auto values = std::dynamic_pointer_cast<arrow::Int32Array>(chunk);
    assert(values != nullptr);
    arrow::Int32Builder builder;
    for (int64_t row = 0; row < values->length(); ++row, ++global_row) {
      const auto status = builder.Append(
          global_row == target_row ? replacement : values->Value(row));
      assert(status.ok());
    }
    chunks.push_back(builder.Finish().ValueOrDie());
  }
  auto changed = table->SetColumn(
      column_index, table->schema()->field(column_index),
      std::make_shared<arrow::ChunkedArray>(std::move(chunks), arrow::int32()));
  assert(changed.ok());
  return changed.MoveValueUnsafe();
}

std::shared_ptr<arrow::Table>
replace_prices(const std::shared_ptr<arrow::Table>& lineitem,
               const std::map<int64_t, arrow::Decimal128>& replacements) {
  const auto type = arrow::decimal128(15, 2);
  std::vector<std::shared_ptr<arrow::Array>> chunks;
  int64_t global_row = 0;
  for (const auto& chunk : lineitem->column(2)->chunks()) {
    const auto values =
        std::dynamic_pointer_cast<arrow::Decimal128Array>(chunk);
    assert(values != nullptr);
    arrow::Decimal128Builder builder(type);
    for (int64_t row = 0; row < values->length(); ++row, ++global_row) {
      const auto replacement = replacements.find(global_row);
      const arrow::Decimal128 value =
          replacement == replacements.end()
              ? arrow::Decimal128(values->GetValue(row))
              : replacement->second;
      const auto status = builder.Append(value);
      assert(status.ok());
    }
    chunks.push_back(builder.Finish().ValueOrDie());
  }
  auto changed = lineitem->SetColumn(
      2, lineitem->schema()->field(2),
      std::make_shared<arrow::ChunkedArray>(std::move(chunks), type));
  assert(changed.ok());
  return changed.MoveValueUnsafe();
}

}  // namespace

int main() {
  int device_count = 0;
  const cudaError_t status = cudaGetDeviceCount(&device_count);
  if (status != cudaSuccess || device_count == 0) {
    std::cout << "Skipping Arrow CUDA runtime test: "
              << cudaGetErrorString(status) << '\n';
    return 0;
  }

  const auto loaded =
      memq5::load_arrow_q5_dataset(MEMQ5_ARROW_FIXTURE_DIR).ValueOrDie();
  memq5::Q5Params params;
  params.region_name = "ASIA";
  memq5::set_q5_date(&params, "1994-01-01");
  params.threads = 2;

  const auto cpu = memq5::execute_q5_arrow_cpu(loaded, params).ValueOrDie();
  const auto copy =
      memq5::execute_q5_arrow_gpu_copy(loaded, params).ValueOrDie();
  const auto managed =
      memq5::execute_q5_arrow_gpu_managed(loaded, params).ValueOrDie();
  const auto mapped =
      memq5::execute_q5_arrow_gpu_mapped(loaded, params).ValueOrDie();

  for (const memq5::Q5Result* result : {&copy, &managed, &mapped}) {
    assert_same_result(cpu, *result);
    assert(result->counters.input_lineitem_rows == 6);
    assert(result->counters.matched_lineitem_rows == 2);
    assert(result->counters.cpu_input_rows == 0);
    assert(result->counters.gpu_input_rows == 6);
    assert(result->counters.d2h_bytes > 0);
  }
  assert(copy.counters.h2d_bytes > 0);
  assert(copy.counters.mapped_remote_read_bytes == 0);
  assert(managed.counters.h2d_bytes > 0);
  assert(managed.counters.mapped_remote_read_bytes == 0);
  assert(mapped.counters.h2d_bytes == 0);
  assert(mapped.counters.mapped_remote_read_bytes > 0);

  memq5::ArrowQ5Dataset empty = loaded;
  empty.lineitem = loaded.lineitem->Slice(0, 0);
  empty.tables["lineitem"] = empty.lineitem;
  const auto empty_cpu =
      memq5::execute_q5_arrow_cpu(empty, params).ValueOrDie();
  assert_same_result(
      empty_cpu, memq5::execute_q5_arrow_gpu_copy(empty, params).ValueOrDie());
  assert_same_result(
      empty_cpu,
      memq5::execute_q5_arrow_gpu_managed(empty, params).ValueOrDie());
  assert_same_result(
      empty_cpu,
      memq5::execute_q5_arrow_gpu_mapped(empty, params).ValueOrDie());

  const int64_t large_price = std::numeric_limits<int64_t>::max() / 100;
  memq5::ArrowQ5Dataset overflow = loaded;
  overflow.lineitem = replace_int32_value(loaded.lineitem, 1, 1, 1);
  overflow.lineitem =
      replace_prices(overflow.lineitem, {{0, arrow::Decimal128(large_price)},
                                         {1, arrow::Decimal128(large_price)}});
  const auto overflow_copy = memq5::execute_q5_arrow_gpu_copy(overflow, params);
  const auto overflow_managed =
      memq5::execute_q5_arrow_gpu_managed(overflow, params);
  const auto overflow_mapped =
      memq5::execute_q5_arrow_gpu_mapped(overflow, params);
  assert(!overflow_copy.ok() && overflow_copy.status().IsCapacityError());
  assert(!overflow_managed.ok() && overflow_managed.status().IsCapacityError());
  assert(!overflow_mapped.ok() && overflow_mapped.status().IsCapacityError());

  return 0;
}

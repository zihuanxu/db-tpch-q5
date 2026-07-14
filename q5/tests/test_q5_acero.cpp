#include "cpu/q5_acero.hpp"

#include <arrow/api.h>
#include <arrow/util/thread_pool.h>

#include <cassert>
#include <iostream>
#include <limits>
#include <map>
#include <string>

#include "cpu/q5_arrow_cpu.hpp"
#include "engine/q5_params.hpp"
#include "engine/q5_result_io.hpp"
#include "io/arrow_q5_loader.hpp"

namespace {

memq5::Q5Params make_params(const std::string& region, int threads) {
  memq5::Q5Params params;
  params.region_name = region;
  memq5::set_q5_date(&params, "1994-01-01");
  params.threads = threads;
  return params;
}

std::shared_ptr<arrow::Table> with_wrong_decimal_scale(
    const std::shared_ptr<arrow::Table>& lineitem) {
  const auto wrong_type = arrow::decimal128(15, 4);
  std::vector<std::shared_ptr<arrow::Array>> chunks;
  for (const auto& chunk : lineitem->column(2)->chunks()) {
    auto data = chunk->data()->Copy();
    data->type = wrong_type;
    chunks.push_back(arrow::MakeArray(std::move(data)));
  }
  const auto wrong_column =
      std::make_shared<arrow::ChunkedArray>(std::move(chunks), wrong_type);
  auto changed = lineitem->SetColumn(
      2, arrow::field("l_extendedprice", wrong_type, false), wrong_column);
  assert(changed.ok());
  return changed.MoveValueUnsafe();
}

std::shared_ptr<arrow::Table> replace_prices(
    const std::shared_ptr<arrow::Table>& lineitem,
    const std::map<int64_t, arrow::Decimal128>& replacements) {
  const auto type = arrow::decimal128(15, 2);
  std::vector<std::shared_ptr<arrow::Array>> chunks;
  int64_t global_row = 0;
  for (const auto& chunk : lineitem->column(2)->chunks()) {
    const auto values = std::dynamic_pointer_cast<arrow::Decimal128Array>(chunk);
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
    std::shared_ptr<arrow::Array> output;
    const auto finish = builder.Finish(&output);
    assert(finish.ok());
    chunks.push_back(std::move(output));
  }
  const auto column =
      std::make_shared<arrow::ChunkedArray>(std::move(chunks), type);
  auto changed = lineitem->SetColumn(2, lineitem->schema()->field(2), column);
  assert(changed.ok());
  return changed.MoveValueUnsafe();
}

std::shared_ptr<arrow::Table> append_row(
    const std::shared_ptr<arrow::Table>& table, int64_t row) {
  auto combined = arrow::ConcatenateTables({table, table->Slice(row, 1)});
  assert(combined.ok());
  return combined.MoveValueUnsafe();
}

}  // namespace

int main() {
  const int global_thread_capacity = arrow::GetCpuThreadPoolCapacity();
  auto loaded = memq5::load_arrow_q5_dataset(MEMQ5_ARROW_FIXTURE_DIR);
  assert(loaded.ok());
  const auto dataset = loaded.MoveValueUnsafe();

  const auto asia = make_params("ASIA", 2);
  auto specialized = memq5::execute_q5_arrow_cpu(dataset, asia);
  auto acero = memq5::execute_q5_acero(dataset, asia);
  if (!acero.ok()) {
    std::cerr << acero.status().ToString() << '\n';
  }
  assert(specialized.ok());
  assert(acero.ok());
  assert(memq5::result_hash_hex(*specialized) ==
         memq5::result_hash_hex(*acero));
  assert(acero->counters.input_lineitem_rows == 6);
  assert(acero->counters.matched_lineitem_rows == 2);

  auto acero_single_thread =
      memq5::execute_q5_acero(dataset, make_params("ASIA", 1));
  assert(acero_single_thread.ok());
  assert(memq5::result_hash_hex(*acero_single_thread) ==
         memq5::result_hash_hex(*acero));

  auto america = memq5::execute_q5_acero(dataset, make_params("AMERICA", 2));
  assert(america.ok());
  assert(america->rows.size() == 1);
  assert(america->rows[0].nation_name == "ARGENTINA");
  assert(america->rows[0].revenue_1e4 == 1200000);
  assert(america->counters.input_lineitem_rows == 6);
  assert(america->counters.matched_lineitem_rows == 1);

  auto missing =
      memq5::execute_q5_acero(dataset, make_params("NOT_A_REGION", 1));
  assert(!missing.ok());

  auto wrong_scale_dataset = dataset;
  wrong_scale_dataset.lineitem = with_wrong_decimal_scale(dataset.lineitem);
  auto wrong_scale = memq5::execute_q5_acero(
      wrong_scale_dataset, make_params("ASIA", 2));
  assert(!wrong_scale.ok());
  assert(wrong_scale.status().ToString().find("decimal128(15, 2)") !=
         std::string::npos);

  auto overflow_dataset = dataset;
  overflow_dataset.lineitem = replace_prices(
      dataset.lineitem,
      {{0, arrow::Decimal128(std::numeric_limits<int64_t>::max())}});
  auto overflow = memq5::execute_q5_acero(
      overflow_dataset, make_params("ASIA", 2));
  assert(!overflow.ok());
  assert(overflow.status().IsCapacityError());

  auto duplicate_customer_dataset = dataset;
  duplicate_customer_dataset.customer = append_row(dataset.customer, 0);
  auto duplicate_customer = memq5::execute_q5_acero(
      duplicate_customer_dataset, make_params("ASIA", 2));
  assert(!duplicate_customer.ok());
  assert(duplicate_customer.status().ToString().find("duplicate customer key") !=
         std::string::npos);

  assert(arrow::GetCpuThreadPoolCapacity() == global_thread_capacity);
  return 0;
}

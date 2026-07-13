#include "cpu/q5_arrow_cpu.hpp"

#include <arrow/api.h>

#include <cassert>
#include <cstdint>
#include <limits>
#include <map>
#include <string>

#include "engine/q5_params.hpp"
#include "engine/q5_result_io.hpp"
#include "io/arrow_q5_loader.hpp"

namespace {

memq5::Q5Params params_with_threads(int threads) {
  memq5::Q5Params params;
  params.region_name = "ASIA";
  memq5::set_q5_date(&params, "1994-01-01");
  params.threads = threads;
  return params;
}

void assert_tiny_result(const memq5::Q5Result& result) {
  assert(result.rows.size() == 2);
  assert(result.rows[0].nation_name == "JAPAN");
  assert(result.rows[0].revenue_1e4 == 1900000);
  assert(result.rows[1].nation_name == "INDIA");
  assert(result.rows[1].revenue_1e4 == 900000);
  assert(result.counters.input_lineitem_rows == 6);
  assert(result.counters.matched_lineitem_rows == 2);
}

std::shared_ptr<arrow::Table> append_row(
    const std::shared_ptr<arrow::Table>& table, int64_t row) {
  auto combined = arrow::ConcatenateTables({table, table->Slice(row, 1)});
  assert(combined.ok());
  return combined.MoveValueUnsafe();
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

std::shared_ptr<arrow::Table> replace_int32_value(
    const std::shared_ptr<arrow::Table>& table, int column_index,
    int64_t target_row, int32_t replacement) {
  std::vector<std::shared_ptr<arrow::Array>> chunks;
  int64_t global_row = 0;
  for (const auto& chunk : table->column(column_index)->chunks()) {
    const auto values = std::dynamic_pointer_cast<arrow::Int32Array>(chunk);
    assert(values != nullptr);
    arrow::Int32Builder builder;
    for (int64_t row = 0; row < values->length(); ++row, ++global_row) {
      const auto status = builder.Append(global_row == target_row
                                             ? replacement
                                             : values->Value(row));
      assert(status.ok());
    }
    std::shared_ptr<arrow::Array> output;
    const auto finish = builder.Finish(&output);
    assert(finish.ok());
    chunks.push_back(std::move(output));
  }
  const auto column = std::make_shared<arrow::ChunkedArray>(
      std::move(chunks), arrow::int32());
  auto changed = table->SetColumn(column_index, table->schema()->field(column_index),
                                  column);
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

}  // namespace

int main() {
  auto loaded = memq5::load_arrow_q5_dataset(MEMQ5_ARROW_FIXTURE_DIR);
  assert(loaded.ok());
  const auto dataset = loaded.MoveValueUnsafe();

  assert(dataset.region != nullptr);
  assert(dataset.nation != nullptr);
  assert(dataset.supplier != nullptr);
  assert(dataset.customer != nullptr);
  assert(dataset.orders != nullptr);
  assert(dataset.lineitem != nullptr);
  assert(dataset.lineitem->column(0)->num_chunks() == 3);

  auto one = memq5::execute_q5_arrow_cpu(dataset, params_with_threads(1));
  auto two = memq5::execute_q5_arrow_cpu(dataset, params_with_threads(2));
  auto eight = memq5::execute_q5_arrow_cpu(dataset, params_with_threads(8));
  assert(one.ok());
  assert(two.ok());
  assert(eight.ok());
  assert_tiny_result(*one);
  assert_tiny_result(*two);
  assert_tiny_result(*eight);
  assert(memq5::result_hash_hex(*one) == memq5::result_hash_hex(*two));
  assert(memq5::result_hash_hex(*one) == memq5::result_hash_hex(*eight));

  auto missing_region_params = params_with_threads(1);
  missing_region_params.region_name = "NOT_A_REGION";
  auto missing_region =
      memq5::execute_q5_arrow_cpu(dataset, missing_region_params);
  assert(!missing_region.ok());
  assert(missing_region.status().ToString().find("region not found") !=
         std::string::npos);

  auto duplicate_region_dataset = dataset;
  duplicate_region_dataset.region = append_row(dataset.region, 0);
  auto duplicate_region = memq5::execute_q5_arrow_cpu(
      duplicate_region_dataset, params_with_threads(2));
  assert(!duplicate_region.ok());
  assert(duplicate_region.status().ToString().find("duplicate region key") !=
         std::string::npos);

  auto duplicate_customer_dataset = dataset;
  duplicate_customer_dataset.customer = append_row(dataset.customer, 3);
  auto duplicate_customer = memq5::execute_q5_arrow_cpu(
      duplicate_customer_dataset, params_with_threads(2));
  assert(!duplicate_customer.ok());
  assert(duplicate_customer.status().ToString().find("duplicate customer key") !=
         std::string::npos);

  auto duplicate_supplier_dataset = dataset;
  duplicate_supplier_dataset.supplier = append_row(dataset.supplier, 2);
  auto duplicate_supplier = memq5::execute_q5_arrow_cpu(
      duplicate_supplier_dataset, params_with_threads(2));
  assert(!duplicate_supplier.ok());
  assert(duplicate_supplier.status().ToString().find("duplicate supplier key") !=
         std::string::npos);

  auto duplicate_order_dataset = dataset;
  duplicate_order_dataset.orders = append_row(dataset.orders, 2);
  auto duplicate_order = memq5::execute_q5_arrow_cpu(
      duplicate_order_dataset, params_with_threads(2));
  assert(!duplicate_order.ok());
  assert(duplicate_order.status().ToString().find("duplicate order key") !=
         std::string::npos);

  auto wrong_scale_dataset = dataset;
  wrong_scale_dataset.lineitem = with_wrong_decimal_scale(dataset.lineitem);
  auto wrong_scale = memq5::execute_q5_arrow_cpu(
      wrong_scale_dataset, params_with_threads(2));
  assert(!wrong_scale.ok());
  assert(wrong_scale.status().ToString().find("decimal128(15, 2)") !=
         std::string::npos);

  auto wide_decimal_dataset = dataset;
  wide_decimal_dataset.lineitem = replace_prices(
      dataset.lineitem, {{0, arrow::Decimal128(1, 0)}});
  auto wide_decimal = memq5::execute_q5_arrow_cpu(
      wide_decimal_dataset, params_with_threads(2));
  assert(!wide_decimal.ok());
  assert(wide_decimal.status().ToString().find("Invalid cast from Decimal128") !=
         std::string::npos);

  auto huge_key_dataset = dataset;
  huge_key_dataset.nation = replace_int32_value(
      dataset.nation, 0, 0, std::numeric_limits<int32_t>::max());
  auto huge_key = memq5::execute_q5_arrow_cpu(
      huge_key_dataset, params_with_threads(2));
  assert(!huge_key.ok());
  assert(huge_key.status().IsCapacityError());

  const int64_t large_price = std::numeric_limits<int64_t>::max() / 100;
  auto overflow_dataset = dataset;
  overflow_dataset.lineitem = replace_int32_value(dataset.lineitem, 1, 1, 1);
  overflow_dataset.lineitem = replace_prices(
      overflow_dataset.lineitem,
      {{0, arrow::Decimal128(large_price)},
       {1, arrow::Decimal128(large_price)}});
  auto local_overflow = memq5::execute_q5_arrow_cpu(
      overflow_dataset, params_with_threads(1));
  assert(!local_overflow.ok());
  assert(local_overflow.status().IsCapacityError());
  auto merge_overflow = memq5::execute_q5_arrow_cpu(
      overflow_dataset, params_with_threads(6));
  assert(!merge_overflow.ok());
  assert(merge_overflow.status().IsCapacityError());
  return 0;
}

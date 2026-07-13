#include "cpu/q5_acero.hpp"

#include <algorithm>
#include <cstdint>
#include <exception>
#include <map>
#include <memory>
#include <new>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <arrow/acero/exec_plan.h>
#include <arrow/acero/options.h>
#include <arrow/api.h>
#include <arrow/compute/api.h>
#include <arrow/compute/expression.h>
#include <arrow/compute/initialize.h>
#include <arrow/util/thread_pool.h>

#include "common/fixed_point.hpp"
#include "common/timer.hpp"

namespace memq5 {
namespace {

namespace acero = arrow::acero;
namespace compute = arrow::compute;

using acero::Declaration;
using arrow::FieldRef;

Declaration table_source(const std::shared_ptr<arrow::Table>& table) {
  return Declaration{"table_source", acero::TableSourceNodeOptions(table)};
}

compute::Expression ref(const std::string& name) {
  return compute::field_ref(FieldRef(name));
}

std::vector<FieldRef> refs(std::initializer_list<const char*> names) {
  std::vector<FieldRef> fields;
  fields.reserve(names.size());
  for (const char* name : names) {
    fields.emplace_back(name);
  }
  return fields;
}

Declaration filter(Declaration input, compute::Expression predicate) {
  return Declaration{"filter", {std::move(input)},
                     acero::FilterNodeOptions(std::move(predicate))};
}

arrow::Result<std::shared_ptr<arrow::Table>> decode_dictionary_column(
    const std::shared_ptr<arrow::Table>& table, const std::string& column_name) {
  const int column_index = table->schema()->GetFieldIndex(column_name);
  if (column_index < 0) {
    return arrow::Status::Invalid("missing dictionary column: ", column_name);
  }
  ARROW_ASSIGN_OR_RAISE(
      const arrow::Datum decoded,
      compute::CallFunction("dictionary_decode",
                            {arrow::Datum(table->column(column_index))}));
  if (!decoded.is_chunked_array()) {
    return arrow::Status::Invalid("dictionary decode did not return chunks: ",
                                  column_name);
  }
  return table->SetColumn(
      column_index, arrow::field(column_name, arrow::utf8(), false),
      decoded.chunked_array());
}

arrow::Result<bool> string_column_contains(
    const std::shared_ptr<arrow::Table>& table, const std::string& column_name,
    const std::string& expected) {
  const auto column = table->GetColumnByName(column_name);
  if (column == nullptr) {
    return arrow::Status::Invalid("missing string column: ", column_name);
  }
  for (const auto& chunk : column->chunks()) {
    const auto strings = std::dynamic_pointer_cast<arrow::StringArray>(chunk);
    if (strings == nullptr || strings->null_count() != 0) {
      return arrow::Status::Invalid("invalid string column: ", column_name);
    }
    for (int64_t row = 0; row < strings->length(); ++row) {
      if (strings->GetView(row) == expected) {
        return true;
      }
    }
  }
  return false;
}

Declaration join(Declaration left, Declaration right,
                 std::initializer_list<const char*> left_keys,
                 std::initializer_list<const char*> right_keys,
                 std::initializer_list<const char*> left_output,
                 std::initializer_list<const char*> right_output) {
  return Declaration{
      "hashjoin", {std::move(left), std::move(right)},
      acero::HashJoinNodeOptions(acero::JoinType::INNER, refs(left_keys),
                                 refs(right_keys), refs(left_output),
                                 refs(right_output))};
}

arrow::Status require_unique_primary_key(
    const std::shared_ptr<arrow::Table>& table, const std::string& table_name,
    const std::string& column_name) {
  const auto column = table->GetColumnByName(column_name);
  if (column == nullptr || column->type()->id() != arrow::Type::INT32 ||
      column->null_count() != 0) {
    return arrow::Status::Invalid("invalid primary key column: ", table_name,
                                  ".", column_name);
  }
  ARROW_ASSIGN_OR_RAISE(
      const arrow::Datum distinct,
      compute::CallFunction("count_distinct", {arrow::Datum(column)}));
  const auto count =
      std::dynamic_pointer_cast<arrow::Int64Scalar>(distinct.scalar());
  if (count == nullptr || !count->is_valid) {
    return arrow::Status::Invalid("cannot count primary key: ", table_name,
                                  ".", column_name);
  }
  if (count->value != table->num_rows()) {
    return arrow::Status::Invalid("duplicate ", table_name, " key in ",
                                  column_name);
  }
  return arrow::Status::OK();
}

arrow::Status validate_primary_keys(const ArrowQ5Dataset& dataset) {
  ARROW_RETURN_NOT_OK(
      require_unique_primary_key(dataset.region, "region", "r_regionkey"));
  ARROW_RETURN_NOT_OK(
      require_unique_primary_key(dataset.nation, "nation", "n_nationkey"));
  ARROW_RETURN_NOT_OK(
      require_unique_primary_key(dataset.supplier, "supplier", "s_suppkey"));
  ARROW_RETURN_NOT_OK(
      require_unique_primary_key(dataset.customer, "customer", "c_custkey"));
  return require_unique_primary_key(dataset.orders, "order", "o_orderkey");
}

arrow::Result<Declaration> q5_relational_plan(const ArrowQ5Dataset& dataset,
                                              const Q5Params& params) {
  if (dataset.region == nullptr || dataset.nation == nullptr ||
      dataset.supplier == nullptr || dataset.customer == nullptr ||
      dataset.orders == nullptr || dataset.lineitem == nullptr) {
    return arrow::Status::Invalid("Arrow Q5 dataset is incomplete");
  }
  ARROW_RETURN_NOT_OK(validate_primary_keys(dataset));

  ARROW_ASSIGN_OR_RAISE(const auto decoded_region,
                        decode_dictionary_column(dataset.region, "r_name"));
  ARROW_ASSIGN_OR_RAISE(const auto decoded_nation,
                        decode_dictionary_column(dataset.nation, "n_name"));
  ARROW_ASSIGN_OR_RAISE(
      const bool region_exists,
      string_column_contains(decoded_region, "r_name", params.region_name));
  if (!region_exists) {
    return arrow::Status::Invalid("region not found: ", params.region_name);
  }

  auto region = table_source(decoded_region);
  region = filter(
      std::move(region),
      compute::call("equal",
                    {ref("r_name"), compute::literal(std::make_shared<
                                         arrow::StringScalar>(params.region_name))}));

  auto nation = table_source(decoded_nation);
  auto nation_in_region =
      join(std::move(nation), std::move(region), {"n_regionkey"},
           {"r_regionkey"}, {"n_nationkey", "n_name"}, {});

  auto customer_nation =
      join(table_source(dataset.customer), std::move(nation_in_region),
           {"c_nationkey"}, {"n_nationkey"},
           {"c_custkey", "c_nationkey"}, {"n_name"});

  auto orders = filter(
      table_source(dataset.orders),
      compute::call(
          "and_kleene",
          {compute::call(
               "greater_equal",
               {ref("o_orderdate"),
                compute::literal(std::make_shared<arrow::Date32Scalar>(
                    params.start_date_days))}),
           compute::call(
               "less",
               {ref("o_orderdate"),
                compute::literal(std::make_shared<arrow::Date32Scalar>(
                    params.end_date_days))})}));
  auto order_customer =
      join(std::move(orders), std::move(customer_nation), {"o_custkey"},
           {"c_custkey"}, {"o_orderkey"}, {"c_nationkey", "n_name"});

  auto line_order =
      join(table_source(dataset.lineitem), std::move(order_customer),
           {"l_orderkey"}, {"o_orderkey"},
           {"l_suppkey", "l_extendedprice", "l_discount"},
           {"c_nationkey", "n_name"});
  auto with_supplier =
      join(std::move(line_order), table_source(dataset.supplier),
           {"l_suppkey"}, {"s_suppkey"},
           {"l_extendedprice", "l_discount", "c_nationkey", "n_name"},
           {"s_nationkey"});
  return filter(std::move(with_supplier),
                compute::call("equal",
                              {ref("c_nationkey"), ref("s_nationkey")}));
}

template <typename ArrayType>
arrow::Result<std::shared_ptr<ArrayType>> required_array(
    const arrow::RecordBatch& batch, const std::string& name,
    arrow::Type::type expected_type) {
  const auto array = batch.GetColumnByName(name);
  if (array == nullptr || array->type_id() != expected_type ||
      array->null_count() != 0) {
    return arrow::Status::Invalid("invalid Acero result column: ", name);
  }
  const auto typed = std::dynamic_pointer_cast<ArrayType>(array);
  if (typed == nullptr) {
    return arrow::Status::Invalid("unexpected Acero result array: ", name);
  }
  return typed;
}

arrow::Result<std::shared_ptr<arrow::Decimal128Array>> required_decimal_array(
    const arrow::RecordBatch& batch, const std::string& name) {
  ARROW_ASSIGN_OR_RAISE(
      const auto array,
      required_array<arrow::Decimal128Array>(batch, name,
                                             arrow::Type::DECIMAL128));
  const auto expected_type = arrow::decimal128(15, 2);
  if (!array->type()->Equals(expected_type)) {
    return arrow::Status::Invalid("Acero result column ", name,
                                  " must be decimal128(15, 2), got ",
                                  array->type()->ToString());
  }
  return array;
}

arrow::Status checked_accumulate(int64_t value, int64_t* destination) {
  int64_t sum = 0;
  if (__builtin_add_overflow(*destination, value, &sum)) {
    return arrow::Status::CapacityError("Acero Q5 revenue overflow");
  }
  *destination = sum;
  return arrow::Status::OK();
}

arrow::Result<std::map<std::string, int64_t>> aggregate_exact_revenue(
    const std::shared_ptr<arrow::Table>& joined) {
  std::map<std::string, int64_t> revenue_by_nation;
  arrow::TableBatchReader reader(joined);
  while (true) {
    std::shared_ptr<arrow::RecordBatch> batch;
    ARROW_RETURN_NOT_OK(reader.ReadNext(&batch));
    if (batch == nullptr) {
      break;
    }
    ARROW_ASSIGN_OR_RAISE(
        const auto names,
        required_array<arrow::StringArray>(*batch, "n_name", arrow::Type::STRING));
    ARROW_ASSIGN_OR_RAISE(
        const auto prices, required_decimal_array(*batch, "l_extendedprice"));
    ARROW_ASSIGN_OR_RAISE(
        const auto discounts, required_decimal_array(*batch, "l_discount"));
    for (int64_t row = 0; row < batch->num_rows(); ++row) {
      ARROW_ASSIGN_OR_RAISE(
          const int64_t price_cents,
          arrow::Decimal128(prices->GetValue(row)).ToInteger<int64_t>());
      ARROW_ASSIGN_OR_RAISE(
          const int64_t discount_value,
          arrow::Decimal128(discounts->GetValue(row)).ToInteger<int64_t>());
      if (price_cents < 0 || discount_value < 0 || discount_value > 100) {
        return arrow::Status::Invalid("invalid Q5 decimal input");
      }
      const int64_t revenue = compute_revenue_1e4(
          price_cents, static_cast<int32_t>(discount_value));
      ARROW_RETURN_NOT_OK(checked_accumulate(
          revenue, &revenue_by_nation[std::string(names->GetView(row))]));
    }
  }
  return revenue_by_nation;
}

}  // namespace

arrow::Result<Q5Result> execute_q5_acero_impl(const ArrowQ5Dataset& dataset,
                                              const Q5Params& params) {
  Stopwatch total_timer;
  ARROW_RETURN_NOT_OK(compute::Initialize());
  ARROW_ASSIGN_OR_RAISE(
      const auto executor,
      arrow::internal::ThreadPool::Make(std::max(1, params.threads)));
  Stopwatch build_timer;
  ARROW_ASSIGN_OR_RAISE(Declaration declaration,
                        q5_relational_plan(dataset, params));

  Q5Result result;
  result.timing.build_ms = build_timer.elapsed_ms();
  Stopwatch scan_timer;
  compute::ExecContext exec_context(arrow::default_memory_pool(), executor.get());
  auto joined_future = acero::DeclarationToTableAsync(
      std::move(declaration), exec_context);
  ARROW_ASSIGN_OR_RAISE(
      const auto joined, joined_future.MoveResult());
  ARROW_ASSIGN_OR_RAISE(const auto revenue_by_nation,
                        aggregate_exact_revenue(joined));
  result.timing.scan_ms = scan_timer.elapsed_ms();

  for (const auto& [name, revenue] : revenue_by_nation) {
    if (revenue != 0) {
      result.rows.push_back(Q5ResultRow{name, revenue});
    }
  }
  std::sort(result.rows.begin(), result.rows.end(),
            [](const Q5ResultRow& left, const Q5ResultRow& right) {
              if (left.revenue_1e4 != right.revenue_1e4) {
                return left.revenue_1e4 > right.revenue_1e4;
              }
              return left.nation_name < right.nation_name;
            });
  result.counters.input_lineitem_rows = dataset.lineitem->num_rows();
  result.counters.matched_lineitem_rows = joined->num_rows();
  result.counters.cpu_input_rows = dataset.lineitem->num_rows();
  result.timing.total_ms = total_timer.elapsed_ms();
  return result;
}

arrow::Result<Q5Result> execute_q5_acero(const ArrowQ5Dataset& dataset,
                                         const Q5Params& params) {
  try {
    return execute_q5_acero_impl(dataset, params);
  } catch (const std::overflow_error& error) {
    return arrow::Status::CapacityError("Acero Q5 arithmetic overflow: ",
                                        error.what());
  } catch (const std::bad_alloc&) {
    return arrow::Status::CapacityError("Acero Q5 allocation failed");
  } catch (const std::exception& error) {
    return arrow::Status::UnknownError("Acero Q5 failed: ", error.what());
  }
}

}  // namespace memq5

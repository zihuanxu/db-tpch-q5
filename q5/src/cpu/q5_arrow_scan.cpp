#include "cpu/q5_arrow_scan.hpp"

#include <algorithm>
#include <cstdint>
#include <exception>
#include <memory>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <arrow/api.h>

#include "common/fixed_point.hpp"
#include "common/timer.hpp"

namespace memq5 {
namespace {

struct ScanPartial {
  std::vector<int64_t> revenue_by_nation;
  int64_t input_rows = 0;
  int64_t matched_rows = 0;
  arrow::Status status = arrow::Status::OK();
};

class ThreadGroup {
 public:
  explicit ThreadGroup(std::size_t capacity) { workers_.reserve(capacity); }

  ~ThreadGroup() { join(); }

  template <typename Function>
  void start(Function&& function) {
    workers_.emplace_back(std::forward<Function>(function));
  }

  void join() noexcept {
    for (auto& worker : workers_) {
      if (worker.joinable()) {
        worker.join();
      }
    }
  }

 private:
  std::vector<std::thread> workers_;
};

arrow::Result<std::vector<std::shared_ptr<arrow::RecordBatch>>> table_batches(
    const std::shared_ptr<arrow::Table>& table) {
  if (table == nullptr) {
    return arrow::Status::Invalid("missing Arrow table");
  }
  arrow::TableBatchReader reader(table);
  std::vector<std::shared_ptr<arrow::RecordBatch>> batches;
  while (true) {
    std::shared_ptr<arrow::RecordBatch> batch;
    ARROW_RETURN_NOT_OK(reader.ReadNext(&batch));
    if (batch == nullptr) {
      break;
    }
    batches.push_back(std::move(batch));
  }
  return batches;
}

template <typename ArrayType>
arrow::Result<std::shared_ptr<ArrayType>> required_array(
    const arrow::RecordBatch& batch, const std::string& column_name,
    arrow::Type::type expected_type) {
  const auto array = batch.GetColumnByName(column_name);
  if (array == nullptr) {
    return arrow::Status::Invalid("missing Arrow column: ", column_name);
  }
  if (array->type_id() != expected_type || array->null_count() != 0) {
    return arrow::Status::Invalid("invalid Arrow column: ", column_name);
  }
  const auto typed = std::dynamic_pointer_cast<ArrayType>(array);
  if (typed == nullptr) {
    return arrow::Status::Invalid("unexpected Arrow array class: ", column_name);
  }
  return typed;
}

arrow::Result<std::shared_ptr<arrow::Decimal128Array>> required_decimal_array(
    const arrow::RecordBatch& batch, const std::string& column_name) {
  ARROW_ASSIGN_OR_RAISE(
      const auto array,
      required_array<arrow::Decimal128Array>(batch, column_name,
                                             arrow::Type::DECIMAL128));
  const auto expected_type = arrow::decimal128(15, 2);
  if (!array->type()->Equals(expected_type)) {
    return arrow::Status::Invalid("Arrow column ", column_name,
                                  " must be decimal128(15, 2), got ",
                                  array->type()->ToString());
  }
  return array;
}

bool valid_key(const std::vector<int32_t>& values, int32_t key) {
  return key >= 0 && static_cast<std::size_t>(key) < values.size();
}

arrow::Status checked_accumulate(int64_t value, int64_t* destination) {
  int64_t sum = 0;
  if (__builtin_add_overflow(*destination, value, &sum)) {
    return arrow::Status::CapacityError("Q5 revenue accumulation overflow");
  }
  *destination = sum;
  return arrow::Status::OK();
}

arrow::Status scan_batches(
    const std::vector<std::shared_ptr<arrow::RecordBatch>>& batches,
    const ArrowQ5Plan& plan, std::size_t global_begin, std::size_t global_end,
    ScanPartial* partial) {
  try {
    std::size_t batch_begin = 0;
    for (const auto& batch : batches) {
      const auto batch_rows = static_cast<std::size_t>(batch->num_rows());
      const std::size_t batch_end = batch_begin + batch_rows;
      if (batch_end <= global_begin) {
        batch_begin = batch_end;
        continue;
      }
      if (batch_begin >= global_end) {
        break;
      }
      const std::size_t local_begin =
          std::max(global_begin, batch_begin) - batch_begin;
      const std::size_t local_end =
          std::min(global_end, batch_end) - batch_begin;
      ARROW_ASSIGN_OR_RAISE(
          const auto order_keys,
          required_array<arrow::Int32Array>(*batch, "l_orderkey", arrow::Type::INT32));
      ARROW_ASSIGN_OR_RAISE(
          const auto supplier_keys,
          required_array<arrow::Int32Array>(*batch, "l_suppkey", arrow::Type::INT32));
      ARROW_ASSIGN_OR_RAISE(
          const auto prices, required_decimal_array(*batch, "l_extendedprice"));
      ARROW_ASSIGN_OR_RAISE(
          const auto discounts, required_decimal_array(*batch, "l_discount"));
      partial->input_rows += static_cast<int64_t>(local_end - local_begin);
      for (std::size_t row_index = local_begin; row_index < local_end;
           ++row_index) {
        const int64_t row = static_cast<int64_t>(row_index);
        const int32_t order_key = order_keys->Value(row);
        const int32_t supplier_key = supplier_keys->Value(row);
        if (!valid_key(plan.order_nation_by_key, order_key) ||
            !valid_key(plan.supplier_nation_by_key, supplier_key)) {
          continue;
        }
        const int32_t order_nation =
            plan.order_nation_by_key[static_cast<std::size_t>(order_key)];
        const int32_t supplier_nation =
            plan.supplier_nation_by_key[static_cast<std::size_t>(supplier_key)];
        if (order_nation < 0 || order_nation != supplier_nation) {
          continue;
        }

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
            revenue, &partial->revenue_by_nation[static_cast<std::size_t>(
                         order_nation)]));
        ++partial->matched_rows;
      }
      batch_begin = batch_end;
    }
  } catch (const std::exception& error) {
    return arrow::Status::Invalid("Arrow CPU Q5 scan failed: ", error.what());
  }
  return arrow::Status::OK();
}

}  // namespace

arrow::Result<Q5Result> scan_q5_arrow_lineitem(
    const std::shared_ptr<arrow::Table>& lineitem, const ArrowQ5Plan& plan,
    int threads) {
  ARROW_ASSIGN_OR_RAISE(const auto batches, table_batches(lineitem));

  const auto lineitem_count = static_cast<std::size_t>(lineitem->num_rows());
  const int requested_threads = std::max(1, threads);
  const int worker_count = static_cast<int>(std::min<std::size_t>(
      static_cast<std::size_t>(requested_threads),
      std::max<std::size_t>(lineitem_count, 1)));
  std::vector<ScanPartial> partials(static_cast<std::size_t>(worker_count));
  const std::size_t nation_count =
      plan.max_nation_key < 0
          ? 0
          : static_cast<std::size_t>(plan.max_nation_key) + 1;
  for (auto& partial : partials) {
    partial.revenue_by_nation.resize(nation_count, 0);
  }

  Stopwatch scan_timer;
  ThreadGroup workers(static_cast<std::size_t>(worker_count));
  for (int worker = 0; worker < worker_count; ++worker) {
    const std::size_t begin =
        lineitem_count * static_cast<std::size_t>(worker) /
        static_cast<std::size_t>(worker_count);
    const std::size_t end =
        lineitem_count * static_cast<std::size_t>(worker + 1) /
        static_cast<std::size_t>(worker_count);
    workers.start([&batches, &plan, begin, end, &partials, worker]() {
      auto& partial = partials[static_cast<std::size_t>(worker)];
      partial.status = scan_batches(batches, plan, begin, end, &partial);
    });
  }
  workers.join();

  Q5Result result;
  result.timing.scan_ms = scan_timer.elapsed_ms();
  std::vector<int64_t> revenue_by_nation(nation_count, 0);
  for (const auto& partial : partials) {
    ARROW_RETURN_NOT_OK(partial.status);
    result.counters.input_lineitem_rows += partial.input_rows;
    result.counters.matched_lineitem_rows += partial.matched_rows;
    result.counters.cpu_input_rows += partial.input_rows;
    for (std::size_t nation = 0; nation < revenue_by_nation.size(); ++nation) {
      ARROW_RETURN_NOT_OK(checked_accumulate(partial.revenue_by_nation[nation],
                                             &revenue_by_nation[nation]));
    }
  }

  for (std::size_t nation = 0; nation < revenue_by_nation.size(); ++nation) {
    if (revenue_by_nation[nation] != 0) {
      result.rows.push_back(
          Q5ResultRow{plan.nation_name_by_key[nation], revenue_by_nation[nation]});
    }
  }
  std::sort(result.rows.begin(), result.rows.end(),
            [](const Q5ResultRow& left, const Q5ResultRow& right) {
              if (left.revenue_1e4 != right.revenue_1e4) {
                return left.revenue_1e4 > right.revenue_1e4;
              }
              return left.nation_name < right.nation_name;
            });
  return result;
}

}  // namespace memq5

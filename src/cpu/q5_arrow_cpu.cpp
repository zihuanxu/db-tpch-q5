#include "cpu/q5_arrow_cpu.hpp"

#include <algorithm>
#include <cstdint>
#include <exception>
#include <limits>
#include <memory>
#include <new>
#include <stdexcept>
#include <string>
#include <system_error>
#include <thread>
#include <unordered_set>
#include <utility>
#include <vector>

#include <arrow/api.h>

#include "common/fixed_point.hpp"
#include "common/timer.hpp"
#include "engine/arrow_q5_plan.hpp"

namespace memq5 {
namespace {

struct ScanPartial {
  std::vector<int64_t> revenue_by_nation;
  int64_t input_rows = 0;
  int64_t matched_rows = 0;
  arrow::Status status = arrow::Status::OK();
};

constexpr std::size_t kMaxDirectKey = 100000000;

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

arrow::Result<std::string> dictionary_string(
    const arrow::DictionaryArray& array, int64_t row) {
  const auto indices =
      std::dynamic_pointer_cast<arrow::Int32Array>(array.indices());
  const auto dictionary =
      std::dynamic_pointer_cast<arrow::StringArray>(array.dictionary());
  if (indices == nullptr || dictionary == nullptr) {
    return arrow::Status::Invalid("Q5 dictionary must use int32 and utf8");
  }
  const int32_t index = indices->Value(row);
  if (index < 0 || index >= dictionary->length()) {
    return arrow::Status::Invalid("dictionary index is out of range");
  }
  return std::string(dictionary->GetView(index));
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

arrow::Status resize_for_key(std::vector<int32_t>* values, int32_t key,
                             int32_t fill_value = -1) {
  if (key < 0) {
    return arrow::Status::Invalid("negative TPC-H primary key");
  }
  const auto target_size = static_cast<std::size_t>(key) + 1;
  if (target_size > kMaxDirectKey + 1) {
    return arrow::Status::CapacityError("TPC-H key is too large for direct index: ",
                                        key);
  }
  if (target_size > values->size()) {
    values->resize(target_size, fill_value);
  }
  return arrow::Status::OK();
}

arrow::Status resize_for_nation(std::vector<std::string>* names,
                                std::vector<uint8_t>* in_region, int32_t key) {
  if (key < 0) {
    return arrow::Status::Invalid("negative nation key");
  }
  const auto target_size = static_cast<std::size_t>(key) + 1;
  if (target_size > kMaxDirectKey + 1) {
    return arrow::Status::CapacityError("nation key is too large: ", key);
  }
  if (target_size > names->size()) {
    names->resize(target_size);
    in_region->resize(target_size, 0);
  }
  return arrow::Status::OK();
}

bool valid_key(const std::vector<int32_t>& values, int32_t key) {
  return key >= 0 && static_cast<std::size_t>(key) < values.size();
}

}  // namespace

arrow::Result<ArrowQ5Plan> build_arrow_q5_plan(const ArrowQ5Dataset& dataset,
                                               const Q5Params& params) {
  Stopwatch timer;
  int32_t region_key = -1;
  std::unordered_set<int32_t> region_keys;
  ARROW_ASSIGN_OR_RAISE(const auto region_batches,
                        table_batches(dataset.region));
  for (const auto& batch : region_batches) {
    ARROW_ASSIGN_OR_RAISE(
        const auto keys,
        required_array<arrow::Int32Array>(*batch, "r_regionkey", arrow::Type::INT32));
    ARROW_ASSIGN_OR_RAISE(
        const auto names,
        required_array<arrow::DictionaryArray>(*batch, "r_name",
                                               arrow::Type::DICTIONARY));
    for (int64_t row = 0; row < batch->num_rows(); ++row) {
      const int32_t key = keys->Value(row);
      if (key < 0) {
        return arrow::Status::Invalid("negative region key");
      }
      if (!region_keys.insert(key).second) {
        return arrow::Status::Invalid("duplicate region key: ", key);
      }
      ARROW_ASSIGN_OR_RAISE(const std::string name,
                            dictionary_string(*names, row));
      if (name == params.region_name) {
        if (region_key >= 0) {
          return arrow::Status::Invalid("duplicate region name: ", name);
        }
        region_key = key;
      }
    }
  }
  if (region_key < 0) {
    return arrow::Status::Invalid("region not found: ", params.region_name);
  }

  ArrowQ5Plan plan;
  std::vector<uint8_t> nation_in_region;
  std::vector<uint8_t> nation_seen;
  ARROW_ASSIGN_OR_RAISE(const auto nation_batches,
                        table_batches(dataset.nation));
  for (const auto& batch : nation_batches) {
    ARROW_ASSIGN_OR_RAISE(
        const auto keys,
        required_array<arrow::Int32Array>(*batch, "n_nationkey", arrow::Type::INT32));
    ARROW_ASSIGN_OR_RAISE(
        const auto names,
        required_array<arrow::DictionaryArray>(*batch, "n_name",
                                               arrow::Type::DICTIONARY));
    ARROW_ASSIGN_OR_RAISE(
        const auto region_keys,
        required_array<arrow::Int32Array>(*batch, "n_regionkey", arrow::Type::INT32));
    for (int64_t row = 0; row < batch->num_rows(); ++row) {
      const int32_t key = keys->Value(row);
      ARROW_RETURN_NOT_OK(
          resize_for_nation(&plan.nation_name_by_key, &nation_in_region, key));
      if (nation_seen.size() < plan.nation_name_by_key.size()) {
        nation_seen.resize(plan.nation_name_by_key.size(), 0);
      }
      if (nation_seen[static_cast<std::size_t>(key)] != 0) {
        return arrow::Status::Invalid("duplicate nation key: ", key);
      }
      nation_seen[static_cast<std::size_t>(key)] = 1;
      ARROW_ASSIGN_OR_RAISE(
          plan.nation_name_by_key[static_cast<std::size_t>(key)],
          dictionary_string(*names, row));
      if (region_keys->Value(row) == region_key) {
        nation_in_region[static_cast<std::size_t>(key)] = 1;
      }
      plan.max_nation_key = std::max(plan.max_nation_key, key);
    }
  }

  ARROW_ASSIGN_OR_RAISE(const auto supplier_batches,
                        table_batches(dataset.supplier));
  std::vector<uint8_t> supplier_seen;
  for (const auto& batch : supplier_batches) {
    ARROW_ASSIGN_OR_RAISE(
        const auto keys,
        required_array<arrow::Int32Array>(*batch, "s_suppkey", arrow::Type::INT32));
    ARROW_ASSIGN_OR_RAISE(
        const auto nations,
        required_array<arrow::Int32Array>(*batch, "s_nationkey", arrow::Type::INT32));
    for (int64_t row = 0; row < batch->num_rows(); ++row) {
      const int32_t key = keys->Value(row);
      ARROW_RETURN_NOT_OK(resize_for_key(&plan.supplier_nation_by_key, key));
      if (supplier_seen.size() < plan.supplier_nation_by_key.size()) {
        supplier_seen.resize(plan.supplier_nation_by_key.size(), 0);
      }
      if (supplier_seen[static_cast<std::size_t>(key)] != 0) {
        return arrow::Status::Invalid("duplicate supplier key: ", key);
      }
      supplier_seen[static_cast<std::size_t>(key)] = 1;
      auto& destination = plan.supplier_nation_by_key[static_cast<std::size_t>(key)];
      const int32_t nation = nations->Value(row);
      if (nation >= 0 && static_cast<std::size_t>(nation) < nation_in_region.size() &&
          nation_in_region[static_cast<std::size_t>(nation)] != 0) {
        destination = nation;
      }
    }
  }

  std::vector<int32_t> customer_nation_by_key;
  std::vector<uint8_t> customer_seen;
  ARROW_ASSIGN_OR_RAISE(const auto customer_batches,
                        table_batches(dataset.customer));
  for (const auto& batch : customer_batches) {
    ARROW_ASSIGN_OR_RAISE(
        const auto keys,
        required_array<arrow::Int32Array>(*batch, "c_custkey", arrow::Type::INT32));
    ARROW_ASSIGN_OR_RAISE(
        const auto nations,
        required_array<arrow::Int32Array>(*batch, "c_nationkey", arrow::Type::INT32));
    for (int64_t row = 0; row < batch->num_rows(); ++row) {
      const int32_t key = keys->Value(row);
      ARROW_RETURN_NOT_OK(resize_for_key(&customer_nation_by_key, key));
      if (customer_seen.size() < customer_nation_by_key.size()) {
        customer_seen.resize(customer_nation_by_key.size(), 0);
      }
      if (customer_seen[static_cast<std::size_t>(key)] != 0) {
        return arrow::Status::Invalid("duplicate customer key: ", key);
      }
      customer_seen[static_cast<std::size_t>(key)] = 1;
      auto& destination = customer_nation_by_key[static_cast<std::size_t>(key)];
      const int32_t nation = nations->Value(row);
      if (nation >= 0 && static_cast<std::size_t>(nation) < nation_in_region.size() &&
          nation_in_region[static_cast<std::size_t>(nation)] != 0) {
        destination = nation;
      }
    }
  }

  ARROW_ASSIGN_OR_RAISE(const auto order_batches,
                        table_batches(dataset.orders));
  std::vector<uint8_t> order_seen;
  for (const auto& batch : order_batches) {
    ARROW_ASSIGN_OR_RAISE(
        const auto keys,
        required_array<arrow::Int32Array>(*batch, "o_orderkey", arrow::Type::INT32));
    ARROW_ASSIGN_OR_RAISE(
        const auto customers,
        required_array<arrow::Int32Array>(*batch, "o_custkey", arrow::Type::INT32));
    ARROW_ASSIGN_OR_RAISE(
        const auto dates,
        required_array<arrow::Date32Array>(*batch, "o_orderdate", arrow::Type::DATE32));
    for (int64_t row = 0; row < batch->num_rows(); ++row) {
      const int32_t key = keys->Value(row);
      ARROW_RETURN_NOT_OK(resize_for_key(&plan.order_nation_by_key, key));
      if (order_seen.size() < plan.order_nation_by_key.size()) {
        order_seen.resize(plan.order_nation_by_key.size(), 0);
      }
      if (order_seen[static_cast<std::size_t>(key)] != 0) {
        return arrow::Status::Invalid("duplicate order key: ", key);
      }
      order_seen[static_cast<std::size_t>(key)] = 1;
      auto& destination = plan.order_nation_by_key[static_cast<std::size_t>(key)];
      const int32_t customer = customers->Value(row);
      const int32_t date = dates->Value(row);
      if (date >= params.start_date_days && date < params.end_date_days &&
          valid_key(customer_nation_by_key, customer)) {
        destination = customer_nation_by_key[static_cast<std::size_t>(customer)];
      }
    }
  }

  plan.build_ms = timer.elapsed_ms();
  return plan;
}

namespace {

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

arrow::Result<Q5Result> execute_q5_arrow_cpu_impl(const ArrowQ5Dataset& dataset,
                                                  const Q5Params& params) {
  Stopwatch total_timer;
  ARROW_ASSIGN_OR_RAISE(const ArrowQ5Plan plan,
                        build_arrow_q5_plan(dataset, params));
  ARROW_ASSIGN_OR_RAISE(const auto batches, table_batches(dataset.lineitem));

  const auto lineitem_count =
      static_cast<std::size_t>(dataset.lineitem->num_rows());
  const int requested_threads = std::max(1, params.threads);
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
  result.timing.build_ms = plan.build_ms;
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
  result.timing.total_ms = total_timer.elapsed_ms();
  return result;
}

arrow::Result<Q5Result> execute_q5_arrow_cpu(const ArrowQ5Dataset& dataset,
                                             const Q5Params& params) {
  try {
    return execute_q5_arrow_cpu_impl(dataset, params);
  } catch (const std::bad_alloc&) {
    return arrow::Status::CapacityError("Arrow CPU Q5 allocation failed");
  } catch (const std::length_error& error) {
    return arrow::Status::CapacityError("Arrow CPU Q5 allocation failed: ",
                                        error.what());
  } catch (const std::system_error& error) {
    return arrow::Status::IOError("Arrow CPU Q5 thread creation failed: ",
                                  error.what());
  } catch (const std::exception& error) {
    return arrow::Status::UnknownError("Arrow CPU Q5 failed: ", error.what());
  }
}

}  // namespace memq5

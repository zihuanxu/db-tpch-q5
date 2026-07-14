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
#include <unordered_set>
#include <utility>
#include <vector>

#include <arrow/api.h>

#include "common/timer.hpp"
#include "engine/arrow_q5_plan.hpp"
#include "cpu/q5_arrow_scan.hpp"

namespace memq5 {
namespace {

constexpr std::size_t kMaxDirectKey = 100000000;

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

arrow::Result<Q5Result> execute_q5_arrow_cpu_impl(const ArrowQ5Dataset& dataset,
                                                  const Q5Params& params) {
  Stopwatch total_timer;
  ARROW_ASSIGN_OR_RAISE(const ArrowQ5Plan plan,
                        build_arrow_q5_plan(dataset, params));
  ARROW_ASSIGN_OR_RAISE(Q5Result result,
                        scan_q5_arrow_lineitem(dataset.lineitem, plan,
                                               params.threads));
  result.timing.build_ms = plan.build_ms;
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

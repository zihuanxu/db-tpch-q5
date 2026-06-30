#include "engine/q5_plan.hpp"

#include <algorithm>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

#include "common/timer.hpp"

namespace memq5 {
namespace {

template <class ColumnT>
int32_t max_key(const ColumnT& column) {
  int32_t max_value = -1;
  for (int64_t i = 0; i < column.size(); ++i) {
    max_value = std::max(max_value, column[static_cast<std::size_t>(i)]);
  }
  return max_value;
}

}  // namespace

bool valid_key(const std::vector<int32_t>& values, int32_t key) {
  return key >= 0 && static_cast<std::size_t>(key) < values.size();
}

Q5PreparedPlan build_q5_plan_cpu(const TpchDatabase& db, const Q5Params& params) {
  Stopwatch build_timer;

  int32_t region_key = -1;
  for (int64_t i = 0; i < db.region.size(); ++i) {
    const auto row = static_cast<std::size_t>(i);
    const std::string& name = db.region.names.value(db.region.r_name_code[row]);
    if (name == params.region_name) {
      region_key = db.region.r_regionkey[row];
      break;
    }
  }
  if (region_key < 0) {
    throw std::runtime_error("region not found: " + params.region_name);
  }

  Q5PreparedPlan plan;
  plan.max_nation_key = max_key(db.nation.n_nationkey);

  std::vector<uint8_t> nation_in_region(
      static_cast<std::size_t>(plan.max_nation_key + 1), 0);
  plan.nation_name_by_key.resize(static_cast<std::size_t>(plan.max_nation_key + 1));

  for (int64_t i = 0; i < db.nation.size(); ++i) {
    const auto row = static_cast<std::size_t>(i);
    const int32_t nation_key = db.nation.n_nationkey[row];
    if (nation_key < 0 || nation_key > plan.max_nation_key) {
      continue;
    }
    plan.nation_name_by_key[static_cast<std::size_t>(nation_key)] =
        db.nation.names.value(db.nation.n_name_code[row]);
    if (db.nation.n_regionkey[row] == region_key) {
      nation_in_region[static_cast<std::size_t>(nation_key)] = 1;
    }
  }

  const int32_t max_supplier = max_key(db.supplier.s_suppkey);
  plan.supplier_nation_by_key.assign(static_cast<std::size_t>(max_supplier + 1), -1);
  for (int64_t i = 0; i < db.supplier.size(); ++i) {
    const auto row = static_cast<std::size_t>(i);
    const int32_t suppkey = db.supplier.s_suppkey[row];
    const int32_t nation = db.supplier.s_nationkey[row];
    if (valid_key(plan.supplier_nation_by_key, suppkey) && nation >= 0 &&
        static_cast<std::size_t>(nation) < nation_in_region.size() &&
        nation_in_region[static_cast<std::size_t>(nation)] != 0) {
      plan.supplier_nation_by_key[static_cast<std::size_t>(suppkey)] = nation;
    }
  }

  const int32_t max_customer = max_key(db.customer.c_custkey);
  std::vector<int32_t> customer_nation_by_key(
      static_cast<std::size_t>(max_customer + 1), -1);
  for (int64_t i = 0; i < db.customer.size(); ++i) {
    const auto row = static_cast<std::size_t>(i);
    const int32_t custkey = db.customer.c_custkey[row];
    const int32_t nation = db.customer.c_nationkey[row];
    if (valid_key(customer_nation_by_key, custkey) && nation >= 0 &&
        static_cast<std::size_t>(nation) < nation_in_region.size() &&
        nation_in_region[static_cast<std::size_t>(nation)] != 0) {
      customer_nation_by_key[static_cast<std::size_t>(custkey)] = nation;
    }
  }

  const int32_t max_order = max_key(db.orders.o_orderkey);
  plan.order_nation_by_key.assign(static_cast<std::size_t>(max_order + 1), -1);
  for (int64_t i = 0; i < db.orders.size(); ++i) {
    const auto row = static_cast<std::size_t>(i);
    const int32_t orderkey = db.orders.o_orderkey[row];
    const int32_t custkey = db.orders.o_custkey[row];
    const int32_t orderdate = db.orders.o_orderdate[row];
    if (!valid_key(plan.order_nation_by_key, orderkey) ||
        orderdate < params.start_date_days ||
        orderdate >= params.end_date_days || !valid_key(customer_nation_by_key, custkey)) {
      continue;
    }
    plan.order_nation_by_key[static_cast<std::size_t>(orderkey)] =
        customer_nation_by_key[static_cast<std::size_t>(custkey)];
  }

  plan.build_ms = build_timer.elapsed_ms();
  return plan;
}

}  // namespace memq5

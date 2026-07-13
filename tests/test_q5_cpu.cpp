#include <cassert>
#include <limits>
#include <stdexcept>

#include "cpu/q5_cpu.hpp"
#include "engine/q5_params.hpp"
#include "engine/q5_result_io.hpp"
#include "io/tpch_loader.hpp"

int main() {
  const memq5::TpchDatabase db = memq5::load_tpch(MEMQ5_FIXTURE_DIR);
  assert(db.lineitem.l_discount_hundredths.size() == 6);
  assert(db.lineitem.l_discount_hundredths[0] == 10);
  assert(db.lineitem.l_discount_hundredths[2] == 5);

  memq5::Q5Params params;
  params.region_name = "ASIA";
  memq5::set_q5_date(&params, "1994-01-01");

  const memq5::Q5Result result = memq5::execute_q5_cpu(db, params);

  assert(result.rows.size() == 2);
  assert(result.rows[0].nation_name == "JAPAN");
  assert(result.rows[0].revenue_1e4 == 1900000);
  assert(result.rows[1].nation_name == "INDIA");
  assert(result.rows[1].revenue_1e4 == 900000);
  assert(result.counters.input_lineitem_rows == 6);
  assert(result.counters.matched_lineitem_rows == 2);
  assert(result.counters.cpu_input_rows == 6);

  params.threads = 2;
  const memq5::Q5Result parallel = memq5::execute_q5_cpu(db, params);
  assert(parallel.rows.size() == result.rows.size());
  assert(parallel.rows[0].nation_name == result.rows[0].nation_name);
  assert(parallel.rows[0].revenue_1e4 == result.rows[0].revenue_1e4);
  assert(parallel.rows[1].nation_name == result.rows[1].nation_name);
  assert(parallel.rows[1].revenue_1e4 == result.rows[1].revenue_1e4);
  assert(memq5::result_hash_hex(parallel) == memq5::result_hash_hex(result));

  memq5::TpchDatabase overflow_db;
  overflow_db.region.r_regionkey.push_back(0);
  overflow_db.region.r_name_code.push_back(overflow_db.region.names.encode("ASIA"));
  overflow_db.nation.n_nationkey.push_back(0);
  overflow_db.nation.n_name_code.push_back(overflow_db.nation.names.encode("JAPAN"));
  overflow_db.nation.n_regionkey.push_back(0);
  overflow_db.supplier.s_suppkey.push_back(0);
  overflow_db.supplier.s_nationkey.push_back(0);
  overflow_db.customer.c_custkey.push_back(0);
  overflow_db.customer.c_nationkey.push_back(0);
  overflow_db.orders.o_orderkey.push_back(0);
  overflow_db.orders.o_custkey.push_back(0);
  overflow_db.orders.o_orderdate.push_back(params.start_date_days);
  overflow_db.lineitem.l_orderkey.push_back(0);
  overflow_db.lineitem.l_suppkey.push_back(0);
  overflow_db.lineitem.l_extendedprice_cents.push_back(100);
  overflow_db.lineitem.l_discount_hundredths.push_back(0);
  overflow_db.lineitem.l_orderkey.push_back(0);
  overflow_db.lineitem.l_suppkey.push_back(0);
  overflow_db.lineitem.l_extendedprice_cents.push_back(
      std::numeric_limits<int64_t>::max());
  overflow_db.lineitem.l_discount_hundredths.push_back(0);

  params.threads = 2;
  bool saw_overflow = false;
  try {
    static_cast<void>(memq5::execute_q5_cpu(overflow_db, params));
  } catch (const std::overflow_error&) {
    saw_overflow = true;
  }
  assert(saw_overflow);

  return 0;
}

#include <cassert>

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

  params.threads = 2;
  const memq5::Q5Result parallel = memq5::execute_q5_cpu(db, params);
  assert(parallel.rows.size() == result.rows.size());
  assert(parallel.rows[0].nation_name == result.rows[0].nation_name);
  assert(parallel.rows[0].revenue_1e4 == result.rows[0].revenue_1e4);
  assert(parallel.rows[1].nation_name == result.rows[1].nation_name);
  assert(parallel.rows[1].revenue_1e4 == result.rows[1].revenue_1e4);
  assert(memq5::result_hash_hex(parallel) == memq5::result_hash_hex(result));

  return 0;
}

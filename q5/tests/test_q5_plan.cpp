#include <cassert>

#include "engine/q5_params.hpp"
#include "engine/q5_plan.hpp"
#include "io/tpch_loader.hpp"

int main() {
  const memq5::TpchDatabase db = memq5::load_tpch(MEMQ5_FIXTURE_DIR);

  memq5::Q5Params params;
  params.region_name = "ASIA";
  memq5::set_q5_date(&params, "1994-01-01");

  const memq5::Q5PreparedPlan plan = memq5::build_q5_plan_cpu(db, params);

  assert(plan.max_nation_key == 12);
  assert(plan.nation_name_by_key[8] == "INDIA");
  assert(plan.nation_name_by_key[12] == "JAPAN");
  assert(plan.supplier_nation_by_key[1] == 8);
  assert(plan.supplier_nation_by_key[2] == 12);
  assert(plan.supplier_nation_by_key[3] == -1);
  assert(plan.order_nation_by_key[100] == 8);
  assert(plan.order_nation_by_key[101] == 12);
  assert(plan.order_nation_by_key[102] == -1);
  assert(plan.order_nation_by_key[103] == -1);
  assert(plan.order_nation_by_key[104] == 9);

  return 0;
}

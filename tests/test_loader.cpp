#include <cassert>

#include "io/tpch_loader.hpp"

int main() {
  const memq5::TpchDatabase db = memq5::load_tpch(MEMQ5_FIXTURE_DIR);

  assert(db.region.size() == 5);
  assert(db.nation.size() == 4);
  assert(db.supplier.size() == 3);
  assert(db.customer.size() == 4);
  assert(db.orders.size() == 5);
  assert(db.lineitem.size() == 6);

  assert(db.region.names.find("ASIA") >= 0);
  assert(db.nation.names.find("INDIA") >= 0);
  assert(db.nation.names.find("JAPAN") >= 0);

  return 0;
}

#include <cassert>
#include <cstdint>

#include "common/bitmap.hpp"
#include "common/column.hpp"
#include "common/date.hpp"
#include "common/fixed_point.hpp"

int main() {
  memq5::Column<int32_t> column;
  column.push_back(7);
  column.push_back(9);
  assert(column.size() == 2);
  assert(reinterpret_cast<std::uintptr_t>(column.data()) % 64 == 0);
  assert(column.view().size == 2);

  memq5::Bitmap bitmap(10);
  bitmap.set(2);
  bitmap.set(9);
  assert(bitmap.test(2));
  assert(bitmap.test(9));
  assert(bitmap.count_set_bits() == 2);
  bitmap.clear(2);
  assert(!bitmap.test(2));
  assert(bitmap.count_set_bits() == 1);

  const int32_t start = memq5::date_to_days("1994-01-01");
  const int32_t end = memq5::date_to_days(memq5::add_year(memq5::parse_date("1994-01-01")));
  assert(end - start == 365);
  assert(memq5::date_to_days("1970-01-01") == 0);

  assert(memq5::parse_fixed_decimal("123.45", 100) == 12345);
  assert(memq5::parse_fixed_decimal("0.05", 10000) == 500);
  assert(memq5::compute_revenue_cents(10000, 1000) == 9000);
  assert(memq5::format_cents(19000) == "190.00");

  return 0;
}

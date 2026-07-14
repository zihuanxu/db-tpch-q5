#pragma once

#include <cstdint>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>

namespace memq5 {

inline int64_t parse_fixed_decimal(std::string_view value, int64_t scale) {
  if (value.empty() || scale <= 0) {
    throw std::invalid_argument("invalid fixed decimal");
  }

  bool negative = false;
  std::size_t pos = 0;
  if (value[pos] == '-') {
    negative = true;
    ++pos;
  }

  int64_t whole = 0;
  while (pos < value.size() && value[pos] >= '0' && value[pos] <= '9') {
    whole = whole * 10 + (value[pos] - '0');
    ++pos;
  }

  int64_t frac = 0;
  int64_t frac_scale = 1;
  int64_t max_frac_scale = scale;
  if (pos < value.size() && value[pos] == '.') {
    ++pos;
    while (pos < value.size() && value[pos] >= '0' && value[pos] <= '9' &&
           frac_scale < max_frac_scale) {
      frac = frac * 10 + (value[pos] - '0');
      frac_scale *= 10;
      ++pos;
    }
    if (pos < value.size() && value[pos] >= '0' && value[pos] <= '9') {
      throw std::invalid_argument("too many fractional digits");
    }
  }

  if (pos != value.size()) {
    throw std::invalid_argument("invalid fixed decimal characters");
  }

  while (frac_scale < scale) {
    frac *= 10;
    frac_scale *= 10;
  }

  const int64_t result = whole * scale + frac;
  return negative ? -result : result;
}

inline int64_t compute_revenue_1e4(int64_t extendedprice_cents,
                                   int32_t discount_hundredths) {
  int64_t product = 0;
  if (__builtin_mul_overflow(extendedprice_cents,
                             static_cast<int64_t>(100 - discount_hundredths),
                             &product)) {
    throw std::overflow_error("revenue_1e4 overflow");
  }
  return product;
}

inline std::string format_revenue_1e4(int64_t revenue_1e4) {
  int64_t cents = 0;
  if (revenue_1e4 >= 0) {
    if (revenue_1e4 > std::numeric_limits<int64_t>::max() - 50) {
      throw std::overflow_error("formatted revenue overflow");
    }
    cents = (revenue_1e4 + 50) / 100;
  } else {
    if (revenue_1e4 < std::numeric_limits<int64_t>::min() + 50) {
      throw std::overflow_error("formatted revenue overflow");
    }
    cents = (revenue_1e4 - 50) / 100;
  }

  const bool negative = cents < 0;
  if (negative) {
    cents = -cents;
  }
  std::ostringstream out;
  if (negative) {
    out << '-';
  }
  out << (cents / 100) << '.' << std::setw(2) << std::setfill('0')
      << (cents % 100);
  return out.str();
}

}  // namespace memq5

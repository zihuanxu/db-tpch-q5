#pragma once

#include <cstdint>
#include <iomanip>
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
  if (pos < value.size() && value[pos] == '.') {
    ++pos;
    while (pos < value.size() && value[pos] >= '0' && value[pos] <= '9' &&
           frac_scale < scale) {
      frac = frac * 10 + (value[pos] - '0');
      frac_scale *= 10;
      ++pos;
    }
    while (pos < value.size() && value[pos] >= '0' && value[pos] <= '9') {
      ++pos;
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

inline int64_t compute_revenue_cents(int64_t extendedprice_cents,
                                     int32_t discount_basis_points) {
  return (extendedprice_cents * (10000 - discount_basis_points)) / 10000;
}

inline std::string format_cents(int64_t cents) {
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

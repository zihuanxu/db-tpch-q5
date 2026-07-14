#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>

namespace memq5 {

struct CivilDate {
  int year = 1970;
  int month = 1;
  int day = 1;
};

inline bool is_leap_year(int year) {
  return (year % 4 == 0 && year % 100 != 0) || (year % 400 == 0);
}

inline int days_in_month(int year, int month) {
  static const int normal[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  if (month < 1 || month > 12) {
    throw std::invalid_argument("month out of range");
  }
  if (month == 2 && is_leap_year(year)) {
    return 29;
  }
  return normal[month - 1];
}

inline CivilDate parse_date(const std::string& value) {
  if (value.size() != 10 || value[4] != '-' || value[7] != '-') {
    throw std::invalid_argument("date must use YYYY-MM-DD format");
  }
  for (std::size_t index = 0; index < value.size(); ++index) {
    if (index == 4 || index == 7) {
      continue;
    }
    if (value[index] < '0' || value[index] > '9') {
      throw std::invalid_argument("date must contain only YYYY-MM-DD digits");
    }
  }
  CivilDate date;
  date.year = std::stoi(value.substr(0, 4));
  date.month = std::stoi(value.substr(5, 2));
  date.day = std::stoi(value.substr(8, 2));
  if (date.day < 1 || date.day > days_in_month(date.year, date.month)) {
    throw std::invalid_argument("day out of range");
  }
  return date;
}

inline CivilDate add_year(const CivilDate& date) {
  CivilDate out{date.year + 1, date.month, date.day};
  const int max_day = days_in_month(out.year, out.month);
  if (out.day > max_day) {
    out.day = max_day;
  }
  return out;
}

// Howard Hinnant's civil-date algorithm, returning days since 1970-01-01.
inline int32_t date_to_days(const CivilDate& date) {
  int y = date.year;
  const unsigned m = static_cast<unsigned>(date.month);
  const unsigned d = static_cast<unsigned>(date.day);
  y -= m <= 2;
  const int era = (y >= 0 ? y : y - 399) / 400;
  const unsigned yoe = static_cast<unsigned>(y - era * 400);
  const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return static_cast<int32_t>(era * 146097 + static_cast<int>(doe) - 719468);
}

inline int32_t date_to_days(const std::string& value) {
  return date_to_days(parse_date(value));
}

}  // namespace memq5

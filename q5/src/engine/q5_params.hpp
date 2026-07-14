#pragma once

#include <cstdint>
#include <string>

#include "common/date.hpp"

namespace memq5 {

struct Q5Params {
  std::string region_name = "ASIA";
  int32_t start_date_days = date_to_days("1994-01-01");
  int32_t end_date_days = date_to_days(add_year(parse_date("1994-01-01")));
  int threads = 1;
};

inline void set_q5_date(Q5Params* params, const std::string& date_text) {
  const CivilDate start = parse_date(date_text);
  params->start_date_days = date_to_days(start);
  params->end_date_days = date_to_days(add_year(start));
}

}  // namespace memq5

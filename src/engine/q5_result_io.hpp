#pragma once

#include <iosfwd>
#include <string>

#include "engine/q5_result.hpp"

namespace memq5 {

std::string result_hash_hex(const Q5Result& result);

void write_rows_csv(std::ostream& out, const Q5Result& result);
void write_json(std::ostream& out, const Q5Result& result);
void write_benchmark_csv(std::ostream& out, const std::string& engine,
                         const std::string& region, const std::string& date,
                         int threads, const Q5Result& result);

}  // namespace memq5

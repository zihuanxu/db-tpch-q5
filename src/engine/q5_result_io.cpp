#include "engine/q5_result_io.hpp"

#include <cstdint>
#include <iomanip>
#include <ios>
#include <ostream>
#include <sstream>
#include <string>

#include "common/fixed_point.hpp"

namespace memq5 {
namespace {

uint64_t fnv1a_update(uint64_t hash, uint8_t byte) {
  constexpr uint64_t kPrime = 1099511628211ull;
  hash ^= byte;
  hash *= kPrime;
  return hash;
}

uint64_t fnv1a_update(uint64_t hash, const std::string& value) {
  for (unsigned char ch : value) {
    hash = fnv1a_update(hash, ch);
  }
  return fnv1a_update(hash, 0xff);
}

uint64_t fnv1a_update(uint64_t hash, int64_t value) {
  const auto unsigned_value = static_cast<uint64_t>(value);
  for (int shift = 0; shift < 64; shift += 8) {
    hash = fnv1a_update(hash, static_cast<uint8_t>(unsigned_value >> shift));
  }
  return hash;
}

std::string json_escape(const std::string& value) {
  std::ostringstream out;
  for (char ch : value) {
    switch (ch) {
      case '\\':
        out << "\\\\";
        break;
      case '"':
        out << "\\\"";
        break;
      case '\n':
        out << "\\n";
        break;
      case '\r':
        out << "\\r";
        break;
      case '\t':
        out << "\\t";
        break;
      default:
        out << ch;
        break;
    }
  }
  return out.str();
}

}  // namespace

std::string result_hash_hex(const Q5Result& result) {
  uint64_t hash = 14695981039346656037ull;
  for (const Q5ResultRow& row : result.rows) {
    hash = fnv1a_update(hash, row.nation_name);
    hash = fnv1a_update(hash, row.revenue_cents);
  }

  std::ostringstream out;
  out << std::hex << std::setw(16) << std::setfill('0') << hash;
  return out.str();
}

void write_rows_csv(std::ostream& out, const Q5Result& result) {
  out << "nation,revenue_cents,revenue\n";
  for (const auto& row : result.rows) {
    out << row.nation_name << ',' << row.revenue_cents << ','
        << format_cents(row.revenue_cents) << '\n';
  }
  out << "result_hash," << result_hash_hex(result) << '\n';
  out << "timing_build_ms," << result.timing.build_ms << '\n';
  out << "timing_h2d_ms," << result.timing.h2d_ms << '\n';
  out << "timing_kernel_ms," << result.timing.kernel_ms << '\n';
  out << "timing_d2h_ms," << result.timing.d2h_ms << '\n';
  out << "timing_scan_ms," << result.timing.scan_ms << '\n';
  out << "timing_total_ms," << result.timing.total_ms << '\n';
}

void write_json(std::ostream& out, const Q5Result& result) {
  out << "{\n";
  out << "  \"result_hash\": \"" << result_hash_hex(result) << "\",\n";
  out << "  \"rows\": [\n";
  for (std::size_t i = 0; i < result.rows.size(); ++i) {
    const auto& row = result.rows[i];
    out << "    {\"nation\": \"" << json_escape(row.nation_name)
        << "\", \"revenue_cents\": " << row.revenue_cents
        << ", \"revenue\": \"" << format_cents(row.revenue_cents) << "\"}";
    out << (i + 1 == result.rows.size() ? "\n" : ",\n");
  }
  out << "  ],\n";
  out << "  \"timing_ms\": {\n";
  out << "    \"build\": " << result.timing.build_ms << ",\n";
  out << "    \"h2d\": " << result.timing.h2d_ms << ",\n";
  out << "    \"kernel\": " << result.timing.kernel_ms << ",\n";
  out << "    \"d2h\": " << result.timing.d2h_ms << ",\n";
  out << "    \"scan\": " << result.timing.scan_ms << ",\n";
  out << "    \"total\": " << result.timing.total_ms << "\n";
  out << "  }\n";
  out << "}\n";
}

void write_benchmark_csv(std::ostream& out, const std::string& engine,
                         const std::string& region, const std::string& date,
                         int threads, const Q5Result& result) {
  out << "engine,region,date,threads,result_rows,result_hash,build_ms,h2d_ms,kernel_ms,d2h_ms,scan_ms,total_ms\n";
  out << engine << ',' << region << ',' << date << ',' << threads << ','
      << result.rows.size() << ',' << result_hash_hex(result) << ','
      << result.timing.build_ms << ','
      << result.timing.h2d_ms << ',' << result.timing.kernel_ms << ','
      << result.timing.d2h_ms << ',' << result.timing.scan_ms << ','
      << result.timing.total_ms << '\n';
}

}  // namespace memq5

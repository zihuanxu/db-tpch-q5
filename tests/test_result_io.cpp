#include <cassert>
#include <sstream>
#include <string>

#include "engine/q5_result.hpp"
#include "engine/q5_result_io.hpp"

int main() {
  memq5::Q5Result result;
  result.rows.push_back({"JAPAN", 19000});
  result.rows.push_back({"INDIA", 9000});
  result.timing.build_ms = 1.0;
  result.timing.h2d_ms = 0.0;
  result.timing.kernel_ms = 0.0;
  result.timing.d2h_ms = 0.0;
  result.timing.scan_ms = 2.0;
  result.timing.total_ms = 3.0;

  const std::string hash = memq5::result_hash_hex(result);
  assert(hash.size() == 16);
  assert(hash == memq5::result_hash_hex(result));

  std::ostringstream rows;
  memq5::write_rows_csv(rows, result);
  assert(rows.str().find("JAPAN,19000,190.00") != std::string::npos);
  assert(rows.str().find("result_hash," + hash) != std::string::npos);

  std::ostringstream json;
  memq5::write_json(json, result);
  assert(json.str().find("\"result_hash\": \"" + hash + "\"") != std::string::npos);
  assert(json.str().find("\"nation\": \"INDIA\"") != std::string::npos);

  std::ostringstream bench;
  memq5::write_benchmark_csv(bench, "cpu", "ASIA", "1994-01-01", 4, result);
  assert(bench.str().find("engine,region,date,threads,result_rows,result_hash") == 0);
  assert(bench.str().find("cpu,ASIA,1994-01-01,4,2," + hash) != std::string::npos);

  return 0;
}

#include <cassert>
#include <sstream>
#include <string>

#include "engine/q5_result.hpp"
#include "engine/q5_result_io.hpp"

int main() {
  memq5::Q5Result result;
  result.rows.push_back({"JAPAN", 1900000});
  result.rows.push_back({"INDIA", 900000});
  result.timing.build_ms = 1.0;
  result.timing.h2d_ms = 0.0;
  result.timing.kernel_ms = 0.0;
  result.timing.d2h_ms = 0.0;
  result.timing.scan_ms = 2.0;
  result.timing.total_ms = 3.0;
  result.timing.cpu_ms = 1.25;
  result.timing.gpu_ms = 2.5;
  result.timing.overlap_ms = 0.75;
  result.counters.input_lineitem_rows = 6;
  result.counters.matched_lineitem_rows = 2;
  result.counters.cpu_input_rows = 6;

  const std::string hash = memq5::result_hash_hex(result);
  assert(hash.size() == 16);
  assert(hash == memq5::result_hash_hex(result));
  memq5::Q5Result rounded_collision = result;
  rounded_collision.rows[0].revenue_1e4 += 1;
  assert(memq5::result_hash_hex(rounded_collision) != hash);

  std::ostringstream rows;
  memq5::write_rows_csv(rows, result);
  assert(rows.str().find("nation,revenue_1e4,revenue\n") == 0);
  assert(rows.str().find("JAPAN,1900000,190.00") != std::string::npos);
  assert(rows.str().find("result_hash," + hash) != std::string::npos);

  std::ostringstream json;
  memq5::write_json(json, result);
  assert(json.str().find("\"result_hash\": \"" + hash + "\"") != std::string::npos);
  assert(json.str().find("\"nation\": \"INDIA\"") != std::string::npos);
  assert(json.str().find("\"revenue_1e4\": 900000") != std::string::npos);
  assert(json.str().find("\"input_lineitem_rows\": 6") !=
         std::string::npos);
  assert(json.str().find("\"matched_lineitem_rows\": 2") !=
         std::string::npos);
  assert(json.str().find("\"cpu_input_rows\": 6") != std::string::npos);
  assert(json.str().find("\"cpu\": 1.25") != std::string::npos);
  assert(json.str().find("\"gpu\": 2.5") != std::string::npos);
  assert(json.str().find("\"overlap\": 0.75") != std::string::npos);
  assert(json.str().find("revenue_cents") == std::string::npos);

  std::ostringstream bench;
  memq5::write_benchmark_csv(bench, "cpu", "ASIA", "1994-01-01", 4, result);
  assert(bench.str().find("engine,region,date,threads,result_rows,result_hash") == 0);
  assert(bench.str().find("cpu,ASIA,1994-01-01,4,2," + hash) != std::string::npos);
  assert(bench.str().find("input_lineitem_rows,matched_lineitem_rows") !=
         std::string::npos);
  assert(bench.str().find("cpu_ms,gpu_ms,overlap_ms") != std::string::npos);

  return 0;
}

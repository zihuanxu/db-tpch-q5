#include <cassert>
#include <sstream>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "engine/q5_result_io.hpp"
#include "session/q5_session_io.hpp"

namespace {

std::vector<nlohmann::json> parse_jsonl(const std::string& text) {
  std::istringstream input(text);
  std::vector<nlohmann::json> records;
  for (std::string line; std::getline(input, line);) {
    assert(!line.empty());
    records.push_back(nlohmann::json::parse(line));
  }
  return records;
}

memq5::Q5Result tiny_result() {
  memq5::Q5Result result;
  result.rows.push_back({"JAPAN", 1900000});
  result.rows.push_back({"INDIA", 900000});
  result.timing.build_ms = 0.0;
  result.timing.h2d_ms = 0.25;
  result.timing.kernel_ms = 0.5;
  result.timing.d2h_ms = 0.125;
  result.timing.scan_ms = 0.75;
  result.timing.total_ms = 1.25;
  result.timing.cpu_ms = 0.8;
  result.timing.gpu_ms = 0.9;
  result.timing.overlap_ms = 0.45;
  result.counters.input_lineitem_rows = 6;
  result.counters.matched_lineitem_rows = 2;
  result.counters.cpu_input_rows = 3;
  result.counters.gpu_input_rows = 3;
  result.counters.h2d_bytes = 0;
  result.counters.d2h_bytes = 16;
  result.counters.mapped_remote_read_bytes = 32;
  return result;
}

}  // namespace

int main() {
  constexpr char kSessionId[] = "123e4567-e89b-42d3-a456-426614174000";
  std::ostringstream output;
  memq5::Q5SessionJsonlWriter writer(output, kSessionId);

  memq5::Q5SessionSetupRecord setup_record;
  setup_record.engine = "hybrid-arrow";
  setup_record.dataset = "/tmp/tiny-arrow";
  setup_record.region = "ASIA";
  setup_record.date = "1994-01-01";
  setup_record.threads = 2;
  setup_record.warmup = 1;
  setup_record.repeat = 2;
  setup_record.dataset_load_ms = 1.5;
  setup_record.setup.plan_build_ms = 2.5;
  setup_record.setup.host_staging_ms = 3.5;
  setup_record.setup.allocation_ms = 4.5;
  setup_record.setup.initial_h2d_ms = 5.5;
  setup_record.setup.total_ms = 6.5;
  setup_record.setup.resident_host_bytes = 101;
  setup_record.setup.resident_gpu_bytes = 202;
  setup_record.setup.resident_pinned_bytes = 303;
  setup_record.selected_cpu_ratio = 0.5;
  writer.WriteSetup(setup_record);

  const memq5::Q5Result result = tiny_result();
  std::string expected_hash;
  assert(memq5::check_session_result_hash(result, &expected_hash).ok());
  assert(expected_hash == memq5::result_hash_hex(result));
  for (int request_index = 0; request_index < 3; ++request_index) {
    memq5::Q5SessionRequestRecord request_record;
    request_record.request_index = request_index;
    request_record.is_warmup = request_index == 0;
    request_record.selected_cpu_ratio = 0.5;
    request_record.result = result;
    writer.WriteRequest(request_record);
    assert(memq5::check_session_result_hash(result, &expected_hash).ok());
  }

  const std::vector<nlohmann::json> records = parse_jsonl(output.str());
  assert(records.size() == 4);
  const auto& setup = records.front();
  assert(setup.at("record_type") == "session_setup");
  assert(setup.at("session_id") == kSessionId);
  assert(setup.at("lifecycle") == "resident");
  assert(setup.at("status") == "ok");
  assert(setup.at("engine") == "hybrid-arrow");
  assert(setup.at("dataset") == "/tmp/tiny-arrow");
  assert(setup.at("region") == "ASIA");
  assert(setup.at("date") == "1994-01-01");
  assert(setup.at("threads") == 2);
  assert(setup.at("warmup") == 1);
  assert(setup.at("repeat") == 2);
  assert(setup.at("dataset_load_ms") == 1.5);
  assert(setup.at("session_setup_ms") == 6.5);
  assert(setup.at("plan_build_ms") == 2.5);
  assert(setup.at("host_staging_ms") == 3.5);
  assert(setup.at("allocation_ms") == 4.5);
  assert(setup.at("initial_h2d_ms") == 5.5);
  assert(setup.at("resident_host_bytes") == 101);
  assert(setup.at("resident_gpu_bytes") == 202);
  assert(setup.at("resident_pinned_bytes") == 303);
  assert(setup.at("selected_cpu_ratio") == 0.5);

  for (std::size_t index = 1; index < records.size(); ++index) {
    const auto& request = records[index];
    assert(request.at("record_type") == "request");
    assert(request.at("session_id") == kSessionId);
    assert(request.at("lifecycle") == "resident");
    assert(request.at("status") == "ok");
    assert(request.at("request_index") == static_cast<int>(index - 1));
    assert(request.at("is_warmup") == (index == 1));
    assert(request.at("selected_cpu_ratio") == 0.5);
    assert(request.at("result_rows") == 2);
    assert(request.at("result_hash") == expected_hash);
    assert(request.at("rows").size() == 2);
    assert(request.at("rows").at(0).at("nation") == "JAPAN");
    assert(request.at("rows").at(0).at("revenue_1e4") == 1900000);
    assert(request.at("rows").at(1).at("nation") == "INDIA");
    assert(request.at("rows").at(1).at("revenue_1e4") == 900000);
    assert(request.at("query_total_ms") == 1.25);
    assert(request.at("build_ms") == 0.0);
    assert(request.at("h2d_ms") == 0.25);
    assert(request.at("kernel_ms") == 0.5);
    assert(request.at("d2h_ms") == 0.125);
    assert(request.at("scan_ms") == 0.75);
    assert(request.at("cpu_ms") == 0.8);
    assert(request.at("gpu_ms") == 0.9);
    assert(request.at("overlap_wall_ms") == 0.45);
    assert(!request.contains("overlap_ms"));
    assert(request.at("input_lineitem_rows") == 6);
    assert(request.at("matched_lineitem_rows") == 2);
    assert(request.at("cpu_input_rows") == 3);
    assert(request.at("gpu_input_rows") == 3);
    assert(request.at("h2d_bytes") == 0);
    assert(request.at("d2h_bytes") == 16);
    assert(request.at("mapped_remote_read_bytes") == 32);
  }

  memq5::Q5Result changed = result;
  changed.rows.front().revenue_1e4 += 1;
  const arrow::Status mismatch =
      memq5::check_session_result_hash(changed, &expected_hash);
  assert(mismatch.IsInvalid());
  assert(mismatch.message().find(expected_hash) != std::string::npos);
  assert(mismatch.message().find(memq5::result_hash_hex(changed)) !=
         std::string::npos);

  const std::string generated_id = memq5::generate_session_id();
  assert(generated_id.size() == 36);
  assert(generated_id[8] == '-');
  assert(generated_id[13] == '-');
  assert(generated_id[18] == '-');
  assert(generated_id[23] == '-');

  std::ostringstream error_output;
  memq5::Q5SessionJsonlWriter error_writer(error_output, kSessionId);
  memq5::Q5SessionRequestRecord error_record;
  error_record.status = "error";
  error_record.error_class = "ResultHashMismatch";
  error_record.error_message = mismatch.message();
  error_record.request_index = 3;
  error_record.result = changed;
  error_writer.WriteRequest(error_record);
  const auto error = parse_jsonl(error_output.str()).front();
  assert(error.at("status") == "error");
  assert(error.at("error_class") == "ResultHashMismatch");
  assert(error.at("error_message") == mismatch.message());
  assert(error.at("result_hash") == memq5::result_hash_hex(changed));

  return 0;
}

#include "session/q5_session_io.hpp"

#include <array>
#include <cstdint>
#include <iomanip>
#include <ostream>
#include <random>
#include <sstream>
#include <stdexcept>
#include <utility>

#include <nlohmann/json.hpp>

#include "engine/q5_result_io.hpp"

namespace memq5 {
namespace {

nlohmann::json setup_json(const std::string& session_id,
                          const Q5SessionSetupRecord& record) {
  return {
      {"record_type", "session_setup"},
      {"session_id", session_id},
      {"lifecycle", "resident"},
      {"status", "ok"},
      {"error_class", ""},
      {"engine", record.engine},
      {"dataset", record.dataset},
      {"region", record.region},
      {"date", record.date},
      {"threads", record.threads},
      {"warmup", record.warmup},
      {"repeat", record.repeat},
      {"dataset_load_ms", record.dataset_load_ms},
      {"session_setup_ms", record.setup.total_ms},
      {"plan_build_ms", record.setup.plan_build_ms},
      {"host_staging_ms", record.setup.host_staging_ms},
      {"allocation_ms", record.setup.allocation_ms},
      {"initial_h2d_ms", record.setup.initial_h2d_ms},
      {"tune_ms", record.tune_ms},
      {"resident_host_bytes", record.setup.resident_host_bytes},
      {"resident_gpu_bytes", record.setup.resident_gpu_bytes},
      {"resident_pinned_bytes", record.setup.resident_pinned_bytes},
      {"selected_cpu_ratio", record.selected_cpu_ratio},
      {"predicted_cpu_ratio", record.predicted_cpu_ratio},
  };
}

nlohmann::json request_rows(const Q5Result* result) {
  nlohmann::json rows = nlohmann::json::array();
  if (result == nullptr) {
    return rows;
  }
  for (const Q5ResultRow& row : result->rows) {
    rows.push_back(
        {{"nation", row.nation_name}, {"revenue_1e4", row.revenue_1e4}});
  }
  return rows;
}

nlohmann::json request_json(const std::string& session_id,
                            const Q5SessionRequestRecord& record) {
  const Q5Result empty_result;
  const Q5Result* result = record.result.has_value() ? &*record.result : nullptr;
  const Q5Result& values = result == nullptr ? empty_result : *result;
  return {
      {"record_type", "request"},
      {"session_id", session_id},
      {"lifecycle", "resident"},
      {"status", record.status},
      {"error_class", record.error_class},
      {"error_message", record.error_message},
      {"request_index", record.request_index},
      {"is_warmup", record.is_warmup},
      {"selected_cpu_ratio", record.selected_cpu_ratio},
      {"result_rows", result == nullptr ? 0 : result->rows.size()},
      {"result_hash", result == nullptr ? "" : result_hash_hex(*result)},
      {"rows", request_rows(result)},
      {"build_ms", values.timing.build_ms},
      {"h2d_ms", values.timing.h2d_ms},
      {"kernel_ms", values.timing.kernel_ms},
      {"d2h_ms", values.timing.d2h_ms},
      {"scan_ms", values.timing.scan_ms},
      {"query_total_ms", values.timing.total_ms},
      {"cpu_ms", values.timing.cpu_ms},
      {"gpu_ms", values.timing.gpu_ms},
      {"overlap_ms", values.timing.overlap_ms},
      {"input_lineitem_rows", values.counters.input_lineitem_rows},
      {"matched_lineitem_rows", values.counters.matched_lineitem_rows},
      {"cpu_input_rows", values.counters.cpu_input_rows},
      {"gpu_input_rows", values.counters.gpu_input_rows},
      {"h2d_bytes", values.counters.h2d_bytes},
      {"d2h_bytes", values.counters.d2h_bytes},
      {"mapped_remote_read_bytes",
       values.counters.mapped_remote_read_bytes},
  };
}

void write_json_line(std::ostream& output, const nlohmann::json& record) {
  output << record.dump() << '\n';
}

}  // namespace

std::string generate_session_id() {
  std::random_device random_device;
  std::mt19937_64 generator(random_device());
  std::uniform_int_distribution<unsigned int> byte_distribution(0, 255);
  std::array<uint8_t, 16> bytes{};
  for (uint8_t& byte : bytes) {
    byte = static_cast<uint8_t>(byte_distribution(generator));
  }
  bytes[6] = static_cast<uint8_t>((bytes[6] & 0x0fU) | 0x40U);
  bytes[8] = static_cast<uint8_t>((bytes[8] & 0x3fU) | 0x80U);

  std::ostringstream output;
  output << std::hex << std::setfill('0');
  for (std::size_t index = 0; index < bytes.size(); ++index) {
    if (index == 4 || index == 6 || index == 8 || index == 10) {
      output << '-';
    }
    output << std::setw(2) << static_cast<unsigned int>(bytes[index]);
  }
  return output.str();
}

arrow::Status check_session_result_hash(const Q5Result& result,
                                        std::string* expected_hash) {
  if (expected_hash == nullptr) {
    return arrow::Status::Invalid("expected result hash pointer is null");
  }
  const std::string actual_hash = result_hash_hex(result);
  if (expected_hash->empty()) {
    *expected_hash = actual_hash;
    return arrow::Status::OK();
  }
  if (*expected_hash != actual_hash) {
    return arrow::Status::Invalid("resident result hash mismatch: expected ",
                                  *expected_hash, ", got ", actual_hash);
  }
  return arrow::Status::OK();
}

Q5SessionJsonlWriter::Q5SessionJsonlWriter(std::ostream& output,
                                           std::string session_id)
    : output_(output), session_id_(std::move(session_id)) {
  if (session_id_.empty()) {
    throw std::invalid_argument("session ID must not be empty");
  }
}

void Q5SessionJsonlWriter::WriteSetup(const Q5SessionSetupRecord& record) {
  write_json_line(output_, setup_json(session_id_, record));
}

void Q5SessionJsonlWriter::WriteRequest(
    const Q5SessionRequestRecord& record) {
  write_json_line(output_, request_json(session_id_, record));
}

const std::string& Q5SessionJsonlWriter::session_id() const {
  return session_id_;
}

}  // namespace memq5

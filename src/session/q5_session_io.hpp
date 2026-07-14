#pragma once

#include <iosfwd>
#include <string>

#include <arrow/status.h>

#include "engine/q5_result.hpp"
#include "session/q5_session_record.hpp"

namespace memq5 {

std::string generate_session_id();

arrow::Status check_session_result_hash(const Q5Result& result,
                                        std::string* expected_hash);

class Q5SessionJsonlWriter {
 public:
  Q5SessionJsonlWriter(std::ostream& output, std::string session_id);

  void WriteSetup(const Q5SessionSetupRecord& record);
  void WriteRequest(const Q5SessionRequestRecord& record);

  const std::string& session_id() const;

 private:
  std::ostream& output_;
  const std::string session_id_;
};

}  // namespace memq5

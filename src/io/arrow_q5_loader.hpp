#pragma once

#include <cstdint>
#include <filesystem>
#include <map>
#include <memory>
#include <string>

#include <arrow/result.h>
#include <arrow/table.h>

namespace memq5 {

struct ArrowQ5TableMetadata {
  std::string file_name;
  int64_t rows = 0;
  int64_t bytes = 0;
  int32_t record_batches = 0;
  std::string sha256;
};

struct ArrowQ5Dataset {
  std::map<std::string, std::shared_ptr<arrow::Table>> tables;
  std::map<std::string, ArrowQ5TableMetadata> metadata;
};

const std::map<std::string, std::shared_ptr<arrow::Schema>>&
expected_q5_arrow_schemas();

arrow::Status validate_q5_arrow_table(const std::string& table_name,
                                      const arrow::Table& table);

arrow::Result<ArrowQ5Dataset> load_arrow_q5_dataset(
    const std::filesystem::path& dataset_dir, bool verify_checksums = true);

}  // namespace memq5

#include "io/arrow_q5_loader.hpp"

#include <arrow/api.h>
#include <nlohmann/json.hpp>

#include <cassert>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <functional>
#include <limits>
#include <map>
#include <sstream>
#include <string>

namespace {

using Json = nlohmann::json;

std::filesystem::path copy_fixture(const std::string& label) {
  const auto suffix = std::chrono::steady_clock::now().time_since_epoch().count();
  const auto destination = std::filesystem::temp_directory_path() /
                           ("memq5_arrow_loader_" + label + "_" +
                            std::to_string(suffix));
  std::filesystem::create_directories(destination);
  for (const auto& entry :
       std::filesystem::directory_iterator(MEMQ5_ARROW_FIXTURE_DIR)) {
    std::filesystem::copy_file(
        entry.path(), destination / entry.path().filename(),
        std::filesystem::copy_options::overwrite_existing);
  }
  return destination;
}

Json read_json(const std::filesystem::path& path) {
  std::ifstream input(path);
  assert(input);
  Json value;
  input >> value;
  return value;
}

void write_json(const std::filesystem::path& path, const Json& value) {
  std::ofstream output(path);
  assert(output);
  output << value.dump(2) << '\n';
  assert(output);
}

std::filesystem::path mutate_manifest(
    const std::string& label, const std::function<void(Json&)>& mutate) {
  const auto destination = copy_fixture(label);
  const auto manifest_path = destination / "manifest.json";
  Json manifest = read_json(manifest_path);
  mutate(manifest);
  write_json(manifest_path, manifest);
  return destination;
}

void expect_load_failure(const std::filesystem::path& dataset_dir,
                         const std::string& message,
                         bool verify_checksums = true) {
  const auto loaded =
      memq5::load_arrow_q5_dataset(dataset_dir, verify_checksums);
  assert(!loaded.ok());
  assert(loaded.status().ToString().find(message) != std::string::npos);
  std::filesystem::remove_all(dataset_dir);
}

std::filesystem::path make_tampered_fixture() {
  const auto destination = copy_fixture("tampered");

  std::fstream region(destination / "region.arrow",
                      std::ios::in | std::ios::out | std::ios::binary);
  assert(region);
  char byte = 0;
  region.read(&byte, 1);
  assert(region.gcount() == 1);
  byte ^= 0x01;
  region.seekp(0);
  region.write(&byte, 1);
  assert(region);
  return destination;
}

void test_table_validation(const memq5::ArrowQ5Dataset& dataset) {
  const auto region = dataset.tables.at("region");

  arrow::Int64Builder wrong_type_builder;
  for (int64_t row = 0; row < region->num_rows(); ++row) {
    const auto append_status = wrong_type_builder.Append(row);
    assert(append_status.ok());
  }
  std::shared_ptr<arrow::Array> wrong_type_values;
  const auto wrong_type_finish = wrong_type_builder.Finish(&wrong_type_values);
  assert(wrong_type_finish.ok());
  const auto wrong_type = region->SetColumn(
      0, arrow::field("r_regionkey", arrow::int64(), false),
      std::make_shared<arrow::ChunkedArray>(wrong_type_values));
  assert(wrong_type.ok());
  const auto wrong_schema_status =
      memq5::validate_q5_arrow_table("region", **wrong_type);
  assert(!wrong_schema_status.ok());
  assert(wrong_schema_status.ToString().find("schema mismatch") !=
         std::string::npos);

  arrow::Int32Builder null_builder;
  const auto append_null_status = null_builder.AppendNull();
  assert(append_null_status.ok());
  for (int64_t row = 1; row < region->num_rows(); ++row) {
    const auto append_status = null_builder.Append(static_cast<int32_t>(row));
    assert(append_status.ok());
  }
  std::shared_ptr<arrow::Array> null_values;
  const auto null_finish = null_builder.Finish(&null_values);
  assert(null_finish.ok());
  const auto with_null = region->SetColumn(
      0, region->schema()->field(0),
      std::make_shared<arrow::ChunkedArray>(null_values));
  assert(with_null.ok());
  const auto null_status = memq5::validate_q5_arrow_table("region", **with_null);
  assert(!null_status.ok());
  assert(null_status.ToString().find("null value") != std::string::npos);
}

}  // namespace

int main() {
  auto loaded = memq5::load_arrow_q5_dataset(MEMQ5_ARROW_FIXTURE_DIR);
  assert(loaded.ok());
  auto dataset = loaded.MoveValueUnsafe();

  const std::map<std::string, int64_t> expected_rows = {
      {"region", 5},   {"nation", 4}, {"supplier", 3},
      {"customer", 4}, {"orders", 5}, {"lineitem", 6},
  };
  assert(dataset.tables.size() == expected_rows.size());
  assert(dataset.metadata.size() == expected_rows.size());
  for (const auto& [table_name, row_count] : expected_rows) {
    const auto table = dataset.tables.at(table_name);
    assert(table->num_rows() == row_count);
    assert(table->schema()->Equals(
        *memq5::expected_q5_arrow_schemas().at(table_name), false));
    assert(dataset.metadata.at(table_name).rows == row_count);
  }
  assert(dataset.metadata.at("lineitem").record_batches == 3);
  test_table_validation(dataset);

  auto missing_manifest =
      memq5::load_arrow_q5_dataset(MEMQ5_TBL_FIXTURE_DIR);
  assert(!missing_manifest.ok());
  assert(missing_manifest.status().ToString().find("missing Arrow dataset manifest") !=
         std::string::npos);

  expect_load_failure(make_tampered_fixture(), "SHA-256 mismatch");

  expect_load_failure(
      mutate_manifest("float_version", [](Json& manifest) {
        manifest["format_version"] = 1.5;
      }),
      "format_version must be an integer");
  expect_load_failure(
      mutate_manifest("float_rows", [](Json& manifest) {
        manifest["tables"]["region"]["rows"] = 5.5;
      }),
      "rows must be an integer");
  expect_load_failure(
      mutate_manifest("wide_version", [](Json& manifest) {
        manifest["format_version"] = std::uint64_t{4294967297ULL};
      }),
      "format_version is outside the supported integer range");
  expect_load_failure(
      mutate_manifest("wide_batches", [](Json& manifest) {
        manifest["tables"]["lineitem"]["record_batches"] =
            std::numeric_limits<std::uint64_t>::max();
      }),
      "record_batches is outside the supported integer range");
  expect_load_failure(
      mutate_manifest("bad_checksum", [](Json& manifest) {
        manifest["tables"]["region"]["sha256"] = "not-a-sha256";
      }),
      "sha256 must contain 64 hexadecimal characters", false);
  expect_load_failure(
      mutate_manifest("unsafe_file", [](Json& manifest) {
        manifest["tables"]["region"]["file"] = "../region.arrow";
      }),
      "unsafe or unexpected file name");
  expect_load_failure(
      mutate_manifest("missing_table", [](Json& manifest) {
        manifest["tables"].erase("supplier");
      }),
      "exactly 6 Q5 tables");
  expect_load_failure(
      mutate_manifest("bad_bytes", [](Json& manifest) {
        manifest["tables"]["orders"]["bytes"] = 1;
      }),
      "byte size mismatch");
  expect_load_failure(
      mutate_manifest("bad_rows", [](Json& manifest) {
        manifest["tables"]["orders"]["rows"] = 6;
      }),
      "row count mismatch");
  expect_load_failure(
      mutate_manifest("bad_batches", [](Json& manifest) {
        manifest["tables"]["orders"]["record_batches"] = 4;
      }),
      "record batch mismatch");
  expect_load_failure(
      mutate_manifest("bad_schema", [](Json& manifest) {
        manifest["tables"]["orders"]["schema"] = "wrong";
      }),
      "manifest schema mismatch");

  const auto malformed = copy_fixture("malformed");
  {
    std::ofstream output(malformed / "manifest.json");
    output << "{";
  }
  expect_load_failure(malformed, "invalid Arrow dataset manifest");

  const auto duplicate = copy_fixture("duplicate");
  {
    std::ifstream input(duplicate / "manifest.json");
    assert(input);
    std::ostringstream buffer;
    buffer << input.rdbuf();
    std::string text = buffer.str();
    const std::string original = "\"format_version\": 1,";
    const auto position = text.find(original);
    assert(position != std::string::npos);
    text.insert(position + original.size(), "\n  \"format_version\": 1,");
    std::ofstream output(duplicate / "manifest.json");
    output << text;
  }
  expect_load_failure(duplicate, "duplicate object key: format_version");
  return 0;
}

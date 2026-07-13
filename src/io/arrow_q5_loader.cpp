#include "io/arrow_q5_loader.hpp"

#include <array>
#include <cctype>
#include <fstream>
#include <iomanip>
#include <limits>
#include <memory>
#include <sstream>
#include <system_error>
#include <type_traits>
#include <unordered_set>
#include <vector>

#include <arrow/io/file.h>
#include <arrow/ipc/reader.h>
#include <nlohmann/json.hpp>
#include <openssl/crypto.h>
#include <openssl/evp.h>

namespace memq5 {
namespace {

using Json = nlohmann::json;

template <typename T>
arrow::Result<T> manifest_value(const Json& object, const char* key,
                                const std::string& context) {
  if (!object.contains(key)) {
    return arrow::Status::Invalid(context, " is missing field ", key);
  }
  const Json& value = object.at(key);
  if constexpr (std::is_integral_v<T>) {
    if (!value.is_number_integer()) {
      return arrow::Status::Invalid(context, " field ", key,
                                    " must be an integer");
    }
    bool in_range = true;
    if (value.is_number_unsigned()) {
      const auto raw = value.get<std::uint64_t>();
      in_range = raw <= static_cast<std::uint64_t>(
                            std::numeric_limits<T>::max());
    } else {
      const auto raw = value.get<std::int64_t>();
      in_range = raw >= static_cast<std::int64_t>(
                            std::numeric_limits<T>::min()) &&
                 raw <= static_cast<std::int64_t>(
                            std::numeric_limits<T>::max());
    }
    if (!in_range) {
      return arrow::Status::Invalid(context, " field ", key,
                                    " is outside the supported integer range");
    }
  } else if constexpr (std::is_same_v<T, std::string>) {
    if (!value.is_string()) {
      return arrow::Status::Invalid(context, " field ", key,
                                    " must be a string");
    }
  }
  try {
    return value.get<T>();
  } catch (const Json::exception& error) {
    return arrow::Status::Invalid(context, " has invalid field ", key, ": ",
                                  error.what());
  }
}

arrow::Result<std::string> normalized_sha256(const std::string& value,
                                             const std::string& context) {
  if (value.size() != 64) {
    return arrow::Status::Invalid(context,
                                  " field sha256 must contain 64 hexadecimal "
                                  "characters");
  }
  std::string normalized;
  normalized.reserve(value.size());
  for (const unsigned char character : value) {
    if (std::isxdigit(character) == 0) {
      return arrow::Status::Invalid(context,
                                    " field sha256 must contain 64 hexadecimal "
                                    "characters");
    }
    normalized.push_back(
        static_cast<char>(std::tolower(static_cast<unsigned char>(character))));
  }
  return normalized;
}

arrow::Result<std::string> sha256_file(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    return arrow::Status::IOError("cannot open file for SHA-256: ", path.string());
  }

  using DigestContext = std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)>;
  DigestContext context(EVP_MD_CTX_new(), &EVP_MD_CTX_free);
  if (!context || EVP_DigestInit_ex(context.get(), EVP_sha256(), nullptr) != 1) {
    return arrow::Status::IOError("cannot initialize SHA-256");
  }

  std::array<char, 1 << 20> buffer{};
  while (input) {
    input.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
    const std::streamsize count = input.gcount();
    if (count > 0 && EVP_DigestUpdate(context.get(), buffer.data(),
                                      static_cast<std::size_t>(count)) != 1) {
      return arrow::Status::IOError("cannot update SHA-256 for ", path.string());
    }
  }
  if (!input.eof()) {
    return arrow::Status::IOError("cannot read file for SHA-256: ", path.string());
  }

  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int digest_size = 0;
  if (EVP_DigestFinal_ex(context.get(), digest.data(), &digest_size) != 1) {
    return arrow::Status::IOError("cannot finish SHA-256 for ", path.string());
  }

  std::ostringstream output;
  output << std::hex << std::setfill('0');
  for (unsigned int index = 0; index < digest_size; ++index) {
    output << std::setw(2) << static_cast<unsigned int>(digest[index]);
  }
  return output.str();
}

arrow::Result<Json> read_manifest(const std::filesystem::path& path) {
  std::ifstream input(path);
  if (!input) {
    return arrow::Status::IOError("missing Arrow dataset manifest: ", path.string());
  }

  try {
    bool duplicate_found = false;
    std::string duplicate_key;
    std::vector<std::unordered_set<std::string>> object_key_stack;
    const auto callback = [&](int, Json::parse_event_t event,
                              Json& parsed) {
      if (event == Json::parse_event_t::object_start) {
        object_key_stack.emplace_back();
      } else if (event == Json::parse_event_t::key) {
        if (object_key_stack.empty()) {
          return false;
        }
        const std::string key = parsed.get<std::string>();
        if (!object_key_stack.back().insert(key).second && !duplicate_found) {
          duplicate_found = true;
          duplicate_key = key;
        }
      } else if (event == Json::parse_event_t::object_end) {
        object_key_stack.pop_back();
      }
      return true;
    };
    Json manifest = Json::parse(input, callback, true);
    if (duplicate_found) {
      return arrow::Status::Invalid("duplicate object key: ", duplicate_key);
    }
    return manifest;
  } catch (const Json::exception& error) {
    return arrow::Status::Invalid("invalid Arrow dataset manifest: ", error.what());
  }
}

arrow::Status validate_required_columns(const std::string& table_name,
                                        const arrow::Table& table) {
  for (int index = 0; index < table.num_columns(); ++index) {
    const auto& field = table.schema()->field(index);
    if (!field->nullable() && table.column(index)->null_count() != 0) {
      return arrow::Status::Invalid("null value in required column ", table_name,
                                    ".", field->name());
    }
  }
  return arrow::Status::OK();
}

}  // namespace

const std::map<std::string, std::shared_ptr<arrow::Schema>>&
expected_q5_arrow_schemas() {
  static const std::map<std::string, std::shared_ptr<arrow::Schema>> schemas = {
      {"region",
       arrow::schema({arrow::field("r_regionkey", arrow::int32(), false),
                      arrow::field("r_name",
                                   arrow::dictionary(arrow::int32(), arrow::utf8()),
                                   false)})},
      {"nation",
       arrow::schema({arrow::field("n_nationkey", arrow::int32(), false),
                      arrow::field("n_name",
                                   arrow::dictionary(arrow::int32(), arrow::utf8()),
                                   false),
                      arrow::field("n_regionkey", arrow::int32(), false)})},
      {"supplier",
       arrow::schema({arrow::field("s_suppkey", arrow::int32(), false),
                      arrow::field("s_nationkey", arrow::int32(), false)})},
      {"customer",
       arrow::schema({arrow::field("c_custkey", arrow::int32(), false),
                      arrow::field("c_nationkey", arrow::int32(), false)})},
      {"orders",
       arrow::schema({arrow::field("o_orderkey", arrow::int32(), false),
                      arrow::field("o_custkey", arrow::int32(), false),
                      arrow::field("o_orderdate", arrow::date32(), false)})},
      {"lineitem",
       arrow::schema({arrow::field("l_orderkey", arrow::int32(), false),
                      arrow::field("l_suppkey", arrow::int32(), false),
                      arrow::field("l_extendedprice", arrow::decimal128(15, 2),
                                   false),
                      arrow::field("l_discount", arrow::decimal128(15, 2),
                                   false)})},
  };
  return schemas;
}

arrow::Status validate_q5_arrow_table(const std::string& table_name,
                                      const arrow::Table& table) {
  const auto& schemas = expected_q5_arrow_schemas();
  const auto expected = schemas.find(table_name);
  if (expected == schemas.end()) {
    return arrow::Status::Invalid("unknown Q5 table: ", table_name);
  }
  if (!table.schema()->Equals(*expected->second, false)) {
    return arrow::Status::Invalid("schema mismatch for ", table_name,
                                  ": expected ", expected->second->ToString(),
                                  ", got ", table.schema()->ToString());
  }
  return validate_required_columns(table_name, table);
}

arrow::Result<ArrowQ5Dataset> load_arrow_q5_dataset(
    const std::filesystem::path& dataset_dir, bool verify_checksums) {
  ARROW_ASSIGN_OR_RAISE(const Json manifest,
                        read_manifest(dataset_dir / "manifest.json"));
  if (!manifest.is_object()) {
    return arrow::Status::Invalid("Arrow dataset manifest must be an object");
  }
  ARROW_ASSIGN_OR_RAISE(const int format_version,
                        manifest_value<int>(manifest, "format_version", "manifest"));
  if (format_version != 1) {
    return arrow::Status::Invalid("unsupported manifest format_version: ",
                                  format_version);
  }
  if (!manifest.contains("tables") || !manifest.at("tables").is_object()) {
    return arrow::Status::Invalid("manifest tables must be an object");
  }

  const Json& table_entries = manifest.at("tables");
  const auto& schemas = expected_q5_arrow_schemas();
  if (table_entries.size() != schemas.size()) {
    return arrow::Status::Invalid("manifest must contain exactly ", schemas.size(),
                                  " Q5 tables");
  }

  ArrowQ5Dataset dataset;
  for (const auto& [table_name, expected_schema] : schemas) {
    if (!table_entries.contains(table_name) ||
        !table_entries.at(table_name).is_object()) {
      return arrow::Status::Invalid("missing table metadata: ", table_name);
    }
    const Json& entry = table_entries.at(table_name);
    const std::string context = "table " + table_name;

    ARROW_ASSIGN_OR_RAISE(const std::string file_name,
                          manifest_value<std::string>(entry, "file", context));
    ARROW_ASSIGN_OR_RAISE(const int64_t rows,
                          manifest_value<int64_t>(entry, "rows", context));
    ARROW_ASSIGN_OR_RAISE(const int64_t bytes,
                          manifest_value<int64_t>(entry, "bytes", context));
    ARROW_ASSIGN_OR_RAISE(
        const int32_t record_batches,
        manifest_value<int32_t>(entry, "record_batches", context));
    ARROW_ASSIGN_OR_RAISE(const std::string schema_text,
                          manifest_value<std::string>(entry, "schema", context));
    ARROW_ASSIGN_OR_RAISE(const std::string expected_hash,
                          manifest_value<std::string>(entry, "sha256", context));
    ARROW_ASSIGN_OR_RAISE(const std::string canonical_hash,
                          normalized_sha256(expected_hash, context));

    if (rows < 0 || bytes < 0 || record_batches < 0) {
      return arrow::Status::Invalid(context,
                                    " rows, bytes, and record_batches must be "
                                    "non-negative");
    }

    const std::filesystem::path relative_path(file_name);
    if (relative_path.is_absolute() || relative_path.filename() != relative_path ||
        file_name != table_name + ".arrow") {
      return arrow::Status::Invalid("unsafe or unexpected file name for ", table_name,
                                    ": ", file_name);
    }
    const std::filesystem::path table_path = dataset_dir / relative_path;

    std::error_code file_error;
    const auto actual_bytes = std::filesystem::file_size(table_path, file_error);
    if (file_error) {
      return arrow::Status::IOError("cannot stat Arrow table ", table_path.string(),
                                    ": ", file_error.message());
    }
    if (actual_bytes != static_cast<std::uintmax_t>(bytes)) {
      return arrow::Status::Invalid("byte size mismatch for ", table_name,
                                    ": expected ", bytes, ", got ", actual_bytes);
    }
    if (verify_checksums) {
      ARROW_ASSIGN_OR_RAISE(const std::string actual_hash,
                            sha256_file(table_path));
      if (CRYPTO_memcmp(actual_hash.data(), canonical_hash.data(),
                        canonical_hash.size()) != 0) {
        return arrow::Status::Invalid("SHA-256 mismatch for ", table_name);
      }
    }

    ARROW_ASSIGN_OR_RAISE(auto input,
                          arrow::io::ReadableFile::Open(table_path.string()));
    ARROW_ASSIGN_OR_RAISE(auto reader,
                          arrow::ipc::RecordBatchFileReader::Open(input));
    ARROW_ASSIGN_OR_RAISE(auto table, reader->ToTable());

    ARROW_RETURN_NOT_OK(validate_q5_arrow_table(table_name, *table));
    if (schema_text != expected_schema->ToString()) {
      return arrow::Status::Invalid("manifest schema mismatch for ", table_name);
    }
    if (table->num_rows() != rows) {
      return arrow::Status::Invalid("row count mismatch for ", table_name,
                                    ": expected ", rows, ", got ",
                                    table->num_rows());
    }
    if (reader->num_record_batches() != record_batches) {
      return arrow::Status::Invalid("record batch mismatch for ", table_name,
                                    ": expected ", record_batches, ", got ",
                                    reader->num_record_batches());
    }
    dataset.tables.emplace(table_name, std::move(table));
    dataset.metadata.emplace(
        table_name,
        ArrowQ5TableMetadata{file_name, rows, bytes, record_batches, canonical_hash});
  }

  return dataset;
}

}  // namespace memq5

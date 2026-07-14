#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>

#include "io/arrow_q5_loader.hpp"

namespace {

struct Options {
  std::filesystem::path dataset_dir;
  bool verify_checksums = true;
};

Options parse_options(int argc, char** argv) {
  Options options;
  for (int index = 1; index < argc; ++index) {
    const std::string argument = argv[index];
    if (argument == "--dataset") {
      if (++index >= argc) {
        throw std::runtime_error("--dataset requires a path");
      }
      options.dataset_dir = argv[index];
    } else if (argument == "--skip-checksums") {
      options.verify_checksums = false;
    } else if (argument == "--help") {
      std::cout << "usage: memq5_arrow_check --dataset <dir> "
                   "[--skip-checksums]\n";
      std::exit(0);
    } else {
      throw std::runtime_error("unknown argument: " + argument);
    }
  }
  if (options.dataset_dir.empty()) {
    throw std::runtime_error("--dataset is required");
  }
  return options;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const Options options = parse_options(argc, argv);
    auto loaded = memq5::load_arrow_q5_dataset(options.dataset_dir,
                                                options.verify_checksums);
    if (!loaded.ok()) {
      std::cerr << loaded.status().ToString() << '\n';
      return 1;
    }

    auto dataset = loaded.MoveValueUnsafe();
    std::cout << "table,rows,record_batches,bytes,sha256\n";
    for (const auto& [table_name, metadata] : dataset.metadata) {
      std::cout << table_name << ',' << metadata.rows << ','
                << metadata.record_batches << ',' << metadata.bytes << ','
                << metadata.sha256 << '\n';
    }
    std::cout << "validated_tables," << dataset.tables.size() << '\n';
    std::cout << "checksums," << (options.verify_checksums ? "verified" : "skipped")
              << '\n';
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "error: " << error.what() << '\n';
    return 1;
  }
}

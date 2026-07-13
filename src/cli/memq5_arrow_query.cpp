#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>

#include "cpu/q5_acero.hpp"
#include "cpu/q5_arrow_cpu.hpp"
#include "engine/q5_params.hpp"
#include "engine/q5_result_io.hpp"
#include "io/arrow_q5_loader.hpp"

#ifdef MEMQ5_HAS_ARROW_CUDA
#include "cuda/q5_arrow_cuda.hpp"
#endif

namespace {

struct Options {
  std::filesystem::path dataset_dir;
  std::string engine = "cpu-specialized";
  std::string region = "ASIA";
  std::string date = "1994-01-01";
  std::string format = "json";
  int threads = 1;
  bool verify_checksums = true;
};

void print_usage(std::ostream& output) {
  output << "usage: memq5_arrow_query --dataset <dir> "
            "--engine cpu-specialized|arrow-acero"
#ifdef MEMQ5_HAS_ARROW_CUDA
            "|gpu-copy|gpu-managed|gpu-mapped"
#endif
            " "
            "[--region ASIA] [--date 1994-01-01] [--threads N] "
            "[--format json|csv|benchmark] [--skip-checksums]\n";
}

int parse_positive_integer(const std::string& text, const std::string& option) {
  std::size_t consumed = 0;
  int value = 0;
  try {
    value = std::stoi(text, &consumed);
  } catch (const std::exception&) {
    throw std::runtime_error(option + " must be a positive integer");
  }
  if (consumed != text.size() || value <= 0) {
    throw std::runtime_error(option + " must be a positive integer");
  }
  return value;
}

Options parse_options(int argc, char** argv) {
  Options options;
  for (int index = 1; index < argc; ++index) {
    const std::string argument = argv[index];
    const auto next_value = [&]() -> std::string {
      if (++index >= argc) {
        throw std::runtime_error(argument + " requires a value");
      }
      return argv[index];
    };
    if (argument == "--dataset") {
      options.dataset_dir = next_value();
    } else if (argument == "--engine") {
      options.engine = next_value();
    } else if (argument == "--region") {
      options.region = next_value();
    } else if (argument == "--date") {
      options.date = next_value();
    } else if (argument == "--threads") {
      options.threads = parse_positive_integer(next_value(), "--threads");
    } else if (argument == "--format") {
      options.format = next_value();
    } else if (argument == "--skip-checksums") {
      options.verify_checksums = false;
    } else if (argument == "--data-dir") {
      throw std::runtime_error(
          "--data-dir is not accepted by Arrow engines; convert the .tbl data "
          "with scripts/prepare_arrow_dataset.py and pass --dataset");
    } else if (argument == "--help") {
      print_usage(std::cout);
      std::exit(0);
    } else {
      throw std::runtime_error("unknown argument: " + argument);
    }
  }
  if (options.dataset_dir.empty()) {
    throw std::runtime_error("--dataset is required");
  }
  bool supported_engine =
      options.engine == "cpu-specialized" || options.engine == "arrow-acero";
#ifdef MEMQ5_HAS_ARROW_CUDA
  supported_engine = supported_engine || options.engine == "gpu-copy" ||
                     options.engine == "gpu-managed" ||
                     options.engine == "gpu-mapped";
#endif
  if (!supported_engine) {
    throw std::runtime_error("unsupported Arrow engine: " + options.engine);
  }
  if (options.format != "json" && options.format != "csv" &&
      options.format != "benchmark") {
    throw std::runtime_error("unsupported output format: " + options.format);
  }
  static_cast<void>(memq5::parse_date(options.date));
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
    const auto dataset = loaded.MoveValueUnsafe();

    memq5::Q5Params params;
    params.region_name = options.region;
    memq5::set_q5_date(&params, options.date);
    params.threads = options.threads;

    arrow::Result<memq5::Q5Result> result =
        arrow::Status::Invalid("unsupported Arrow engine");
    if (options.engine == "cpu-specialized") {
      result = memq5::execute_q5_arrow_cpu(dataset, params);
    } else if (options.engine == "arrow-acero") {
      result = memq5::execute_q5_acero(dataset, params);
    }
#ifdef MEMQ5_HAS_ARROW_CUDA
    else if (options.engine == "gpu-copy") {
      result = memq5::execute_q5_arrow_gpu_copy(dataset, params);
    } else if (options.engine == "gpu-managed") {
      result = memq5::execute_q5_arrow_gpu_managed(dataset, params);
    } else if (options.engine == "gpu-mapped") {
      result = memq5::execute_q5_arrow_gpu_mapped(dataset, params);
    }
#endif
    if (!result.ok()) {
      std::cerr << result.status().ToString() << '\n';
      return 1;
    }

    if (options.format == "json") {
      memq5::write_json(std::cout, *result);
    } else if (options.format == "csv") {
      memq5::write_rows_csv(std::cout, *result);
    } else {
      memq5::write_benchmark_csv(std::cout, options.engine, options.region,
                                 options.date, options.threads, *result);
    }
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "error: " << error.what() << '\n';
    print_usage(std::cerr);
    return 1;
  }
}

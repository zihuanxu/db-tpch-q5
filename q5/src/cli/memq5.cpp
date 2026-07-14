#include <exception>
#include <iostream>
#include <string>

#include "common/fixed_point.hpp"
#include "cpu/q5_cpu.hpp"
#include "engine/q5_params.hpp"
#include "engine/q5_result_io.hpp"
#include "io/tpch_loader.hpp"

#ifdef MEMQ5_HAS_CUDA
#include "cuda/q5_cuda.hpp"
#endif

namespace {

void print_help() {
  std::cout
      << "memq5 --engine cpu --data-dir <path> [--region ASIA] [--date 1994-01-01]\n"
      << "      [--threads 1] [--format rows|json|benchmark]\n"
      << "\n"
      << "Available engines:\n"
      << "  cpu          Handwritten CPU TPC-H Q5 path\n"
#ifdef MEMQ5_HAS_CUDA
      << "  gpu-copy     Handwritten CUDA lineitem scan with explicit copies\n"
      << "  gpu-managed  CUDA managed memory with prefetch\n"
      << "  gpu-mapped   CUDA mapped pinned host memory/UVA-style access\n"
#endif
      ;
}

struct CliOptions {
  std::string engine = "cpu";
  std::string data_dir;
  std::string date_text = "1994-01-01";
  std::string format = "rows";
  memq5::Q5Params params;
};

CliOptions parse_args(int argc, char** argv) {
  CliOptions options;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    auto require_value = [&](const std::string& name) -> std::string {
      if (i + 1 >= argc) {
        throw std::runtime_error(name + " requires a value");
      }
      return argv[++i];
    };

    if (arg == "--help" || arg == "-h") {
      print_help();
      std::exit(0);
    } else if (arg == "--engine") {
      options.engine = require_value(arg);
    } else if (arg == "--data-dir") {
      options.data_dir = require_value(arg);
    } else if (arg == "--region") {
      options.params.region_name = require_value(arg);
    } else if (arg == "--date") {
      options.date_text = require_value(arg);
      memq5::set_q5_date(&options.params, options.date_text);
    } else if (arg == "--threads") {
      options.params.threads = std::stoi(require_value(arg));
      if (options.params.threads < 1) {
        throw std::runtime_error("--threads must be >= 1");
      }
    } else if (arg == "--format") {
      options.format = require_value(arg);
    } else {
      throw std::runtime_error("unknown argument: " + arg);
    }
  }
  if (options.data_dir.empty()) {
    throw std::runtime_error("--data-dir is required");
  }
  return options;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const CliOptions options = parse_args(argc, argv);
    const memq5::TpchDatabase db = memq5::load_tpch(options.data_dir);
    memq5::Q5Result result;
    if (options.engine == "cpu") {
      result = memq5::execute_q5_cpu(db, options.params);
    } else if (options.engine == "gpu-copy") {
#ifdef MEMQ5_HAS_CUDA
      result = memq5::execute_q5_gpu_copy(db, options.params);
#else
      throw std::runtime_error("gpu-copy was not built; reconfigure with "
                               "-DMEMQ5_ENABLE_CUDA=ON");
#endif
    } else if (options.engine == "gpu-managed") {
#ifdef MEMQ5_HAS_CUDA
      result = memq5::execute_q5_gpu_managed(db, options.params);
#else
      throw std::runtime_error("gpu-managed was not built; reconfigure with "
                               "-DMEMQ5_ENABLE_CUDA=ON");
#endif
    } else if (options.engine == "gpu-mapped") {
#ifdef MEMQ5_HAS_CUDA
      result = memq5::execute_q5_gpu_mapped(db, options.params);
#else
      throw std::runtime_error("gpu-mapped was not built; reconfigure with "
                               "-DMEMQ5_ENABLE_CUDA=ON");
#endif
    } else {
      throw std::runtime_error("unknown engine: " + options.engine);
    }

    if (options.format == "rows") {
      memq5::write_rows_csv(std::cout, result);
    } else if (options.format == "json") {
      memq5::write_json(std::cout, result);
    } else if (options.format == "benchmark") {
      memq5::write_benchmark_csv(std::cout, options.engine,
                                  options.params.region_name, options.date_text,
                                  options.params.threads, result);
    } else {
      throw std::runtime_error("unknown format: " + options.format);
    }
    return 0;
  } catch (const std::exception& ex) {
    std::cerr << "error: " << ex.what() << '\n';
    return 1;
  }
}

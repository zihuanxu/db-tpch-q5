#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>

#include <arrow/result.h>

#include "common/timer.hpp"
#include "engine/q5_params.hpp"
#include "engine/q5_result.hpp"
#include "io/arrow_q5_loader.hpp"
#include "session/q5_cpu_session.hpp"
#include "session/q5_session_io.hpp"

#ifdef MEMQ5_HAS_ARROW_CUDA
#include <cuda_runtime.h>

#include "cuda/q5_arrow_cuda.hpp"
#include "hybrid/q5_hybrid.hpp"
#endif

namespace {

struct Options {
  std::filesystem::path dataset_dir;
  std::string engine = "cpu-specialized";
  std::string region = "ASIA";
  std::string date = "1994-01-01";
  int threads = 1;
  double cpu_ratio = 0.5;
  int warmup = 3;
  int repeat = 10;
  bool verify_checksums = true;
};

void print_usage(std::ostream& output) {
  output << "usage: memq5_arrow_session --dataset <dir> "
            "--engine cpu-specialized"
#ifdef MEMQ5_HAS_ARROW_CUDA
            "|gpu-copy|gpu-managed|gpu-mapped|hybrid-arrow"
#endif
            " [--region ASIA] [--date 1994-01-01] [--threads N] "
            "[--cpu-ratio 0.5] [--warmup 3] [--repeat 10] "
            "[--skip-checksums]\n";
}

int parse_integer(const std::string& text, const std::string& option,
                  bool allow_zero) {
  std::size_t consumed = 0;
  int value = 0;
  try {
    value = std::stoi(text, &consumed);
  } catch (const std::exception&) {
    throw std::runtime_error(option +
                             (allow_zero ? " must be a nonnegative integer"
                                         : " must be a positive integer"));
  }
  if (consumed != text.size() || value < (allow_zero ? 0 : 1)) {
    throw std::runtime_error(option +
                             (allow_zero ? " must be a nonnegative integer"
                                         : " must be a positive integer"));
  }
  return value;
}

double parse_ratio(const std::string& text) {
  std::size_t consumed = 0;
  double value = 0.0;
  try {
    value = std::stod(text, &consumed);
  } catch (const std::exception&) {
    throw std::runtime_error("--cpu-ratio must be between 0 and 1");
  }
  if (consumed != text.size() || !std::isfinite(value) || value < 0.0 ||
      value > 1.0) {
    throw std::runtime_error("--cpu-ratio must be between 0 and 1");
  }
  return value;
}

bool is_supported_engine(const std::string& engine) {
  bool supported = engine == "cpu-specialized";
#ifdef MEMQ5_HAS_ARROW_CUDA
  supported = supported || engine == "gpu-copy" || engine == "gpu-managed" ||
              engine == "gpu-mapped" || engine == "hybrid-arrow";
#endif
  return supported;
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
      options.threads = parse_integer(next_value(), "--threads", false);
    } else if (argument == "--cpu-ratio") {
      options.cpu_ratio = parse_ratio(next_value());
    } else if (argument == "--warmup") {
      options.warmup = parse_integer(next_value(), "--warmup", true);
    } else if (argument == "--repeat") {
      options.repeat = parse_integer(next_value(), "--repeat", false);
    } else if (argument == "--skip-checksums") {
      options.verify_checksums = false;
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
  if (!is_supported_engine(options.engine)) {
    throw std::runtime_error("unsupported resident Arrow engine: " +
                             options.engine);
  }
  const memq5::CivilDate start_date = memq5::parse_date(options.date);
  static_cast<void>(memq5::date_to_days(start_date));
  static_cast<void>(memq5::date_to_days(memq5::add_year(start_date)));
  return options;
}

class ResidentQ5Session {
 public:
  virtual ~ResidentQ5Session() = default;
  virtual arrow::Result<memq5::Q5Result> Execute() = 0;
  virtual const memq5::Q5SessionSetup& setup() const = 0;
  virtual double selected_cpu_ratio() const = 0;
};

template <typename Session>
class ResidentQ5SessionAdapter final : public ResidentQ5Session {
 public:
  ResidentQ5SessionAdapter(std::unique_ptr<Session> session,
                           double selected_cpu_ratio)
      : session_(std::move(session)),
        selected_cpu_ratio_(selected_cpu_ratio) {}

  arrow::Result<memq5::Q5Result> Execute() override {
    return session_->Execute();
  }

  const memq5::Q5SessionSetup& setup() const override {
    return session_->setup();
  }

  double selected_cpu_ratio() const override {
    return selected_cpu_ratio_;
  }

 private:
  std::unique_ptr<Session> session_;
  const double selected_cpu_ratio_;
};

template <typename Session>
std::unique_ptr<ResidentQ5Session> adapt_session(
    std::unique_ptr<Session> session, double selected_cpu_ratio) {
  return std::make_unique<ResidentQ5SessionAdapter<Session>>(
      std::move(session), selected_cpu_ratio);
}

arrow::Result<std::unique_ptr<ResidentQ5Session>> make_session(
    const memq5::ArrowQ5Dataset& dataset, const memq5::Q5Params& params,
    const Options& options) {
  if (options.engine == "cpu-specialized") {
    ARROW_ASSIGN_OR_RAISE(auto session,
                          memq5::ArrowCpuQ5Session::Make(dataset, params));
    return adapt_session(std::move(session), 1.0);
  }
#ifdef MEMQ5_HAS_ARROW_CUDA
  if (options.engine == "gpu-copy" || options.engine == "gpu-managed" ||
      options.engine == "gpu-mapped") {
    memq5::ArrowCudaMemoryMode mode = memq5::ArrowCudaMemoryMode::kCopy;
    if (options.engine == "gpu-managed") {
      mode = memq5::ArrowCudaMemoryMode::kManaged;
    } else if (options.engine == "gpu-mapped") {
      mode = memq5::ArrowCudaMemoryMode::kMapped;
    }
    ARROW_ASSIGN_OR_RAISE(
        auto session,
        memq5::ArrowCudaQ5Session::Make(dataset, params, mode));
    return adapt_session(std::move(session), 0.0);
  }
  if (options.engine == "hybrid-arrow") {
    memq5::HybridOptions hybrid_options;
    hybrid_options.cpu_ratio = options.cpu_ratio;
    hybrid_options.cpu_threads = options.threads;
    ARROW_ASSIGN_OR_RAISE(
        auto session,
        memq5::HybridQ5Session::Make(dataset, params, hybrid_options));
    const double selected_cpu_ratio = session->cpu_ratio();
    return adapt_session(std::move(session), selected_cpu_ratio);
  }
#endif
  return arrow::Status::Invalid("unsupported resident Arrow engine: ",
                                options.engine);
}

#ifdef MEMQ5_HAS_ARROW_CUDA
bool requires_cuda(const std::string& engine) {
  return engine == "gpu-copy" || engine == "gpu-managed" ||
         engine == "gpu-mapped" || engine == "hybrid-arrow";
}

int check_cuda_availability(const std::string& engine) {
  if (!requires_cuda(engine)) {
    return 0;
  }
  int device_count = 0;
  const cudaError_t status = cudaGetDeviceCount(&device_count);
  if (status == cudaErrorNoDevice ||
      (status == cudaSuccess && device_count == 0)) {
    std::cerr << "CUDA engine skipped: no CUDA device is available\n";
    return 77;
  }
  if (status != cudaSuccess) {
    std::cerr << "CUDA device discovery failed: "
              << cudaGetErrorString(status) << '\n';
    return 1;
  }
  return 0;
}
#endif

memq5::Q5SessionSetupRecord make_setup_record(
    const Options& options, double dataset_load_ms,
    const ResidentQ5Session& session) {
  memq5::Q5SessionSetupRecord record;
  record.engine = options.engine;
  record.dataset = options.dataset_dir.string();
  record.region = options.region;
  record.date = options.date;
  record.threads = options.threads;
  record.warmup = options.warmup;
  record.repeat = options.repeat;
  record.dataset_load_ms = dataset_load_ms;
  record.setup = session.setup();
  record.selected_cpu_ratio = session.selected_cpu_ratio();
  return record;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const Options options = parse_options(argc, argv);
    const std::string session_id = memq5::generate_session_id();
#ifdef MEMQ5_HAS_ARROW_CUDA
    const int cuda_availability = check_cuda_availability(options.engine);
    if (cuda_availability != 0) {
      return cuda_availability;
    }
#endif

    memq5::Stopwatch dataset_load_timer;
    auto loaded = memq5::load_arrow_q5_dataset(options.dataset_dir,
                                               options.verify_checksums);
    const double dataset_load_ms = dataset_load_timer.elapsed_ms();
    if (!loaded.ok()) {
      std::cerr << loaded.status().ToString() << '\n';
      return 1;
    }
    const auto dataset = loaded.MoveValueUnsafe();

    memq5::Q5Params params;
    params.region_name = options.region;
    memq5::set_q5_date(&params, options.date);
    params.threads = options.threads;
    auto session_result = make_session(dataset, params, options);
    if (!session_result.ok()) {
      std::cerr << session_result.status().ToString() << '\n';
      return 1;
    }
    auto session = session_result.MoveValueUnsafe();

    memq5::Q5SessionJsonlWriter writer(std::cout, session_id);
    writer.WriteSetup(make_setup_record(options, dataset_load_ms, *session));

    std::string expected_hash;
    const int64_t request_count =
        static_cast<int64_t>(options.warmup) + options.repeat;
    for (int64_t request_index = 0; request_index < request_count;
         ++request_index) {
      memq5::Q5SessionRequestRecord request;
      request.request_index = request_index;
      request.is_warmup = request_index < options.warmup;
      request.selected_cpu_ratio = session->selected_cpu_ratio();

      auto result = session->Execute();
      if (!result.ok()) {
        request.status = "error";
        request.error_class = "ExecutionError";
        request.error_message = result.status().ToString();
        writer.WriteRequest(request);
        std::cerr << request.error_message << '\n';
        return 1;
      }
      request.result = result.MoveValueUnsafe();
      const arrow::Status hash_status =
          memq5::check_session_result_hash(*request.result, &expected_hash);
      if (!hash_status.ok()) {
        request.status = "error";
        request.error_class = "ResultHashMismatch";
        request.error_message = hash_status.message();
        writer.WriteRequest(request);
        std::cerr << request.error_message << '\n';
        return 1;
      }
      writer.WriteRequest(request);
    }
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "error: " << error.what() << '\n';
    print_usage(std::cerr);
    return 1;
  }
}

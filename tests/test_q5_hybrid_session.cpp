#include <cuda_runtime.h>

#include <cassert>
#include <cmath>
#include <condition_variable>
#include <future>
#include <iostream>
#include <limits>
#include <memory>
#include <mutex>
#include <vector>

#include "cuda/q5_arrow_cuda.hpp"
#include "engine/q5_params.hpp"
#include "engine/q5_result_io.hpp"
#include "hybrid/q5_hybrid.hpp"
#include "io/arrow_q5_loader.hpp"
#include "session/q5_cpu_session.hpp"

#ifdef NDEBUG
#error "CUDA tests require assertions"
#endif

namespace {

memq5::Q5Params Asia1994Params() {
  memq5::Q5Params params;
  params.region_name = "ASIA";
  memq5::set_q5_date(&params, "1994-01-01");
  params.threads = 2;
  return params;
}

void assert_setup(const memq5::Q5SessionSetup& setup) {
  assert(setup.plan_build_ms >= 0.0);
  assert(setup.host_staging_ms >= 0.0);
  assert(setup.allocation_ms >= 0.0);
  assert(setup.initial_h2d_ms >= 0.0);
  assert(setup.total_ms >= 0.0);
  assert(setup.resident_host_bytes > 0);
  assert(setup.resident_gpu_bytes > 0);
  assert(setup.resident_pinned_bytes == 0);
}

void assert_request(const memq5::Q5Result& result, int64_t cpu_rows,
                    int64_t gpu_rows) {
  assert(memq5::result_hash_hex(result) == "248d10b6ee352953");
  assert(result.counters.input_lineitem_rows == 6);
  assert(result.counters.matched_lineitem_rows == 2);
  assert(result.counters.cpu_input_rows == cpu_rows);
  assert(result.counters.gpu_input_rows == gpu_rows);
  assert(result.counters.cpu_input_rows + result.counters.gpu_input_rows ==
         result.counters.input_lineitem_rows);
  assert(result.counters.h2d_bytes == 0);
  assert(result.timing.build_ms == 0.0);
  assert(result.timing.h2d_ms == 0.0);
  assert(result.timing.cpu_ms >= 0.0);
  assert(result.timing.gpu_ms >= 0.0);
  assert(result.timing.overlap_ms >= 0.0);
}

std::unique_ptr<memq5::HybridQ5Session> MakeSessionAfterDatasetRelease(
    double ratio) {
  const auto dataset =
      memq5::load_arrow_q5_dataset(MEMQ5_ARROW_FIXTURE_DIR).ValueOrDie();
  memq5::HybridOptions options;
  options.cpu_ratio = ratio;
  options.cpu_threads = 2;
  return memq5::HybridQ5Session::Make(dataset, Asia1994Params(), options)
      .ValueOrDie();
}

void assert_ratio_one_combines_cpu_host_bytes(
    const memq5::ArrowQ5Dataset& dataset, const memq5::Q5Params& params) {
  memq5::HybridOptions options;
  options.cpu_ratio = 1.0;
  options.cpu_threads = 2;
  auto hybrid =
      memq5::HybridQ5Session::Make(dataset, params, options).ValueOrDie();
  auto cpu = memq5::ArrowCpuQ5Session::Make(dataset, params).ValueOrDie();

  memq5::ArrowQ5Dataset empty_gpu_dataset = dataset;
  empty_gpu_dataset.lineitem =
      dataset.lineitem->Slice(dataset.lineitem->num_rows(), 0);
  empty_gpu_dataset.tables["lineitem"] = empty_gpu_dataset.lineitem;
  auto gpu = memq5::ArrowCudaQ5Session::Make(
                 empty_gpu_dataset, params, memq5::ArrowCudaMemoryMode::kCopy)
                 .ValueOrDie();

  assert(cpu->setup().resident_host_bytes > 0);
  assert(hybrid->setup().resident_host_bytes ==
         cpu->setup().resident_host_bytes + gpu->setup().resident_host_bytes);
}

void assert_concurrent_execution_is_stable(
    const memq5::ArrowQ5Dataset& dataset, const memq5::Q5Params& params) {
  memq5::HybridOptions options;
  options.cpu_ratio = 0.5;
  options.cpu_threads = 2;
  auto session =
      memq5::HybridQ5Session::Make(dataset, params, options).ValueOrDie();

  constexpr int kConcurrentCalls = 8;
  std::mutex start_mutex;
  std::condition_variable start_condition;
  int ready = 0;
  bool start = false;
  std::vector<std::future<arrow::Result<memq5::Q5Result>>> calls;
  calls.reserve(kConcurrentCalls);
  for (int call = 0; call < kConcurrentCalls; ++call) {
    calls.push_back(std::async(std::launch::async, [&]() {
      {
        std::unique_lock<std::mutex> lock(start_mutex);
        ++ready;
        start_condition.notify_all();
        start_condition.wait(lock, [&]() { return start; });
      }
      return session->Execute();
    }));
  }

  {
    std::unique_lock<std::mutex> lock(start_mutex);
    start_condition.wait(lock, [&]() { return ready == kConcurrentCalls; });
    start = true;
  }
  start_condition.notify_all();

  for (auto& call : calls) {
    assert_request(call.get().ValueOrDie(), 3, 3);
  }
}

}  // namespace

int main() {
  int device_count = 0;
  const cudaError_t status = cudaGetDeviceCount(&device_count);
  if (status == cudaErrorNoDevice ||
      (status == cudaSuccess && device_count == 0)) {
    std::cout << "Skipping resident hybrid CUDA test: "
              << cudaGetErrorString(status) << '\n';
    return 77;
  }
  if (status != cudaSuccess) {
    std::cerr << "CUDA device discovery failed: "
              << cudaGetErrorString(status) << '\n';
    return 1;
  }

  const auto dataset =
      memq5::load_arrow_q5_dataset(MEMQ5_ARROW_FIXTURE_DIR).ValueOrDie();
  const memq5::Q5Params params = Asia1994Params();

  for (const double ratio : {0.0, 0.25, 0.5, 0.75, 1.0}) {
    memq5::HybridOptions options;
    options.cpu_ratio = ratio;
    options.cpu_threads = 2;
    auto session =
        memq5::HybridQ5Session::Make(dataset, params, options).ValueOrDie();
    assert(session->cpu_ratio() == ratio);
    assert_setup(session->setup());

    const auto first = session->Execute().ValueOrDie();
    const auto second = session->Execute().ValueOrDie();
    const int64_t cpu_rows = std::llround(6.0 * ratio);
    const int64_t gpu_rows = 6 - cpu_rows;
    assert_request(first, cpu_rows, gpu_rows);
    assert_request(second, cpu_rows, gpu_rows);
    assert(memq5::result_hash_hex(first) == memq5::result_hash_hex(second));
    assert(first.counters.cpu_input_rows == second.counters.cpu_input_rows);
    assert(first.counters.gpu_input_rows == second.counters.gpu_input_rows);
  }

  for (const double invalid_ratio :
       {-0.01, 1.01, std::numeric_limits<double>::quiet_NaN()}) {
    memq5::HybridOptions options;
    options.cpu_ratio = invalid_ratio;
    const auto invalid = memq5::HybridQ5Session::Make(dataset, params, options);
    assert(!invalid.ok());
    assert(invalid.status().IsInvalid());
  }

  auto detached_session = MakeSessionAfterDatasetRelease(0.5);
  assert_request(detached_session->Execute().ValueOrDie(), 3, 3);
  assert_request(detached_session->Execute().ValueOrDie(), 3, 3);

  assert_ratio_one_combines_cpu_host_bytes(dataset, params);
  assert_concurrent_execution_is_stable(dataset, params);

  return 0;
}

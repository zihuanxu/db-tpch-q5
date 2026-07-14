#include <cuda_runtime.h>

#include <arrow/api.h>

#include <cassert>
#include <condition_variable>
#include <future>
#include <iostream>
#include <mutex>
#include <vector>

#include "cuda/q5_arrow_cuda.hpp"
#include "engine/arrow_q5_plan.hpp"
#include "engine/q5_params.hpp"
#include "engine/q5_result_io.hpp"
#include "io/arrow_q5_loader.hpp"

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

int64_t expected_resident_host_bytes(
    const memq5::ArrowQ5Dataset& dataset, const memq5::Q5Params& params) {
  const auto plan = memq5::build_arrow_q5_plan(dataset, params).ValueOrDie();
  int64_t bytes = dataset.lineitem->num_rows() *
                  static_cast<int64_t>(sizeof(int32_t) * 3 + sizeof(int64_t));
  bytes += static_cast<int64_t>(plan.order_nation_by_key.size() * sizeof(int32_t));
  bytes += static_cast<int64_t>(
      plan.supplier_nation_by_key.size() * sizeof(int32_t));
  for (const auto& name : plan.nation_name_by_key) {
    bytes += static_cast<int64_t>(name.size());
  }
  const std::size_t nation_count =
      plan.max_nation_key < 0 ? 0 : static_cast<std::size_t>(plan.max_nation_key) + 1;
  bytes += static_cast<int64_t>(nation_count * sizeof(uint64_t) +
                                sizeof(uint64_t) + sizeof(int32_t));
  return bytes;
}

void assert_setup_for_mode(const memq5::Q5SessionSetup& setup,
                           memq5::ArrowCudaMemoryMode mode) {
  assert(setup.plan_build_ms >= 0.0);
  assert(setup.host_staging_ms >= 0.0);
  assert(setup.allocation_ms >= 0.0);
  assert(setup.initial_h2d_ms >= 0.0);
  assert(setup.total_ms >= 0.0);

  if (mode == memq5::ArrowCudaMemoryMode::kCopy) {
    assert(setup.resident_gpu_bytes > 0);
    assert(setup.resident_pinned_bytes == 0);
  } else if (mode == memq5::ArrowCudaMemoryMode::kManaged) {
    assert(setup.resident_gpu_bytes > 0);
    assert(setup.resident_pinned_bytes == 0);
  } else {
    assert(setup.resident_gpu_bytes > 0);
    assert(setup.resident_pinned_bytes > 0);
    assert(setup.initial_h2d_ms == 0.0);
  }
}

void assert_concurrent_managed_calls_are_stable(
    const memq5::ArrowQ5Dataset& dataset, const memq5::Q5Params& params) {
  auto session = memq5::ArrowCudaQ5Session::Make(
                     dataset, params, memq5::ArrowCudaMemoryMode::kManaged)
                     .ValueOrDie();

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
    const auto result = call.get().ValueOrDie();
    assert(memq5::result_hash_hex(result) == "248d10b6ee352953");
  }
}

}  // namespace

int main() {
  int device_count = 0;
  const cudaError_t status = cudaGetDeviceCount(&device_count);
  if (status == cudaErrorNoDevice ||
      (status == cudaSuccess && device_count == 0)) {
    std::cout << "Skipping Arrow CUDA session test: "
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
  const int64_t expected_host_bytes =
      expected_resident_host_bytes(dataset, params);

  assert_concurrent_managed_calls_are_stable(dataset, params);

  for (const auto mode : {memq5::ArrowCudaMemoryMode::kCopy,
                          memq5::ArrowCudaMemoryMode::kManaged,
                          memq5::ArrowCudaMemoryMode::kMapped}) {
    auto session =
        memq5::ArrowCudaQ5Session::Make(dataset, params, mode).ValueOrDie();
    const auto first = session->Execute().ValueOrDie();
    const auto second = session->Execute().ValueOrDie();

    assert(memq5::result_hash_hex(first) == "248d10b6ee352953");
    assert(memq5::result_hash_hex(first) == memq5::result_hash_hex(second));
    assert_setup_for_mode(session->setup(), mode);
    assert(session->setup().resident_host_bytes == expected_host_bytes);

    if (mode == memq5::ArrowCudaMemoryMode::kCopy) {
      assert(first.timing.h2d_ms == 0.0);
      assert(second.counters.h2d_bytes == 0);
    } else if (mode == memq5::ArrowCudaMemoryMode::kManaged) {
      assert(first.counters.h2d_bytes == 0);
      assert(second.counters.h2d_bytes == first.counters.d2h_bytes);
      assert(second.timing.h2d_ms >= 0.0);
    }
  }

  return 0;
}

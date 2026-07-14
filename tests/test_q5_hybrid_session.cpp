#include <cuda_runtime.h>

#include <cassert>
#include <cmath>
#include <iostream>

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
  assert(result.timing.h2d_ms == 0.0);
  assert(result.timing.cpu_ms >= 0.0);
  assert(result.timing.gpu_ms >= 0.0);
  assert(result.timing.overlap_ms >= 0.0);
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

  for (const double ratio : {0.25, 0.5, 0.75}) {
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

  return 0;
}

#include <cuda_runtime.h>

#include <cassert>
#include <cmath>
#include <iostream>

#include "cpu/q5_arrow_cpu.hpp"
#include "engine/q5_params.hpp"
#include "engine/q5_result_io.hpp"
#include "hybrid/q5_hybrid.hpp"
#include "io/arrow_q5_loader.hpp"

int main() {
  int device_count = 0;
  const cudaError_t status = cudaGetDeviceCount(&device_count);
  if (status == cudaErrorNoDevice ||
      (status == cudaSuccess && device_count == 0)) {
    std::cout << "Skipping hybrid CUDA runtime test: "
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
  memq5::Q5Params params;
  params.region_name = "ASIA";
  memq5::set_q5_date(&params, "1994-01-01");
  params.threads = 2;
  const auto cpu = memq5::execute_q5_arrow_cpu(dataset, params).ValueOrDie();

  for (const double ratio : {0.25, 0.5, 0.75}) {
    memq5::HybridOptions options;
    options.cpu_ratio = ratio;
    options.cpu_threads = 2;
    const auto hybrid =
        memq5::execute_q5_hybrid(dataset, params, options).ValueOrDie();
    assert(memq5::result_hash_hex(cpu) == memq5::result_hash_hex(hybrid));
    assert(hybrid.counters.input_lineitem_rows == 6);
    assert(hybrid.counters.matched_lineitem_rows == 2);
    assert(hybrid.counters.cpu_input_rows == std::llround(6.0 * ratio));
    assert(hybrid.counters.gpu_input_rows + hybrid.counters.cpu_input_rows == 6);
    assert(hybrid.timing.cpu_ms >= 0.0);
    assert(hybrid.timing.gpu_ms >= 0.0);
    assert(hybrid.timing.overlap_ms >= 0.0);
  }

  memq5::HybridOptions invalid;
  invalid.cpu_ratio = 1.1;
  assert(!memq5::execute_q5_hybrid(dataset, params, invalid).ok());
  return 0;
}

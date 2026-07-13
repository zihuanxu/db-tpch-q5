#include <cuda_runtime.h>

#include <cassert>
#include <iostream>

#include "cpu/q5_cpu.hpp"
#include "cuda/q5_cuda.hpp"
#include "engine/q5_params.hpp"
#include "engine/q5_result_io.hpp"
#include "io/tpch_loader.hpp"

#ifdef NDEBUG
#error "CUDA tests require assertions"
#endif

int main() {
  int device_count = 0;
  const cudaError_t status = cudaGetDeviceCount(&device_count);
  if (status != cudaSuccess || device_count == 0) {
    std::cout << "Skipping CUDA runtime test: " << cudaGetErrorString(status)
              << "\n";
    return 0;
  }

  const memq5::TpchDatabase db = memq5::load_tpch(MEMQ5_FIXTURE_DIR);

  memq5::Q5Params params;
  params.region_name = "ASIA";
  memq5::set_q5_date(&params, "1994-01-01");

  const memq5::Q5Result cpu = memq5::execute_q5_cpu(db, params);
  const memq5::Q5Result gpu = memq5::execute_q5_gpu_copy(db, params);
  const memq5::Q5Result managed = memq5::execute_q5_gpu_managed(db, params);
  const memq5::Q5Result mapped = memq5::execute_q5_gpu_mapped(db, params);

  assert(memq5::result_hash_hex(cpu) == memq5::result_hash_hex(gpu));
  assert(memq5::result_hash_hex(cpu) == memq5::result_hash_hex(managed));
  assert(memq5::result_hash_hex(cpu) == memq5::result_hash_hex(mapped));
  assert(cpu.rows.size() == gpu.rows.size());
  assert(cpu.rows.size() == managed.rows.size());
  assert(cpu.rows.size() == mapped.rows.size());

  for (const memq5::Q5Result* result : {&gpu, &managed, &mapped}) {
    assert(result->counters.input_lineitem_rows == 6);
    assert(result->counters.matched_lineitem_rows == 2);
    assert(result->counters.cpu_input_rows == 0);
    assert(result->counters.gpu_input_rows == 6);
    assert(result->counters.d2h_bytes > 0);
  }
  assert(gpu.counters.h2d_bytes > 0);
  assert(gpu.counters.mapped_remote_read_bytes == 0);
  assert(managed.counters.h2d_bytes > 0);
  assert(managed.counters.mapped_remote_read_bytes == 0);
  assert(mapped.counters.h2d_bytes == 0);
  assert(mapped.counters.mapped_remote_read_bytes > 0);

  for (std::size_t i = 0; i < cpu.rows.size(); ++i) {
    assert(cpu.rows[i].nation_name == gpu.rows[i].nation_name);
    assert(cpu.rows[i].revenue_1e4 == gpu.rows[i].revenue_1e4);
    assert(cpu.rows[i].nation_name == managed.rows[i].nation_name);
    assert(cpu.rows[i].revenue_1e4 == managed.rows[i].revenue_1e4);
    assert(cpu.rows[i].nation_name == mapped.rows[i].nation_name);
    assert(cpu.rows[i].revenue_1e4 == mapped.rows[i].revenue_1e4);
  }

  return 0;
}

#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#include "hybrid/batch_partition.hpp"
#include "session/q5_session_io.hpp"

#ifdef MEMQ5_TEST_HYBRID_AUTO_CUDA
#include <cuda_runtime.h>

#include "cuda/q5_arrow_cuda.hpp"
#include "engine/q5_params.hpp"
#include "engine/q5_result_io.hpp"
#include "hybrid/q5_hybrid.hpp"
#include "io/arrow_q5_loader.hpp"
#endif

namespace {

void assert_boundary_partition() {
  const std::vector<int64_t> lengths{3, 5, 2};

  const auto below =
      memq5::partition_batch_lengths_at_boundary(lengths, 0.52).ValueOrDie();
  assert(below.cpu_rows == 3);
  assert(below.gpu_rows == 7);
  assert((below.cpu_slices == std::vector<memq5::BatchSlice>{{0, 0, 3}}));
  assert((below.gpu_slices ==
          std::vector<memq5::BatchSlice>{{1, 0, 5}, {2, 0, 2}}));

  const auto above =
      memq5::partition_batch_lengths_at_boundary(lengths, 0.79).ValueOrDie();
  assert(above.cpu_rows == 8);
  assert(above.gpu_rows == 2);
  assert((above.cpu_slices ==
          std::vector<memq5::BatchSlice>{{0, 0, 3}, {1, 0, 5}}));
  assert((above.gpu_slices == std::vector<memq5::BatchSlice>{{2, 0, 2}}));
}

void assert_ties_choose_lower_boundary() {
  const auto partition =
      memq5::partition_batch_lengths_at_boundary({3, 4, 3}, 0.5)
          .ValueOrDie();
  assert(partition.cpu_rows == 3);
  assert(partition.gpu_rows == 7);
}

void assert_rows_are_conserved() {
  const std::vector<int64_t> lengths{4, 0, 7, 2};
  for (const double ratio : {0.0, 0.01, 0.5, 0.99, 1.0}) {
    const auto first =
        memq5::partition_batch_lengths_at_boundary(lengths, ratio)
            .ValueOrDie();
    const auto second =
        memq5::partition_batch_lengths_at_boundary(lengths, ratio)
            .ValueOrDie();
    assert(first.cpu_rows + first.gpu_rows == 13);
    assert(first.cpu_slices == second.cpu_slices);
    assert(first.gpu_slices == second.gpu_slices);
    for (const auto& slice : first.cpu_slices) {
      assert(slice.offset == 0);
      assert(slice.length == lengths[static_cast<std::size_t>(slice.batch_index)]);
    }
    for (const auto& slice : first.gpu_slices) {
      assert(slice.offset == 0);
      assert(slice.length == lengths[static_cast<std::size_t>(slice.batch_index)]);
    }
  }
}

void assert_empty_input_is_safe() {
  for (const std::vector<int64_t>& lengths :
       {std::vector<int64_t>{}, std::vector<int64_t>{0, 0}}) {
    const auto partition =
        memq5::partition_batch_lengths_at_boundary(lengths, 0.5).ValueOrDie();
    assert(partition.cpu_rows == 0);
    assert(partition.gpu_rows == 0);
    assert(partition.cpu_slices.empty());
    assert(partition.gpu_slices.empty());
  }
}

void assert_invalid_input_is_rejected() {
  assert(!memq5::partition_batch_lengths_at_boundary({3, 5}, -0.01).ok());
  assert(!memq5::partition_batch_lengths_at_boundary({3, 5}, 1.01).ok());
  assert(!memq5::partition_batch_lengths_at_boundary(
              {3, 5}, std::numeric_limits<double>::quiet_NaN())
              .ok());
  assert(!memq5::partition_batch_lengths_at_boundary({3, -1}, 0.5).ok());
  assert(!memq5::partition_batch_lengths_at_boundary(
              {std::numeric_limits<int64_t>::max(), 1}, 0.5)
              .ok());
}

void assert_fixed_partition_semantics_are_unchanged() {
  const auto partition =
      memq5::partition_batch_lengths({3, 5, 2}, 0.5).ValueOrDie();
  assert(partition.cpu_rows == 5);
  assert(partition.gpu_rows == 5);
  assert((partition.cpu_slices ==
          std::vector<memq5::BatchSlice>{{0, 0, 3}, {1, 0, 2}}));
  assert((partition.gpu_slices ==
          std::vector<memq5::BatchSlice>{{1, 2, 3}, {2, 0, 2}}));
}

void assert_auto_provenance_is_setup_only() {
  std::ostringstream output;
  memq5::Q5SessionJsonlWriter writer(output, "auto-session");

  memq5::Q5SessionSetupRecord setup_record;
  setup_record.engine = "hybrid-arrow";
  setup_record.hybrid_model_version = "hybrid-cost-v1-batch-v1";
  setup_record.calibration_rows = 10;
  setup_record.cpu_calibration_requests = 1;
  setup_record.gpu_calibration_requests = 1;
  setup_record.cpu_calibration_ms = 4.0;
  setup_record.gpu_calibration_ms = 3.0;
  setup_record.gpu_kernel_calibration_ms = 2.0;
  setup_record.cpu_rows_per_ms = 2.5;
  setup_record.gpu_kernel_rows_per_ms = 5.0;
  setup_record.gpu_fixed_ms = 1.0;
  setup_record.predicted_cpu_ratio = 0.6;
  setup_record.selected_batch_boundary_rows = 7;
  setup_record.selected_cpu_ratio = 0.7;
  setup_record.realized_cpu_ratio = 0.7;
  setup_record.tune_ms = 9.0;
  writer.WriteSetup(setup_record);

  memq5::Q5SessionRequestRecord request_record;
  request_record.selected_cpu_ratio = 0.7;
  writer.WriteRequest(request_record);

  std::istringstream records(output.str());
  std::string setup_line;
  std::string request_line;
  assert(std::getline(records, setup_line));
  assert(std::getline(records, request_line));
  const nlohmann::json setup = nlohmann::json::parse(setup_line);
  const nlohmann::json request = nlohmann::json::parse(request_line);

  assert(setup.at("hybrid_model_version") == "hybrid-cost-v1-batch-v1");
  assert(setup.at("calibration_rows") == 10);
  assert(setup.at("cpu_calibration_requests") == 1);
  assert(setup.at("gpu_calibration_requests") == 1);
  assert(setup.at("cpu_calibration_ms") == 4.0);
  assert(setup.at("gpu_calibration_ms") == 3.0);
  assert(setup.at("gpu_kernel_calibration_ms") == 2.0);
  assert(setup.at("cpu_rows_per_ms") == 2.5);
  assert(setup.at("gpu_kernel_rows_per_ms") == 5.0);
  assert(!setup.contains("gpu_rows_per_ms"));
  assert(setup.at("gpu_fixed_ms") == 1.0);
  assert(setup.at("predicted_cpu_ratio") == 0.6);
  assert(setup.at("selected_batch_boundary_rows") == 7);
  assert(setup.at("realized_cpu_ratio") == 0.7);
  assert(setup.at("selected_cpu_ratio") == 0.7);
  assert(setup.at("tune_ms") == 9.0);

  assert(request.at("selected_cpu_ratio") == 0.7);
  for (const char* setup_only_key : {
           "hybrid_model_version",
           "calibration_rows",
           "cpu_calibration_requests",
           "gpu_calibration_requests",
           "cpu_calibration_ms",
           "gpu_calibration_ms",
           "gpu_kernel_calibration_ms",
           "cpu_rows_per_ms",
           "gpu_kernel_rows_per_ms",
           "gpu_fixed_ms",
           "predicted_cpu_ratio",
           "selected_batch_boundary_rows",
           "realized_cpu_ratio",
           "tune_ms",
       }) {
    assert(!request.contains(setup_only_key));
  }
}

#ifdef MEMQ5_TEST_HYBRID_AUTO_CUDA
int require_cuda_device() {
  int device_count = 0;
  const cudaError_t status = cudaGetDeviceCount(&device_count);
  if (status == cudaErrorNoDevice ||
      (status == cudaSuccess && device_count == 0)) {
    std::cout << "Skipping hybrid-auto CUDA runtime test: "
              << cudaGetErrorString(status) << '\n';
    return 77;
  }
  if (status != cudaSuccess) {
    std::cerr << "CUDA device discovery failed: "
              << cudaGetErrorString(status) << '\n';
    return 1;
  }
  return 0;
}

void assert_calibration_results_must_match_exactly() {
  memq5::Q5Result cpu;
  cpu.rows = {{"CHINA", 100}, {"INDIA", 50}};
  cpu.counters.input_lineitem_rows = 10;
  cpu.counters.matched_lineitem_rows = 3;
  cpu.counters.cpu_input_rows = 10;

  memq5::Q5Result gpu = cpu;
  gpu.counters.cpu_input_rows = 0;
  gpu.counters.gpu_input_rows = 10;
  assert(memq5::validate_hybrid_calibration_results(cpu, gpu, 10).ok());

  memq5::Q5Result different_rows = gpu;
  different_rows.rows[0].revenue_1e4 += 1;
  assert(!memq5::validate_hybrid_calibration_results(cpu, different_rows, 10)
              .ok());

  memq5::Q5Result different_order = gpu;
  std::swap(different_order.rows[0], different_order.rows[1]);
  assert(!memq5::validate_hybrid_calibration_results(cpu, different_order, 10)
              .ok());

  memq5::Q5Result different_matched = gpu;
  ++different_matched.counters.matched_lineitem_rows;
  assert(!memq5::validate_hybrid_calibration_results(cpu, different_matched,
                                                      10)
              .ok());

  memq5::Q5Result wrong_gpu_ownership = gpu;
  wrong_gpu_ownership.counters.gpu_input_rows = 9;
  assert(!memq5::validate_hybrid_calibration_results(cpu,
                                                      wrong_gpu_ownership, 10)
              .ok());
}

void assert_fixed_endpoints_do_not_use_idle_children() {
  const auto dataset =
      memq5::load_arrow_q5_dataset(MEMQ5_ARROW_FIXTURE_DIR).ValueOrDie();
  memq5::Q5Params params;
  params.region_name = "ASIA";
  memq5::set_q5_date(&params, "1994-01-01");
  params.threads = 2;

  for (const double ratio : {0.0, 1.0}) {
    memq5::HybridOptions options;
    options.cpu_ratio = ratio;
    options.cpu_threads = 2;
    auto session =
        memq5::HybridQ5Session::Make(dataset, params, options).ValueOrDie();
    const auto resident = session->Execute().ValueOrDie();
    const auto cold =
        memq5::execute_q5_hybrid(dataset, params, options).ValueOrDie();

    assert(memq5::result_hash_hex(resident) == "248d10b6ee352953");
    assert(memq5::result_hash_hex(cold) == "248d10b6ee352953");
    if (ratio == 0.0) {
      assert(resident.counters.cpu_input_rows == 0);
      assert(resident.timing.cpu_ms == 0.0);
      assert(resident.timing.overlap_ms == 0.0);
      assert(cold.counters.cpu_input_rows == 0);
      assert(cold.timing.cpu_ms == 0.0);
      assert(cold.timing.overlap_ms == 0.0);
    } else {
      assert(session->setup().resident_gpu_bytes == 0);
      assert(session->setup().initial_h2d_ms == 0.0);
      assert(resident.counters.gpu_input_rows == 0);
      assert(resident.counters.d2h_bytes == 0);
      assert(resident.timing.d2h_ms == 0.0);
      assert(resident.timing.gpu_ms == 0.0);
      assert(resident.timing.overlap_ms == 0.0);
      assert(cold.counters.gpu_input_rows == 0);
      assert(cold.counters.h2d_bytes == 0);
      assert(cold.counters.d2h_bytes == 0);
      assert(cold.timing.h2d_ms == 0.0);
      assert(cold.timing.d2h_ms == 0.0);
      assert(cold.timing.gpu_ms == 0.0);
      assert(cold.timing.overlap_ms == 0.0);
    }
  }
}

void assert_fixed_empty_input_needs_no_children() {
  auto dataset =
      memq5::load_arrow_q5_dataset(MEMQ5_ARROW_FIXTURE_DIR).ValueOrDie();
  dataset.lineitem = dataset.lineitem->Slice(0, 0);
  dataset.tables["lineitem"] = dataset.lineitem;
  memq5::Q5Params params;
  params.region_name = "ASIA";
  memq5::set_q5_date(&params, "1994-01-01");
  params.threads = 2;
  memq5::HybridOptions options;
  options.cpu_ratio = 0.5;
  options.cpu_threads = 2;

  auto session =
      memq5::HybridQ5Session::Make(dataset, params, options).ValueOrDie();
  const auto result = session->Execute().ValueOrDie();
  const auto cold =
      memq5::execute_q5_hybrid(dataset, params, options).ValueOrDie();
  assert(session->setup().resident_host_bytes == 0);
  assert(session->setup().resident_gpu_bytes == 0);
  assert(result.rows.empty());
  assert(result.counters.input_lineitem_rows == 0);
  assert(result.counters.cpu_input_rows == 0);
  assert(result.counters.gpu_input_rows == 0);
  assert(result.timing.cpu_ms == 0.0);
  assert(result.timing.gpu_ms == 0.0);
  assert(result.timing.overlap_ms == 0.0);
  assert(cold.rows.empty());
  assert(cold.counters.input_lineitem_rows == 0);
  assert(cold.timing.cpu_ms == 0.0);
  assert(cold.timing.gpu_ms == 0.0);
}

void assert_first_gpu_request_uses_steady_state_reset() {
  const auto dataset =
      memq5::load_arrow_q5_dataset(MEMQ5_ARROW_FIXTURE_DIR).ValueOrDie();
  memq5::Q5Params params;
  params.region_name = "ASIA";
  memq5::set_q5_date(&params, "1994-01-01");
  params.threads = 2;
  auto session = memq5::ArrowCudaQ5Session::Make(
                     dataset, params, memq5::ArrowCudaMemoryMode::kManaged)
                     .ValueOrDie();

  const auto first = session->Execute().ValueOrDie();
  const auto second = session->Execute().ValueOrDie();
  assert(first.counters.h2d_bytes == first.counters.d2h_bytes);
  assert(second.counters.h2d_bytes == first.counters.d2h_bytes);
  assert(memq5::result_hash_hex(first) == memq5::result_hash_hex(second));
}

void assert_auto_calibrates_once_and_requests_are_stable() {
  const auto dataset =
      memq5::load_arrow_q5_dataset(MEMQ5_ARROW_FIXTURE_DIR).ValueOrDie();
  memq5::Q5Params params;
  params.region_name = "ASIA";
  memq5::set_q5_date(&params, "1994-01-01");
  params.threads = 2;

  memq5::HybridOptions fixed_options;
  fixed_options.cpu_ratio = 0.5;
  fixed_options.cpu_threads = 2;
  auto fixed =
      memq5::HybridQ5Session::Make(dataset, params, fixed_options).ValueOrDie();
  assert(fixed->cpu_ratio() == 0.5);
  assert(!fixed->auto_tuning().enabled);

  memq5::HybridOptions auto_options;
  auto_options.selection = memq5::HybridSelection::kAuto;
  auto_options.cpu_threads = 2;
  auto session =
      memq5::HybridQ5Session::Make(dataset, params, auto_options).ValueOrDie();
  const memq5::HybridAutoTuning tuning = session->auto_tuning();

  assert(tuning.enabled);
  assert(tuning.model_version == "hybrid-cost-v1-batch-v1");
  assert(tuning.calibration_rows == dataset.lineitem->num_rows());
  assert(tuning.cpu_calibration_requests == 1);
  assert(tuning.gpu_calibration_requests == 1);
  assert(tuning.cpu_calibration_ms > 0.0);
  assert(tuning.gpu_calibration_ms > 0.0);
  assert(tuning.gpu_kernel_calibration_ms > 0.0);
  assert(tuning.cpu_rows_per_ms > 0.0);
  assert(tuning.gpu_kernel_rows_per_ms > 0.0);
  assert(tuning.gpu_fixed_ms >= 0.0);
  assert(tuning.predicted_cpu_ratio >= 0.0);
  assert(tuning.predicted_cpu_ratio <= 1.0);
  assert(tuning.selected_batch_boundary_rows >= 0);
  assert(tuning.selected_batch_boundary_rows <= tuning.calibration_rows);
  assert(tuning.realized_cpu_ratio >= 0.0);
  assert(tuning.realized_cpu_ratio <= 1.0);
  assert(tuning.tune_ms >= 0.0);
  assert(std::fabs(tuning.realized_cpu_ratio - session->cpu_ratio()) < 1e-12);
  assert(std::fabs(tuning.realized_cpu_ratio * tuning.calibration_rows -
                   tuning.selected_batch_boundary_rows) < 1e-12);

  const auto first = session->Execute().ValueOrDie();
  const auto second = session->Execute().ValueOrDie();
  assert(memq5::result_hash_hex(first) == "248d10b6ee352953");
  assert(memq5::result_hash_hex(second) == memq5::result_hash_hex(first));
  assert(first.counters.input_lineitem_rows == tuning.calibration_rows);
  assert(first.counters.cpu_input_rows + first.counters.gpu_input_rows ==
         tuning.calibration_rows);
  assert(second.counters.cpu_input_rows == first.counters.cpu_input_rows);
  assert(second.counters.gpu_input_rows == first.counters.gpu_input_rows);

  const auto& after_requests = session->auto_tuning();
  assert(after_requests.cpu_calibration_requests == 1);
  assert(after_requests.gpu_calibration_requests == 1);
  assert(after_requests.tune_ms == tuning.tune_ms);
  assert(after_requests.predicted_cpu_ratio == tuning.predicted_cpu_ratio);
  assert(after_requests.realized_cpu_ratio == tuning.realized_cpu_ratio);
}
#endif

}  // namespace

int main() {
#ifdef MEMQ5_TEST_HYBRID_AUTO_CUDA
  const int cuda_status = require_cuda_device();
  if (cuda_status != 0) {
    return cuda_status;
  }
#endif
  assert_boundary_partition();
  assert_ties_choose_lower_boundary();
  assert_rows_are_conserved();
  assert_empty_input_is_safe();
  assert_invalid_input_is_rejected();
  assert_fixed_partition_semantics_are_unchanged();
  assert_auto_provenance_is_setup_only();
#ifdef MEMQ5_TEST_HYBRID_AUTO_CUDA
  assert_calibration_results_must_match_exactly();
  assert_fixed_endpoints_do_not_use_idle_children();
  assert_fixed_empty_input_needs_no_children();
  assert_first_gpu_request_uses_steady_state_reset();
  assert_auto_calibrates_once_and_requests_are_stable();
#endif
  return 0;
}

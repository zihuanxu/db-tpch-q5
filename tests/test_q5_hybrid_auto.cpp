#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "hybrid/batch_partition.hpp"
#include "session/q5_session_io.hpp"

#ifdef MEMQ5_TEST_HYBRID_AUTO_CUDA
#include <cuda_runtime.h>

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
  setup_record.gpu_rows_per_ms = 5.0;
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
  assert(setup.at("gpu_rows_per_ms") == 5.0);
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
           "gpu_rows_per_ms",
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
void assert_auto_calibrates_once_and_requests_are_stable() {
  int device_count = 0;
  const cudaError_t cuda_status = cudaGetDeviceCount(&device_count);
  if (cuda_status == cudaErrorNoDevice ||
      (cuda_status == cudaSuccess && device_count == 0)) {
    std::exit(77);
  }
  assert(cuda_status == cudaSuccess);

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
  assert(tuning.gpu_rows_per_ms > 0.0);
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
  assert_boundary_partition();
  assert_ties_choose_lower_boundary();
  assert_rows_are_conserved();
  assert_empty_input_is_safe();
  assert_invalid_input_is_rejected();
  assert_fixed_partition_semantics_are_unchanged();
  assert_auto_provenance_is_setup_only();
#ifdef MEMQ5_TEST_HYBRID_AUTO_CUDA
  assert_auto_calibrates_once_and_requests_are_stable();
#endif
  return 0;
}

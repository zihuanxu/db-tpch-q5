#include "hybrid/q5_hybrid.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <exception>
#include <future>
#include <map>
#include <string>
#include <utility>
#include <vector>

#include <arrow/api.h>

#include "common/nvtx_range.hpp"
#include "common/timer.hpp"
#include "cpu/q5_arrow_cpu.hpp"
#include "cuda/q5_arrow_cuda.hpp"
#include "hybrid/batch_partition.hpp"
#include "hybrid/hybrid_cost_model.hpp"

namespace memq5 {
namespace {

arrow::Result<std::vector<int64_t>> lineitem_batch_lengths(
    const std::shared_ptr<arrow::Table>& lineitem) {
  if (lineitem == nullptr) {
    return arrow::Status::Invalid("missing Arrow lineitem table");
  }
  arrow::TableBatchReader reader(lineitem);
  std::vector<int64_t> lengths;
  while (true) {
    std::shared_ptr<arrow::RecordBatch> batch;
    ARROW_RETURN_NOT_OK(reader.ReadNext(&batch));
    if (batch == nullptr) {
      break;
    }
    lengths.push_back(batch->num_rows());
  }
  return lengths;
}

arrow::Status checked_add(int64_t value, int64_t* destination,
                          const char* context) {
  int64_t sum = 0;
  if (__builtin_add_overflow(*destination, value, &sum)) {
    return arrow::Status::CapacityError(context, " overflow");
  }
  *destination = sum;
  return arrow::Status::OK();
}

arrow::Result<Q5SessionSetup> combine_session_setup(
    const Q5SessionSetup& cpu_setup, const Q5SessionSetup& gpu_setup,
    const Stopwatch& setup_timer) {
  Q5SessionSetup setup;
  setup.plan_build_ms = cpu_setup.plan_build_ms + gpu_setup.plan_build_ms;
  setup.host_staging_ms = cpu_setup.host_staging_ms + gpu_setup.host_staging_ms;
  setup.allocation_ms = cpu_setup.allocation_ms + gpu_setup.allocation_ms;
  setup.initial_h2d_ms =
      cpu_setup.initial_h2d_ms + gpu_setup.initial_h2d_ms;
  ARROW_RETURN_NOT_OK(checked_add(cpu_setup.resident_host_bytes,
                                  &setup.resident_host_bytes,
                                  "hybrid resident host bytes"));
  ARROW_RETURN_NOT_OK(checked_add(gpu_setup.resident_host_bytes,
                                  &setup.resident_host_bytes,
                                  "hybrid resident host bytes"));
  ARROW_RETURN_NOT_OK(checked_add(cpu_setup.resident_gpu_bytes,
                                  &setup.resident_gpu_bytes,
                                  "hybrid resident GPU bytes"));
  ARROW_RETURN_NOT_OK(checked_add(gpu_setup.resident_gpu_bytes,
                                  &setup.resident_gpu_bytes,
                                  "hybrid resident GPU bytes"));
  ARROW_RETURN_NOT_OK(checked_add(cpu_setup.resident_pinned_bytes,
                                  &setup.resident_pinned_bytes,
                                  "hybrid resident pinned bytes"));
  ARROW_RETURN_NOT_OK(checked_add(gpu_setup.resident_pinned_bytes,
                                  &setup.resident_pinned_bytes,
                                  "hybrid resident pinned bytes"));
  setup.total_ms = setup_timer.elapsed_ms();
  return setup;
}

struct CalibratedPartition {
  HybridPartition partition;
  HybridAutoTuning tuning;
};

arrow::Result<CalibratedPartition> calibrate_auto_partition(
    const ArrowQ5Dataset& dataset, const Q5Params& params, int cpu_threads,
    const std::vector<int64_t>& batch_lengths) {
  if (dataset.lineitem == nullptr || dataset.lineitem->num_rows() <= 0) {
    return arrow::Status::Invalid(
        "hybrid auto calibration requires positive lineitem rows");
  }

  Stopwatch tune_timer;
  HybridAutoTuning tuning;
  tuning.enabled = true;
  tuning.model_version = "hybrid-cost-v1-batch-v1";
  tuning.calibration_rows = dataset.lineitem->num_rows();

  Q5Params cpu_params = params;
  cpu_params.threads = cpu_threads;
  ARROW_ASSIGN_OR_RAISE(auto cpu_calibration_session,
                        ArrowCpuQ5Session::Make(dataset, cpu_params));
  ARROW_ASSIGN_OR_RAISE(Q5Result cpu_calibration,
                        cpu_calibration_session->Execute());
  tuning.cpu_calibration_requests = 1;
  tuning.cpu_calibration_ms = cpu_calibration.timing.total_ms;
  cpu_calibration_session.reset();

  ARROW_ASSIGN_OR_RAISE(
      auto gpu_calibration_session,
      ArrowCudaQ5Session::Make(dataset, params, ArrowCudaMemoryMode::kCopy));
  ARROW_ASSIGN_OR_RAISE(Q5Result gpu_calibration,
                        gpu_calibration_session->Execute());
  tuning.gpu_calibration_requests = 1;
  tuning.gpu_calibration_ms = gpu_calibration.timing.total_ms;
  tuning.gpu_kernel_calibration_ms = gpu_calibration.timing.kernel_ms;
  tuning.gpu_fixed_ms =
      std::max(0.0, tuning.gpu_calibration_ms -
                        tuning.gpu_kernel_calibration_ms);
  gpu_calibration_session.reset();

  const HybridCalibration calibration{
      tuning.calibration_rows,
      tuning.cpu_calibration_ms,
      tuning.gpu_kernel_calibration_ms,
      tuning.gpu_fixed_ms,
  };
  ARROW_ASSIGN_OR_RAISE(const HybridPrediction prediction,
                        predict_hybrid_ratio(calibration));
  tuning.cpu_rows_per_ms =
      static_cast<double>(tuning.calibration_rows) / tuning.cpu_calibration_ms;
  tuning.gpu_rows_per_ms = static_cast<double>(tuning.calibration_rows) /
                           tuning.gpu_kernel_calibration_ms;
  tuning.predicted_cpu_ratio = prediction.predicted_cpu_ratio;

  ARROW_ASSIGN_OR_RAISE(
      HybridPartition partition,
      partition_batch_lengths_at_boundary(batch_lengths,
                                          tuning.predicted_cpu_ratio));
  if (partition.cpu_rows + partition.gpu_rows != tuning.calibration_rows) {
    return arrow::Status::Invalid(
        "hybrid auto calibration row count does not match Arrow batches");
  }
  tuning.selected_batch_boundary_rows = partition.cpu_rows;
  tuning.realized_cpu_ratio =
      static_cast<double>(partition.cpu_rows) / tuning.calibration_rows;
  tuning.tune_ms = tune_timer.elapsed_ms();
  return CalibratedPartition{std::move(partition), std::move(tuning)};
}

arrow::Result<Q5Result> merge_hybrid_results(
    const Q5Result& cpu, const Q5Result& gpu, double execution_wall_ms,
    const Stopwatch& total_timer) {
  NvtxRange merge_range("merge");
  Q5Result result;
  std::map<std::string, int64_t> revenue_by_nation;
  for (const Q5Result* partial : {&cpu, &gpu}) {
    for (const auto& row : partial->rows) {
      ARROW_RETURN_NOT_OK(checked_add(
          row.revenue_1e4, &revenue_by_nation[row.nation_name],
          "hybrid Q5 revenue accumulation"));
    }
  }
  for (const auto& [nation, revenue] : revenue_by_nation) {
    if (revenue != 0) {
      result.rows.push_back(Q5ResultRow{nation, revenue});
    }
  }
  std::sort(result.rows.begin(), result.rows.end(),
            [](const Q5ResultRow& left, const Q5ResultRow& right) {
              if (left.revenue_1e4 != right.revenue_1e4) {
                return left.revenue_1e4 > right.revenue_1e4;
              }
              return left.nation_name < right.nation_name;
            });

  ARROW_RETURN_NOT_OK(checked_add(
      cpu.counters.input_lineitem_rows, &result.counters.input_lineitem_rows,
      "hybrid input rows"));
  ARROW_RETURN_NOT_OK(checked_add(
      gpu.counters.input_lineitem_rows, &result.counters.input_lineitem_rows,
      "hybrid input rows"));
  ARROW_RETURN_NOT_OK(checked_add(
      cpu.counters.matched_lineitem_rows, &result.counters.matched_lineitem_rows,
      "hybrid matched rows"));
  ARROW_RETURN_NOT_OK(checked_add(
      gpu.counters.matched_lineitem_rows, &result.counters.matched_lineitem_rows,
      "hybrid matched rows"));
  result.counters.cpu_input_rows = cpu.counters.cpu_input_rows;
  result.counters.gpu_input_rows = gpu.counters.gpu_input_rows;
  result.counters.h2d_bytes = gpu.counters.h2d_bytes;
  result.counters.d2h_bytes = gpu.counters.d2h_bytes;
  result.counters.mapped_remote_read_bytes =
      gpu.counters.mapped_remote_read_bytes;

  result.timing.build_ms = cpu.timing.build_ms + gpu.timing.build_ms;
  result.timing.h2d_ms = gpu.timing.h2d_ms;
  result.timing.kernel_ms = gpu.timing.kernel_ms;
  result.timing.d2h_ms = gpu.timing.d2h_ms;
  result.timing.scan_ms = execution_wall_ms;
  result.timing.cpu_ms = cpu.timing.total_ms;
  result.timing.gpu_ms = gpu.timing.total_ms;
  result.timing.overlap_ms =
      std::max(0.0, result.timing.cpu_ms + result.timing.gpu_ms -
                        execution_wall_ms);
  result.timing.total_ms = total_timer.elapsed_ms();
  return result;
}

}  // namespace

HybridQ5Session::HybridQ5Session(
    std::unique_ptr<ArrowCpuQ5Session> cpu_session,
    std::unique_ptr<ArrowCudaQ5Session> gpu_session, Q5SessionSetup setup,
    double cpu_ratio, HybridAutoTuning auto_tuning)
    : cpu_session_(std::move(cpu_session)),
      gpu_session_(std::move(gpu_session)),
      setup_(setup),
      cpu_ratio_(cpu_ratio),
      auto_tuning_(std::move(auto_tuning)) {}

HybridQ5Session::~HybridQ5Session() = default;

arrow::Result<std::unique_ptr<HybridQ5Session>> HybridQ5Session::Make(
    const ArrowQ5Dataset& dataset, const Q5Params& params,
    const HybridOptions& options) {
  try {
    if (options.selection != HybridSelection::kFixed &&
        options.selection != HybridSelection::kAuto) {
      return arrow::Status::Invalid("unknown hybrid selection mode");
    }
    if (options.selection == HybridSelection::kFixed &&
        (!std::isfinite(options.cpu_ratio) || options.cpu_ratio < 0.0 ||
         options.cpu_ratio > 1.0)) {
      return arrow::Status::Invalid("cpu_ratio must be between 0 and 1");
    }
    if (options.cpu_threads <= 0) {
      return arrow::Status::Invalid("cpu_threads must be positive");
    }

    NvtxRange session_setup_range("session_setup");
    Stopwatch setup_timer;
    ARROW_ASSIGN_OR_RAISE(const auto batch_lengths,
                          lineitem_batch_lengths(dataset.lineitem));

    HybridPartition partition;
    HybridAutoTuning auto_tuning;
    double selected_cpu_ratio = options.cpu_ratio;
    if (options.selection == HybridSelection::kAuto) {
      ARROW_ASSIGN_OR_RAISE(
          CalibratedPartition calibrated,
          calibrate_auto_partition(dataset, params, options.cpu_threads,
                                   batch_lengths));
      partition = std::move(calibrated.partition);
      auto_tuning = std::move(calibrated.tuning);
      selected_cpu_ratio = auto_tuning.realized_cpu_ratio;
    } else {
      ARROW_ASSIGN_OR_RAISE(
          partition,
          partition_batch_lengths(batch_lengths, options.cpu_ratio));
    }

    ArrowQ5Dataset cpu_dataset = dataset;
    cpu_dataset.lineitem = dataset.lineitem->Slice(0, partition.cpu_rows);
    cpu_dataset.tables["lineitem"] = cpu_dataset.lineitem;
    ArrowQ5Dataset gpu_dataset = dataset;
    gpu_dataset.lineitem =
        dataset.lineitem->Slice(partition.cpu_rows, partition.gpu_rows);
    gpu_dataset.tables["lineitem"] = gpu_dataset.lineitem;

    Q5Params cpu_params = params;
    cpu_params.threads = options.cpu_threads;
    ARROW_ASSIGN_OR_RAISE(auto cpu_session,
                          ArrowCpuQ5Session::Make(cpu_dataset, cpu_params));
    ARROW_ASSIGN_OR_RAISE(
        auto gpu_session,
        ArrowCudaQ5Session::Make(gpu_dataset, params,
                                 ArrowCudaMemoryMode::kCopy));
    ARROW_ASSIGN_OR_RAISE(
        Q5SessionSetup setup,
        combine_session_setup(cpu_session->setup(), gpu_session->setup(),
                              setup_timer));
    return std::unique_ptr<HybridQ5Session>(new HybridQ5Session(
        std::move(cpu_session), std::move(gpu_session), setup,
        selected_cpu_ratio, std::move(auto_tuning)));
  } catch (const std::bad_alloc&) {
    return arrow::Status::CapacityError("hybrid Q5 session allocation failed");
  } catch (const std::exception& error) {
    return arrow::Status::UnknownError("hybrid Q5 session failed: ",
                                       error.what());
  }
}

arrow::Result<Q5Result> HybridQ5Session::Execute() {
  try {
    NvtxRange request_range("request");
    Stopwatch total_timer;
    Stopwatch execution_timer;
    auto gpu_future = std::async(std::launch::async, [this]() {
      return gpu_session_->Execute();
    });
    arrow::Result<Q5Result> cpu_result = cpu_session_->Execute();
    // Inspect neither status until get() has joined the GPU request. The child
    // APIs return ordinary failures as Result; future destruction also joins
    // the GPU request if an unexpected CPU exception escapes before get().
    arrow::Result<Q5Result> gpu_result = gpu_future.get();
    const double execution_wall_ms = execution_timer.elapsed_ms();

    if (!cpu_result.ok()) {
      return cpu_result.status();
    }
    if (!gpu_result.ok()) {
      return gpu_result.status();
    }
    return merge_hybrid_results(*cpu_result, *gpu_result, execution_wall_ms,
                                total_timer);
  } catch (const std::bad_alloc&) {
    return arrow::Status::CapacityError("hybrid Q5 session allocation failed");
  } catch (const std::exception& error) {
    return arrow::Status::UnknownError("hybrid Q5 session failed: ",
                                       error.what());
  }
}

const Q5SessionSetup& HybridQ5Session::setup() const { return setup_; }

double HybridQ5Session::cpu_ratio() const { return cpu_ratio_; }

const HybridAutoTuning& HybridQ5Session::auto_tuning() const {
  return auto_tuning_;
}

arrow::Result<Q5Result> execute_q5_hybrid(
    const ArrowQ5Dataset& dataset, const Q5Params& params,
    const HybridOptions& options) {
  try {
    if (options.selection != HybridSelection::kFixed) {
      return arrow::Status::Invalid(
          "hybrid auto selection requires a resident session");
    }
    if (!std::isfinite(options.cpu_ratio) || options.cpu_ratio < 0.0 ||
        options.cpu_ratio > 1.0) {
      return arrow::Status::Invalid("cpu_ratio must be between 0 and 1");
    }
    if (options.cpu_threads <= 0) {
      return arrow::Status::Invalid("cpu_threads must be positive");
    }
    NvtxRange request_range("request");
    Stopwatch total_timer;
    ARROW_ASSIGN_OR_RAISE(const auto batch_lengths,
                          lineitem_batch_lengths(dataset.lineitem));
    ARROW_ASSIGN_OR_RAISE(
        const HybridPartition partition,
        partition_batch_lengths(batch_lengths, options.cpu_ratio));

    ArrowQ5Dataset cpu_dataset = dataset;
    cpu_dataset.lineitem = dataset.lineitem->Slice(0, partition.cpu_rows);
    cpu_dataset.tables["lineitem"] = cpu_dataset.lineitem;
    ArrowQ5Dataset gpu_dataset = dataset;
    gpu_dataset.lineitem =
        dataset.lineitem->Slice(partition.cpu_rows, partition.gpu_rows);
    gpu_dataset.tables["lineitem"] = gpu_dataset.lineitem;

    Q5Params cpu_params = params;
    cpu_params.threads = options.cpu_threads;
    Stopwatch execution_timer;
    auto gpu_future = std::async(
        std::launch::async, [&gpu_dataset, &params]() {
          return execute_q5_arrow_gpu_copy(gpu_dataset, params);
        });
    arrow::Result<Q5Result> cpu_result =
        execute_q5_arrow_cpu(cpu_dataset, cpu_params);
    arrow::Result<Q5Result> gpu_result = gpu_future.get();
    const double execution_wall_ms = execution_timer.elapsed_ms();

    if (!cpu_result.ok()) {
      return cpu_result.status();
    }
    if (!gpu_result.ok()) {
      return gpu_result.status();
    }
    return merge_hybrid_results(*cpu_result, *gpu_result, execution_wall_ms,
                                total_timer);
  } catch (const std::bad_alloc&) {
    return arrow::Status::CapacityError("hybrid Q5 allocation failed");
  } catch (const std::exception& error) {
    return arrow::Status::UnknownError("hybrid Q5 failed: ", error.what());
  }
}

}  // namespace memq5

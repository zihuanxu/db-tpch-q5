#include "session/q5_cpu_session.hpp"

#include <exception>
#include <limits>
#include <memory>
#include <new>
#include <stdexcept>
#include <system_error>
#include <unordered_set>
#include <utility>

#include <arrow/array.h>
#include <arrow/array/data.h>
#include <arrow/buffer.h>

#include "common/nvtx_range.hpp"
#include "common/timer.hpp"
#include "cpu/q5_arrow_scan.hpp"

namespace memq5 {
namespace {

template <typename Function>
auto arrow_cpu_status_boundary(Function&& function) -> decltype(function()) {
  try {
    return function();
  } catch (const std::bad_alloc&) {
    return arrow::Status::CapacityError("Arrow CPU Q5 allocation failed");
  } catch (const std::length_error& error) {
    return arrow::Status::CapacityError("Arrow CPU Q5 allocation failed: ",
                                        error.what());
  } catch (const std::system_error& error) {
    return arrow::Status::IOError("Arrow CPU Q5 thread creation failed: ",
                                  error.what());
  } catch (const std::exception& error) {
    return arrow::Status::UnknownError("Arrow CPU Q5 failed: ", error.what());
  }
}

arrow::Status add_resident_bytes(int64_t value, int64_t* total,
                                 const char* context) {
  if (value < 0 || *total > std::numeric_limits<int64_t>::max() - value) {
    return arrow::Status::CapacityError(context, " byte count overflow");
  }
  *total += value;
  return arrow::Status::OK();
}

arrow::Status add_resident_elements(std::size_t count, std::size_t width,
                                    int64_t* total, const char* context) {
  const auto max_bytes =
      static_cast<std::size_t>(std::numeric_limits<int64_t>::max());
  if (width != 0 && count > max_bytes / width) {
    return arrow::Status::CapacityError(context, " byte count overflow");
  }
  return add_resident_bytes(static_cast<int64_t>(count * width), total,
                            context);
}

std::shared_ptr<arrow::Buffer> root_buffer(
    std::shared_ptr<arrow::Buffer> buffer) {
  while (buffer != nullptr && buffer->parent() != nullptr) {
    buffer = buffer->parent();
  }
  return buffer;
}

arrow::Status add_array_buffer_bytes(
    const std::shared_ptr<arrow::ArrayData>& data,
    std::unordered_set<const arrow::Buffer*>* seen, int64_t* total) {
  if (data == nullptr) {
    return arrow::Status::Invalid("missing Arrow array data");
  }
  for (const auto& buffer : data->buffers) {
    const auto root = root_buffer(buffer);
    if (root != nullptr && seen->insert(root.get()).second) {
      ARROW_RETURN_NOT_OK(add_resident_bytes(
          root->size(), total, "Arrow CPU resident buffer"));
    }
  }
  for (const auto& child : data->child_data) {
    ARROW_RETURN_NOT_OK(add_array_buffer_bytes(child, seen, total));
  }
  if (data->dictionary != nullptr) {
    ARROW_RETURN_NOT_OK(add_array_buffer_bytes(data->dictionary, seen, total));
  }
  return arrow::Status::OK();
}

arrow::Result<int64_t> cpu_resident_host_bytes(
    const std::shared_ptr<arrow::Table>& lineitem, const ArrowQ5Plan& plan) {
  if (lineitem == nullptr) {
    return arrow::Status::Invalid("missing Arrow lineitem table");
  }

  // This is deterministic logical payload, not allocator/RSS accounting.
  // Count each root Arrow backing buffer retained by the zero-copy table once,
  // including full roots pinned by slices. Add live plan element/string bytes;
  // exclude schema/container metadata, spare capacity, and allocator padding.
  int64_t bytes = 0;
  std::unordered_set<const arrow::Buffer*> seen;
  for (const auto& column : lineitem->columns()) {
    for (const auto& chunk : column->chunks()) {
      if (chunk == nullptr) {
        return arrow::Status::Invalid("missing Arrow lineitem chunk");
      }
      ARROW_RETURN_NOT_OK(add_array_buffer_bytes(chunk->data(), &seen, &bytes));
    }
  }

  ARROW_RETURN_NOT_OK(add_resident_elements(
      plan.supplier_nation_by_key.size(), sizeof(int32_t), &bytes,
      "Arrow CPU supplier plan"));
  ARROW_RETURN_NOT_OK(add_resident_elements(
      plan.order_nation_by_key.size(), sizeof(int32_t), &bytes,
      "Arrow CPU order plan"));
  for (const auto& name : plan.nation_name_by_key) {
    ARROW_RETURN_NOT_OK(add_resident_elements(
        name.size(), sizeof(char), &bytes, "Arrow CPU nation-name plan"));
  }
  return bytes;
}

arrow::Result<Q5Result> scan_cpu_session_lineitem(
    const std::shared_ptr<arrow::Table>& lineitem, const ArrowQ5Plan& plan,
    int threads) {
  NvtxRange cpu_scan_range("cpu_scan");
  return scan_q5_arrow_lineitem(lineitem, plan, threads);
}

}  // namespace

ArrowCpuQ5Session::ArrowCpuQ5Session(std::shared_ptr<arrow::Table> lineitem,
                                     ArrowQ5Plan plan, int threads,
                                     Q5SessionSetup setup)
    : lineitem_(std::move(lineitem)),
      plan_(std::move(plan)),
      threads_(threads),
      setup_(setup) {}

arrow::Result<std::unique_ptr<ArrowCpuQ5Session>> ArrowCpuQ5Session::Make(
    const ArrowQ5Dataset& dataset, const Q5Params& params) {
  return arrow_cpu_status_boundary([&]()
                                       -> arrow::Result<std::unique_ptr<
                                           ArrowCpuQ5Session>> {
    if (params.threads <= 0) {
      return arrow::Status::Invalid("Q5 CPU session threads must be positive");
    }
    NvtxRange session_setup_range("session_setup");
    Stopwatch setup_timer;
    ARROW_ASSIGN_OR_RAISE(ArrowQ5Plan plan, build_arrow_q5_plan(dataset, params));
    Q5SessionSetup setup;
    setup.plan_build_ms = plan.build_ms;
    ARROW_ASSIGN_OR_RAISE(
        setup.resident_host_bytes,
        cpu_resident_host_bytes(dataset.lineitem, plan));
    setup.total_ms = setup_timer.elapsed_ms();
    return std::unique_ptr<ArrowCpuQ5Session>(new ArrowCpuQ5Session(
        dataset.lineitem, std::move(plan), params.threads, setup));
  });
}

arrow::Result<Q5Result> ArrowCpuQ5Session::Execute() const {
  return arrow_cpu_status_boundary([&]() -> arrow::Result<Q5Result> {
    NvtxRange request_range("request");
    Stopwatch total_timer;
    ARROW_ASSIGN_OR_RAISE(Q5Result result,
                          scan_cpu_session_lineitem(lineitem_, plan_, threads_));
    result.timing.build_ms = 0.0;
    result.timing.total_ms = total_timer.elapsed_ms();
    return result;
  });
}

const Q5SessionSetup& ArrowCpuQ5Session::setup() const { return setup_; }

}  // namespace memq5

#include "cuda/q5_arrow_cuda.hpp"

#include <cuda_runtime.h>

#include <algorithm>
#include <cstdint>
#include <limits>
#include <memory>
#include <new>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <arrow/api.h>

#include "common/timer.hpp"
#include "engine/arrow_q5_plan.hpp"

namespace memq5 {
namespace {

struct ArrowGpuInput {
  ArrowQ5Plan plan;
  std::vector<int32_t> order_keys;
  std::vector<int32_t> supplier_keys;
  std::vector<int64_t> price_cents;
  std::vector<int32_t> discount_hundredths;
  double build_ms = 0.0;
};

class CudaCapacityError : public std::runtime_error {
public:
  using std::runtime_error::runtime_error;
};

void check_cuda(cudaError_t status, const char* context) {
  if (status != cudaSuccess) {
    const std::string message =
        std::string(context) + ": " + cudaGetErrorString(status);
    if (status == cudaErrorMemoryAllocation) {
      throw CudaCapacityError(message);
    }
    throw std::runtime_error(message);
  }
}

class CudaEvent {
public:
  CudaEvent() { check_cuda(cudaEventCreate(&event_), "cudaEventCreate"); }
  ~CudaEvent() {
    if (event_ != nullptr) {
      cudaEventDestroy(event_);
    }
  }

  CudaEvent(const CudaEvent&) = delete;
  CudaEvent& operator=(const CudaEvent&) = delete;

  cudaEvent_t get() const { return event_; }

private:
  cudaEvent_t event_ = nullptr;
};

double elapsed_ms(const CudaEvent& start, const CudaEvent& stop) {
  check_cuda(cudaEventSynchronize(stop.get()), "cudaEventSynchronize");
  float elapsed = 0.0f;
  check_cuda(cudaEventElapsedTime(&elapsed, start.get(), stop.get()),
             "cudaEventElapsedTime");
  return elapsed;
}

template <typename T> class DeviceBuffer {
public:
  explicit DeviceBuffer(std::size_t size) : size_(size) {
    if (size_ > 0) {
      check_cuda(cudaMalloc(&data_, size_ * sizeof(T)), "cudaMalloc");
    }
  }

  ~DeviceBuffer() {
    if (data_ != nullptr) {
      cudaFree(data_);
    }
  }

  DeviceBuffer(const DeviceBuffer&) = delete;
  DeviceBuffer& operator=(const DeviceBuffer&) = delete;

  T* data() { return data_; }
  const T* data() const { return data_; }
  std::size_t size() const { return size_; }

  void copy_from_host(const T* source, std::size_t count) {
    if (count > 0) {
      check_cuda(
          cudaMemcpy(data_, source, count * sizeof(T), cudaMemcpyHostToDevice),
          "cudaMemcpy H2D");
    }
  }

  void copy_to_host(T* destination, std::size_t count) const {
    if (count > 0) {
      check_cuda(cudaMemcpy(destination, data_, count * sizeof(T),
                            cudaMemcpyDeviceToHost),
                 "cudaMemcpy D2H");
    }
  }

  void zero() {
    if (size_ > 0) {
      check_cuda(cudaMemset(data_, 0, size_ * sizeof(T)), "cudaMemset");
    }
  }

private:
  T* data_ = nullptr;
  std::size_t size_ = 0;
};

template <typename T> class ManagedBuffer {
public:
  explicit ManagedBuffer(std::size_t size) : size_(size) {
    if (size_ > 0) {
      check_cuda(cudaMallocManaged(&data_, size_ * sizeof(T)),
                 "cudaMallocManaged");
    }
  }

  ~ManagedBuffer() {
    if (data_ != nullptr) {
      cudaFree(data_);
    }
  }

  ManagedBuffer(const ManagedBuffer&) = delete;
  ManagedBuffer& operator=(const ManagedBuffer&) = delete;

  T* data() { return data_; }
  const T* data() const { return data_; }
  std::size_t size() const { return size_; }

  void copy_from_host(const T* source, std::size_t count) {
    if (count > 0) {
      std::copy(source, source + count, data_);
    }
  }

  void zero() {
    if (size_ > 0) {
      std::fill(data_, data_ + size_, T{});
    }
  }

  void prefetch(int device) {
    if (size_ > 0) {
      check_cuda(cudaMemPrefetchAsync(data_, size_ * sizeof(T), device),
                 "cudaMemPrefetchAsync");
    }
  }

private:
  T* data_ = nullptr;
  std::size_t size_ = 0;
};

template <typename T> class MappedHostBuffer {
public:
  explicit MappedHostBuffer(std::size_t size) : size_(size) {
    if (size_ > 0) {
      check_cuda(
          cudaHostAlloc(&host_data_, size_ * sizeof(T), cudaHostAllocMapped),
          "cudaHostAlloc mapped");
      const cudaError_t status =
          cudaHostGetDevicePointer(&device_data_, host_data_, 0);
      if (status != cudaSuccess) {
        cudaFreeHost(host_data_);
        host_data_ = nullptr;
        check_cuda(status, "cudaHostGetDevicePointer");
      }
    }
  }

  ~MappedHostBuffer() {
    if (host_data_ != nullptr) {
      cudaFreeHost(host_data_);
    }
  }

  MappedHostBuffer(const MappedHostBuffer&) = delete;
  MappedHostBuffer& operator=(const MappedHostBuffer&) = delete;

  T* device_data() { return device_data_; }
  const T* device_data() const { return device_data_; }

  void copy_from_host(const T* source, std::size_t count) {
    if (count > 0) {
      std::copy(source, source + count, host_data_);
    }
  }

private:
  T* host_data_ = nullptr;
  T* device_data_ = nullptr;
  std::size_t size_ = 0;
};

template <typename ArrayType>
arrow::Result<std::shared_ptr<ArrayType>>
required_array(const arrow::RecordBatch& batch, const std::string& name,
               arrow::Type::type expected_type) {
  const auto array = batch.GetColumnByName(name);
  if (array == nullptr || array->type_id() != expected_type ||
      array->null_count() != 0) {
    return arrow::Status::Invalid("invalid Arrow lineitem column: ", name);
  }
  const auto typed = std::dynamic_pointer_cast<ArrayType>(array);
  if (typed == nullptr) {
    return arrow::Status::Invalid("unexpected Arrow array class: ", name);
  }
  return typed;
}

arrow::Result<std::shared_ptr<arrow::Decimal128Array>>
required_decimal(const arrow::RecordBatch& batch, const std::string& name) {
  ARROW_ASSIGN_OR_RAISE(const auto array,
                        required_array<arrow::Decimal128Array>(
                            batch, name, arrow::Type::DECIMAL128));
  if (!array->type()->Equals(arrow::decimal128(15, 2))) {
    return arrow::Status::Invalid("Arrow column ", name,
                                  " must be decimal128(15, 2)");
  }
  return array;
}

arrow::Result<ArrowGpuInput>
prepare_arrow_gpu_input(const ArrowQ5Dataset& dataset, const Q5Params& params) {
  if (dataset.lineitem == nullptr) {
    return arrow::Status::Invalid("missing Arrow lineitem table");
  }
  ARROW_ASSIGN_OR_RAISE(ArrowQ5Plan plan, build_arrow_q5_plan(dataset, params));
  if (dataset.lineitem->num_rows() < 0 ||
      static_cast<uint64_t>(dataset.lineitem->num_rows()) >
          std::numeric_limits<std::size_t>::max()) {
    return arrow::Status::CapacityError("lineitem row count exceeds size_t");
  }

  ArrowGpuInput input;
  input.plan = std::move(plan);
  const std::size_t row_count =
      static_cast<std::size_t>(dataset.lineitem->num_rows());
  input.order_keys.reserve(row_count);
  input.supplier_keys.reserve(row_count);
  input.price_cents.reserve(row_count);
  input.discount_hundredths.reserve(row_count);

  Stopwatch staging_timer;
  arrow::TableBatchReader reader(dataset.lineitem);
  while (true) {
    std::shared_ptr<arrow::RecordBatch> batch;
    ARROW_RETURN_NOT_OK(reader.ReadNext(&batch));
    if (batch == nullptr) {
      break;
    }
    ARROW_ASSIGN_OR_RAISE(const auto order_keys,
                          required_array<arrow::Int32Array>(
                              *batch, "l_orderkey", arrow::Type::INT32));
    ARROW_ASSIGN_OR_RAISE(const auto supplier_keys,
                          required_array<arrow::Int32Array>(
                              *batch, "l_suppkey", arrow::Type::INT32));
    ARROW_ASSIGN_OR_RAISE(const auto prices,
                          required_decimal(*batch, "l_extendedprice"));
    ARROW_ASSIGN_OR_RAISE(const auto discounts,
                          required_decimal(*batch, "l_discount"));
    for (int64_t row = 0; row < batch->num_rows(); ++row) {
      ARROW_ASSIGN_OR_RAISE(
          const int64_t price,
          arrow::Decimal128(prices->GetValue(row)).ToInteger<int64_t>());
      ARROW_ASSIGN_OR_RAISE(
          const int64_t discount,
          arrow::Decimal128(discounts->GetValue(row)).ToInteger<int64_t>());
      if (price < 0 || price > std::numeric_limits<int64_t>::max() / 100 ||
          discount < 0 || discount > 100) {
        return arrow::Status::Invalid("invalid Arrow Q5 decimal input");
      }
      input.order_keys.push_back(order_keys->Value(row));
      input.supplier_keys.push_back(supplier_keys->Value(row));
      input.price_cents.push_back(price);
      input.discount_hundredths.push_back(static_cast<int32_t>(discount));
    }
  }
  if (input.order_keys.size() != row_count ||
      input.supplier_keys.size() != row_count ||
      input.price_cents.size() != row_count ||
      input.discount_hundredths.size() != row_count) {
    return arrow::Status::Invalid(
        "Arrow lineitem columns have inconsistent rows");
  }
  input.build_ms = input.plan.build_ms + staging_timer.elapsed_ms();
  return input;
}

std::size_t checked_add(std::size_t left, std::size_t right) {
  if (left > std::numeric_limits<std::size_t>::max() - right) {
    throw std::overflow_error("CUDA byte count overflow");
  }
  return left + right;
}

std::size_t checked_multiply(std::size_t count, std::size_t width) {
  if (count > std::numeric_limits<std::size_t>::max() / width) {
    throw std::overflow_error("CUDA byte count overflow");
  }
  return count * width;
}

int64_t checked_counter_bytes(std::size_t bytes) {
  if (bytes > static_cast<std::size_t>(std::numeric_limits<int64_t>::max())) {
    throw std::overflow_error("CUDA byte count exceeds int64");
  }
  return static_cast<int64_t>(bytes);
}

int64_t input_bytes(const ArrowGpuInput& input) {
  std::size_t bytes = 0;
  bytes = checked_add(
      bytes, checked_multiply(input.order_keys.size(), sizeof(int32_t)));
  bytes = checked_add(
      bytes, checked_multiply(input.supplier_keys.size(), sizeof(int32_t)));
  bytes = checked_add(
      bytes, checked_multiply(input.price_cents.size(), sizeof(int64_t)));
  bytes = checked_add(bytes, checked_multiply(input.discount_hundredths.size(),
                                              sizeof(int32_t)));
  bytes =
      checked_add(bytes, checked_multiply(input.plan.order_nation_by_key.size(),
                                          sizeof(int32_t)));
  bytes = checked_add(bytes,
                      checked_multiply(input.plan.supplier_nation_by_key.size(),
                                       sizeof(int32_t)));
  return checked_counter_bytes(bytes);
}

int64_t logical_mapped_read_bytes(const ArrowGpuInput& input,
                                  std::size_t nation_count) {
  std::size_t bytes = 0;
  for (std::size_t row = 0; row < input.order_keys.size(); ++row) {
    bytes = checked_add(bytes, 2 * sizeof(int32_t));
    const int32_t order_key = input.order_keys[row];
    const int32_t supplier_key = input.supplier_keys[row];
    if (order_key < 0 ||
        static_cast<std::size_t>(order_key) >=
            input.plan.order_nation_by_key.size() ||
        supplier_key < 0 ||
        static_cast<std::size_t>(supplier_key) >=
            input.plan.supplier_nation_by_key.size()) {
      continue;
    }

    bytes = checked_add(bytes, 2 * sizeof(int32_t));
    const int32_t order_nation = input.plan.order_nation_by_key[order_key];
    const int32_t supplier_nation =
        input.plan.supplier_nation_by_key[supplier_key];
    if (order_nation < 0 || order_nation != supplier_nation ||
        static_cast<std::size_t>(order_nation) >= nation_count) {
      continue;
    }
    bytes = checked_add(bytes, sizeof(int64_t) + sizeof(int32_t));
  }
  return checked_counter_bytes(bytes);
}

int64_t output_bytes(std::size_t nation_count) {
  std::size_t bytes = checked_multiply(nation_count, sizeof(uint64_t));
  bytes = checked_add(bytes, sizeof(uint64_t));
  bytes = checked_add(bytes, sizeof(int32_t));
  return checked_counter_bytes(bytes);
}

arrow::Status validate_cuda_dimensions(const ArrowGpuInput& input,
                                       std::size_t nation_count) {
  if (input.order_keys.size() >
          static_cast<std::size_t>(std::numeric_limits<int64_t>::max()) ||
      input.plan.order_nation_by_key.size() >
          static_cast<std::size_t>(std::numeric_limits<int32_t>::max()) ||
      input.plan.supplier_nation_by_key.size() >
          static_cast<std::size_t>(std::numeric_limits<int32_t>::max()) ||
      nation_count >
          static_cast<std::size_t>(std::numeric_limits<int32_t>::max())) {
    return arrow::Status::CapacityError(
        "Arrow Q5 input exceeds CUDA index range");
  }
  constexpr std::size_t kThreads = 256;
  const std::size_t blocks =
      (input.order_keys.size() + kThreads - 1) / kThreads;
  if (blocks > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
    return arrow::Status::CapacityError(
        "Arrow Q5 input exceeds CUDA grid range");
  }
  return arrow::Status::OK();
}

__device__ void checked_atomic_add(unsigned long long* destination,
                                   unsigned long long value,
                                   int32_t* overflow) {
  unsigned long long current = atomicAdd(destination, 0ull);
  while (true) {
    if (current > static_cast<unsigned long long>(LLONG_MAX) - value) {
      atomicExch(overflow, 1);
      return;
    }
    const unsigned long long desired = current + value;
    const unsigned long long observed =
        atomicCAS(destination, current, desired);
    if (observed == current) {
      return;
    }
    current = observed;
  }
}

__global__ void
arrow_q5_kernel(const int32_t* order_keys, const int32_t* supplier_keys,
                const int64_t* price_cents, const int32_t* discount_hundredths,
                int64_t row_count, const int32_t* order_nation_by_key,
                int32_t order_map_size, const int32_t* supplier_nation_by_key,
                int32_t supplier_map_size,
                unsigned long long* revenue_by_nation, int32_t nation_count,
                unsigned long long* matched_rows, int32_t* overflow) {
  const int64_t row =
      static_cast<int64_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (row >= row_count) {
    return;
  }
  const int32_t order_key = order_keys[row];
  const int32_t supplier_key = supplier_keys[row];
  if (order_key < 0 || order_key >= order_map_size || supplier_key < 0 ||
      supplier_key >= supplier_map_size) {
    return;
  }
  const int32_t order_nation = order_nation_by_key[order_key];
  const int32_t supplier_nation = supplier_nation_by_key[supplier_key];
  if (order_nation < 0 || order_nation != supplier_nation ||
      order_nation >= nation_count) {
    return;
  }
  const unsigned long long revenue = static_cast<unsigned long long>(
      price_cents[row] * (100 - discount_hundredths[row]));
  atomicAdd(matched_rows, 1ull);
  checked_atomic_add(&revenue_by_nation[order_nation], revenue, overflow);
}

double launch_kernel(const ArrowGpuInput& input, const int32_t* order_keys,
                     const int32_t* supplier_keys, const int64_t* price_cents,
                     const int32_t* discount_hundredths,
                     const int32_t* order_map, const int32_t* supplier_map,
                     unsigned long long* revenue, std::size_t nation_count,
                     unsigned long long* matched, int32_t* overflow) {
  constexpr int kThreads = 256;
  const int blocks =
      static_cast<int>((input.order_keys.size() + kThreads - 1) / kThreads);
  CudaEvent start;
  CudaEvent stop;
  check_cuda(cudaEventRecord(start.get()), "cudaEventRecord kernel start");
  if (blocks > 0) {
    arrow_q5_kernel<<<blocks, kThreads>>>(
        order_keys, supplier_keys, price_cents, discount_hundredths,
        static_cast<int64_t>(input.order_keys.size()), order_map,
        static_cast<int32_t>(input.plan.order_nation_by_key.size()),
        supplier_map,
        static_cast<int32_t>(input.plan.supplier_nation_by_key.size()), revenue,
        static_cast<int32_t>(nation_count), matched, overflow);
    check_cuda(cudaGetLastError(), "arrow_q5_kernel launch");
  }
  check_cuda(cudaEventRecord(stop.get()), "cudaEventRecord kernel stop");
  return elapsed_ms(start, stop);
}

arrow::Result<Q5Result> finish_result(
    const ArrowGpuInput& input, const std::vector<unsigned long long>& revenue,
    unsigned long long matched_rows, int32_t overflow, Q5Result result,
    const Stopwatch& total_timer) {
  if (overflow != 0) {
    return arrow::Status::CapacityError("GPU Q5 revenue accumulation overflow");
  }
  for (std::size_t nation = 0; nation < revenue.size(); ++nation) {
    if (revenue[nation] == 0) {
      continue;
    }
    if (revenue[nation] >
        static_cast<unsigned long long>(std::numeric_limits<int64_t>::max())) {
      return arrow::Status::CapacityError("GPU Q5 revenue exceeds int64");
    }
    result.rows.push_back(Q5ResultRow{input.plan.nation_name_by_key[nation],
                                      static_cast<int64_t>(revenue[nation])});
  }
  std::sort(result.rows.begin(), result.rows.end(),
            [](const Q5ResultRow& left, const Q5ResultRow& right) {
              if (left.revenue_1e4 != right.revenue_1e4) {
                return left.revenue_1e4 > right.revenue_1e4;
              }
              return left.nation_name < right.nation_name;
            });
  result.counters.input_lineitem_rows =
      static_cast<int64_t>(input.order_keys.size());
  result.counters.matched_lineitem_rows = static_cast<int64_t>(matched_rows);
  result.counters.gpu_input_rows =
      static_cast<int64_t>(input.order_keys.size());
  result.timing.scan_ms =
      result.timing.h2d_ms + result.timing.kernel_ms + result.timing.d2h_ms;
  result.timing.total_ms = total_timer.elapsed_ms();
  return result;
}

}  // namespace

template <typename Function>
auto arrow_cuda_status_boundary(Function&& function) -> decltype(function()) {
  try {
    return function();
  } catch (const std::bad_alloc&) {
    return arrow::Status::CapacityError("Arrow CUDA Q5 allocation failed");
  } catch (const CudaCapacityError& error) {
    return arrow::Status::CapacityError("Arrow CUDA Q5 failed: ", error.what());
  } catch (const std::length_error& error) {
    return arrow::Status::CapacityError("Arrow CUDA Q5 failed: ", error.what());
  } catch (const std::overflow_error& error) {
    return arrow::Status::CapacityError("Arrow CUDA Q5 failed: ", error.what());
  } catch (const std::exception& error) {
    return arrow::Status::IOError("Arrow CUDA Q5 failed: ", error.what());
  }
}

struct ArrowCudaQ5Session::Impl {
  ArrowGpuInput input;
  const std::size_t nation_count;
  const ArrowCudaMemoryMode mode;
  int device = 0;
  Q5SessionSetup setup;
  int64_t initial_h2d_bytes = 0;
  bool outputs_clean = true;
  bool managed_output_on_device = false;

  std::unique_ptr<DeviceBuffer<int32_t>> copy_order_keys;
  std::unique_ptr<DeviceBuffer<int32_t>> copy_supplier_keys;
  std::unique_ptr<DeviceBuffer<int64_t>> copy_prices;
  std::unique_ptr<DeviceBuffer<int32_t>> copy_discounts;
  std::unique_ptr<DeviceBuffer<int32_t>> copy_order_map;
  std::unique_ptr<DeviceBuffer<int32_t>> copy_supplier_map;

  std::unique_ptr<ManagedBuffer<int32_t>> managed_order_keys;
  std::unique_ptr<ManagedBuffer<int32_t>> managed_supplier_keys;
  std::unique_ptr<ManagedBuffer<int64_t>> managed_prices;
  std::unique_ptr<ManagedBuffer<int32_t>> managed_discounts;
  std::unique_ptr<ManagedBuffer<int32_t>> managed_order_map;
  std::unique_ptr<ManagedBuffer<int32_t>> managed_supplier_map;

  std::unique_ptr<MappedHostBuffer<int32_t>> mapped_order_keys;
  std::unique_ptr<MappedHostBuffer<int32_t>> mapped_supplier_keys;
  std::unique_ptr<MappedHostBuffer<int64_t>> mapped_prices;
  std::unique_ptr<MappedHostBuffer<int32_t>> mapped_discounts;
  std::unique_ptr<MappedHostBuffer<int32_t>> mapped_order_map;
  std::unique_ptr<MappedHostBuffer<int32_t>> mapped_supplier_map;

  std::unique_ptr<DeviceBuffer<unsigned long long>> device_revenue;
  std::unique_ptr<DeviceBuffer<unsigned long long>> device_matched;
  std::unique_ptr<DeviceBuffer<int32_t>> device_overflow;
  std::unique_ptr<ManagedBuffer<unsigned long long>> managed_revenue;
  std::unique_ptr<ManagedBuffer<unsigned long long>> managed_matched;
  std::unique_ptr<ManagedBuffer<int32_t>> managed_overflow;

  std::vector<unsigned long long> host_revenue;
  unsigned long long host_matched = 0;
  int32_t host_overflow = 0;

  Impl(ArrowGpuInput prepared, std::size_t nations,
       ArrowCudaMemoryMode memory_mode)
      : input(std::move(prepared)),
        nation_count(nations),
        mode(memory_mode),
        host_revenue(nations, 0) {}

  void allocate() {
    if (mode == ArrowCudaMemoryMode::kCopy) {
      allocate_copy();
      return;
    }
    if (mode == ArrowCudaMemoryMode::kManaged) {
      allocate_managed();
      return;
    }
    allocate_mapped();
  }

  void initialize() {
    if (mode == ArrowCudaMemoryMode::kCopy) {
      initialize_copy();
      return;
    }
    if (mode == ArrowCudaMemoryMode::kManaged) {
      initialize_managed();
      return;
    }
    initialize_mapped();
  }

  void reset_output_buffers() {
    if (outputs_clean) {
      return;
    }
    if (mode == ArrowCudaMemoryMode::kManaged) {
      zero_managed_outputs();
      managed_output_on_device = false;
    } else {
      zero_device_outputs();
    }
    outputs_clean = true;
  }

  double prefetch_managed_output_to_device_if_needed() {
    if (mode != ArrowCudaMemoryMode::kManaged || managed_output_on_device) {
      return 0.0;
    }
    CudaEvent start;
    CudaEvent stop;
    check_cuda(cudaEventRecord(start.get()),
               "cudaEventRecord managed output prefetch start");
    prefetch_managed_outputs(device);
    check_cuda(cudaEventRecord(stop.get()),
               "cudaEventRecord managed output prefetch stop");
    managed_output_on_device = true;
    return elapsed_ms(start, stop);
  }

  double launch_existing_kernel() {
    outputs_clean = false;
    return launch_kernel(
        input, order_keys_data(), supplier_keys_data(), prices_data(),
        discounts_data(), order_map_data(), supplier_map_data(), revenue_data(),
        nation_count, matched_data(), overflow_data());
  }

  double collect_small_output() {
    CudaEvent start;
    CudaEvent stop;
    check_cuda(cudaEventRecord(start.get()), "cudaEventRecord D2H start");
    if (mode == ArrowCudaMemoryMode::kManaged) {
      prefetch_managed_outputs(cudaCpuDeviceId);
    } else {
      device_revenue->copy_to_host(host_revenue.data(), host_revenue.size());
      device_matched->copy_to_host(&host_matched, 1);
      device_overflow->copy_to_host(&host_overflow, 1);
    }
    check_cuda(cudaEventRecord(stop.get()), "cudaEventRecord D2H stop");
    const double d2h_ms = elapsed_ms(start, stop);
    if (mode == ArrowCudaMemoryMode::kManaged) {
      if (nation_count > 0) {
        std::copy(managed_revenue->data(),
                  managed_revenue->data() + nation_count, host_revenue.begin());
      }
      host_matched = managed_matched->data()[0];
      host_overflow = managed_overflow->data()[0];
      managed_output_on_device = false;
    }
    return d2h_ms;
  }

  arrow::Result<Q5Result> finish_result_for_request(
      double h2d_ms, double kernel_ms, double d2h_ms,
      const Stopwatch& total_timer) {
    Q5Result result;
    result.timing.h2d_ms = h2d_ms;
    result.timing.kernel_ms = kernel_ms;
    result.timing.d2h_ms = d2h_ms;
    result.counters.d2h_bytes = output_bytes(nation_count);
    if (mode == ArrowCudaMemoryMode::kMapped) {
      result.counters.mapped_remote_read_bytes =
          logical_mapped_read_bytes(input, nation_count);
    }
    return finish_result(input, host_revenue, host_matched, host_overflow,
                         std::move(result), total_timer);
  }

  void fold_setup_into_cold_result(Q5Result* result) const {
    result->timing.build_ms = setup.plan_build_ms + setup.host_staging_ms;
    result->timing.h2d_ms += setup.initial_h2d_ms;
    result->timing.scan_ms = result->timing.h2d_ms + result->timing.kernel_ms +
                             result->timing.d2h_ms;
    result->timing.total_ms += setup.total_ms;
    result->counters.h2d_bytes += initial_h2d_bytes;
  }

 private:
  void allocate_copy() {
    copy_order_keys =
        std::make_unique<DeviceBuffer<int32_t>>(input.order_keys.size());
    copy_supplier_keys =
        std::make_unique<DeviceBuffer<int32_t>>(input.supplier_keys.size());
    copy_prices = std::make_unique<DeviceBuffer<int64_t>>(input.price_cents.size());
    copy_discounts = std::make_unique<DeviceBuffer<int32_t>>(
        input.discount_hundredths.size());
    copy_order_map = std::make_unique<DeviceBuffer<int32_t>>(
        input.plan.order_nation_by_key.size());
    copy_supplier_map = std::make_unique<DeviceBuffer<int32_t>>(
        input.plan.supplier_nation_by_key.size());
    allocate_device_outputs();
  }

  void allocate_managed() {
    check_cuda(cudaGetDevice(&device), "cudaGetDevice");
    managed_order_keys =
        std::make_unique<ManagedBuffer<int32_t>>(input.order_keys.size());
    managed_supplier_keys =
        std::make_unique<ManagedBuffer<int32_t>>(input.supplier_keys.size());
    managed_prices =
        std::make_unique<ManagedBuffer<int64_t>>(input.price_cents.size());
    managed_discounts = std::make_unique<ManagedBuffer<int32_t>>(
        input.discount_hundredths.size());
    managed_order_map = std::make_unique<ManagedBuffer<int32_t>>(
        input.plan.order_nation_by_key.size());
    managed_supplier_map = std::make_unique<ManagedBuffer<int32_t>>(
        input.plan.supplier_nation_by_key.size());
    managed_revenue =
        std::make_unique<ManagedBuffer<unsigned long long>>(nation_count);
    managed_matched = std::make_unique<ManagedBuffer<unsigned long long>>(1);
    managed_overflow = std::make_unique<ManagedBuffer<int32_t>>(1);
  }

  void allocate_mapped() {
    mapped_order_keys =
        std::make_unique<MappedHostBuffer<int32_t>>(input.order_keys.size());
    mapped_supplier_keys = std::make_unique<MappedHostBuffer<int32_t>>(
        input.supplier_keys.size());
    mapped_prices =
        std::make_unique<MappedHostBuffer<int64_t>>(input.price_cents.size());
    mapped_discounts = std::make_unique<MappedHostBuffer<int32_t>>(
        input.discount_hundredths.size());
    mapped_order_map = std::make_unique<MappedHostBuffer<int32_t>>(
        input.plan.order_nation_by_key.size());
    mapped_supplier_map = std::make_unique<MappedHostBuffer<int32_t>>(
        input.plan.supplier_nation_by_key.size());
    allocate_device_outputs();
  }

  void allocate_device_outputs() {
    device_revenue =
        std::make_unique<DeviceBuffer<unsigned long long>>(nation_count);
    device_matched = std::make_unique<DeviceBuffer<unsigned long long>>(1);
    device_overflow = std::make_unique<DeviceBuffer<int32_t>>(1);
  }

  void initialize_copy() {
    CudaEvent start;
    CudaEvent stop;
    check_cuda(cudaEventRecord(start.get()), "cudaEventRecord H2D start");
    copy_order_keys->copy_from_host(input.order_keys.data(),
                                    input.order_keys.size());
    copy_supplier_keys->copy_from_host(input.supplier_keys.data(),
                                       input.supplier_keys.size());
    copy_prices->copy_from_host(input.price_cents.data(),
                                input.price_cents.size());
    copy_discounts->copy_from_host(input.discount_hundredths.data(),
                                   input.discount_hundredths.size());
    copy_order_map->copy_from_host(input.plan.order_nation_by_key.data(),
                                   input.plan.order_nation_by_key.size());
    copy_supplier_map->copy_from_host(input.plan.supplier_nation_by_key.data(),
                                      input.plan.supplier_nation_by_key.size());
    zero_device_outputs();
    check_cuda(cudaEventRecord(stop.get()), "cudaEventRecord H2D stop");
    setup.initial_h2d_ms = elapsed_ms(start, stop);
    initial_h2d_bytes = input_bytes(input);
  }

  void initialize_managed() {
    Stopwatch staging_timer;
    managed_order_keys->copy_from_host(input.order_keys.data(),
                                       input.order_keys.size());
    managed_supplier_keys->copy_from_host(input.supplier_keys.data(),
                                          input.supplier_keys.size());
    managed_prices->copy_from_host(input.price_cents.data(),
                                   input.price_cents.size());
    managed_discounts->copy_from_host(input.discount_hundredths.data(),
                                      input.discount_hundredths.size());
    managed_order_map->copy_from_host(input.plan.order_nation_by_key.data(),
                                      input.plan.order_nation_by_key.size());
    managed_supplier_map->copy_from_host(input.plan.supplier_nation_by_key.data(),
                                         input.plan.supplier_nation_by_key.size());
    zero_managed_outputs();
    setup.host_staging_ms += staging_timer.elapsed_ms();

    CudaEvent start;
    CudaEvent stop;
    check_cuda(cudaEventRecord(start.get()),
               "cudaEventRecord managed prefetch start");
    prefetch_managed_inputs(device);
    prefetch_managed_outputs(device);
    check_cuda(cudaEventRecord(stop.get()),
               "cudaEventRecord managed prefetch stop");
    setup.initial_h2d_ms = elapsed_ms(start, stop);
    initial_h2d_bytes = checked_counter_bytes(
        checked_add(static_cast<std::size_t>(input_bytes(input)),
                    static_cast<std::size_t>(output_bytes(nation_count))));
    managed_output_on_device = true;
  }

  void initialize_mapped() {
    Stopwatch staging_timer;
    mapped_order_keys->copy_from_host(input.order_keys.data(),
                                      input.order_keys.size());
    mapped_supplier_keys->copy_from_host(input.supplier_keys.data(),
                                         input.supplier_keys.size());
    mapped_prices->copy_from_host(input.price_cents.data(),
                                  input.price_cents.size());
    mapped_discounts->copy_from_host(input.discount_hundredths.data(),
                                     input.discount_hundredths.size());
    mapped_order_map->copy_from_host(input.plan.order_nation_by_key.data(),
                                     input.plan.order_nation_by_key.size());
    mapped_supplier_map->copy_from_host(input.plan.supplier_nation_by_key.data(),
                                        input.plan.supplier_nation_by_key.size());
    zero_device_outputs();
    setup.host_staging_ms += staging_timer.elapsed_ms();
  }

  const int32_t* order_keys_data() const {
    if (mode == ArrowCudaMemoryMode::kCopy) {
      return copy_order_keys->data();
    }
    if (mode == ArrowCudaMemoryMode::kManaged) {
      return managed_order_keys->data();
    }
    return mapped_order_keys->device_data();
  }

  const int32_t* supplier_keys_data() const {
    if (mode == ArrowCudaMemoryMode::kCopy) {
      return copy_supplier_keys->data();
    }
    if (mode == ArrowCudaMemoryMode::kManaged) {
      return managed_supplier_keys->data();
    }
    return mapped_supplier_keys->device_data();
  }

  const int64_t* prices_data() const {
    if (mode == ArrowCudaMemoryMode::kCopy) {
      return copy_prices->data();
    }
    if (mode == ArrowCudaMemoryMode::kManaged) {
      return managed_prices->data();
    }
    return mapped_prices->device_data();
  }

  const int32_t* discounts_data() const {
    if (mode == ArrowCudaMemoryMode::kCopy) {
      return copy_discounts->data();
    }
    if (mode == ArrowCudaMemoryMode::kManaged) {
      return managed_discounts->data();
    }
    return mapped_discounts->device_data();
  }

  const int32_t* order_map_data() const {
    if (mode == ArrowCudaMemoryMode::kCopy) {
      return copy_order_map->data();
    }
    if (mode == ArrowCudaMemoryMode::kManaged) {
      return managed_order_map->data();
    }
    return mapped_order_map->device_data();
  }

  const int32_t* supplier_map_data() const {
    if (mode == ArrowCudaMemoryMode::kCopy) {
      return copy_supplier_map->data();
    }
    if (mode == ArrowCudaMemoryMode::kManaged) {
      return managed_supplier_map->data();
    }
    return mapped_supplier_map->device_data();
  }

  unsigned long long* revenue_data() {
    return mode == ArrowCudaMemoryMode::kManaged ? managed_revenue->data()
                                                   : device_revenue->data();
  }

  unsigned long long* matched_data() {
    return mode == ArrowCudaMemoryMode::kManaged ? managed_matched->data()
                                                   : device_matched->data();
  }

  int32_t* overflow_data() {
    return mode == ArrowCudaMemoryMode::kManaged ? managed_overflow->data()
                                                   : device_overflow->data();
  }

  void zero_device_outputs() {
    device_revenue->zero();
    device_matched->zero();
    device_overflow->zero();
  }

  void zero_managed_outputs() {
    managed_revenue->zero();
    managed_matched->zero();
    managed_overflow->zero();
  }

  void prefetch_managed_inputs(int location) {
    managed_order_keys->prefetch(location);
    managed_supplier_keys->prefetch(location);
    managed_prices->prefetch(location);
    managed_discounts->prefetch(location);
    managed_order_map->prefetch(location);
    managed_supplier_map->prefetch(location);
  }

  void prefetch_managed_outputs(int location) {
    managed_revenue->prefetch(location);
    managed_matched->prefetch(location);
    managed_overflow->prefetch(location);
  }
};

ArrowCudaQ5Session::ArrowCudaQ5Session(std::unique_ptr<Impl> impl)
    : impl_(std::move(impl)) {}

ArrowCudaQ5Session::~ArrowCudaQ5Session() = default;

arrow::Result<std::unique_ptr<ArrowCudaQ5Session>> ArrowCudaQ5Session::Make(
    const ArrowQ5Dataset& dataset, const Q5Params& params,
    ArrowCudaMemoryMode mode) {
  return arrow_cuda_status_boundary(
      [&]() -> arrow::Result<std::unique_ptr<ArrowCudaQ5Session>> {
        Stopwatch setup_timer;
        ARROW_ASSIGN_OR_RAISE(ArrowGpuInput input,
                              prepare_arrow_gpu_input(dataset, params));
        const std::size_t nation_count =
            input.plan.max_nation_key < 0
                ? 0
                : static_cast<std::size_t>(input.plan.max_nation_key) + 1;
        ARROW_RETURN_NOT_OK(validate_cuda_dimensions(input, nation_count));

        Q5SessionSetup setup;
        setup.plan_build_ms = input.plan.build_ms;
        setup.host_staging_ms = input.build_ms - setup.plan_build_ms;
        const int64_t gpu_input_bytes = input_bytes(input);
        const int64_t output_size_bytes = output_bytes(nation_count);

        Stopwatch allocation_timer;
        auto impl = std::unique_ptr<Impl>(
            new Impl(std::move(input), nation_count, mode));
        impl->allocate();
        setup.allocation_ms = allocation_timer.elapsed_ms();

        impl->setup = setup;
        impl->initialize();
        impl->setup.resident_host_bytes = checked_counter_bytes(checked_add(
            static_cast<std::size_t>(gpu_input_bytes),
            static_cast<std::size_t>(output_size_bytes)));
        if (mode == ArrowCudaMemoryMode::kMapped) {
          impl->setup.resident_gpu_bytes = output_size_bytes;
          impl->setup.resident_pinned_bytes = gpu_input_bytes;
        } else {
          impl->setup.resident_gpu_bytes = checked_counter_bytes(checked_add(
              static_cast<std::size_t>(gpu_input_bytes),
              static_cast<std::size_t>(output_size_bytes)));
        }
        impl->setup.total_ms = setup_timer.elapsed_ms();
        return std::unique_ptr<ArrowCudaQ5Session>(
            new ArrowCudaQ5Session(std::move(impl)));
      });
}

arrow::Result<Q5Result> ArrowCudaQ5Session::Execute() {
  return arrow_cuda_status_boundary([&]() -> arrow::Result<Q5Result> {
    Stopwatch total_timer;
    impl_->reset_output_buffers();
    const double h2d_ms = impl_->prefetch_managed_output_to_device_if_needed();
    const double kernel_ms = impl_->launch_existing_kernel();
    const double d2h_ms = impl_->collect_small_output();
    return impl_->finish_result_for_request(h2d_ms, kernel_ms, d2h_ms,
                                             total_timer);
  });
}

const Q5SessionSetup& ArrowCudaQ5Session::setup() const { return impl_->setup; }

arrow::Result<Q5Result> execute_q5_arrow_gpu_copy(const ArrowQ5Dataset& dataset,
                                                  const Q5Params& params) {
  ARROW_ASSIGN_OR_RAISE(auto session,
                        ArrowCudaQ5Session::Make(dataset, params,
                                                 ArrowCudaMemoryMode::kCopy));
  ARROW_ASSIGN_OR_RAISE(Q5Result result, session->Execute());
  session->impl_->fold_setup_into_cold_result(&result);
  return result;
}

arrow::Result<Q5Result>
execute_q5_arrow_gpu_managed(const ArrowQ5Dataset& dataset,
                             const Q5Params& params) {
  ARROW_ASSIGN_OR_RAISE(
      auto session,
      ArrowCudaQ5Session::Make(dataset, params, ArrowCudaMemoryMode::kManaged));
  ARROW_ASSIGN_OR_RAISE(Q5Result result, session->Execute());
  session->impl_->fold_setup_into_cold_result(&result);
  return result;
}

arrow::Result<Q5Result>
execute_q5_arrow_gpu_mapped(const ArrowQ5Dataset& dataset,
                            const Q5Params& params) {
  ARROW_ASSIGN_OR_RAISE(
      auto session,
      ArrowCudaQ5Session::Make(dataset, params, ArrowCudaMemoryMode::kMapped));
  ARROW_ASSIGN_OR_RAISE(Q5Result result, session->Execute());
  session->impl_->fold_setup_into_cold_result(&result);
  return result;
}

}  // namespace memq5

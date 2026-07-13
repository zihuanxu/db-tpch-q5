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

enum class MemoryMode { kCopy, kManaged, kMapped };

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

arrow::Result<Q5Result> finish_result(const ArrowGpuInput& input,
                                      std::vector<unsigned long long> revenue,
                                      unsigned long long matched_rows,
                                      int32_t overflow, Q5Result result,
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

arrow::Result<Q5Result> execute_copy(const ArrowQ5Dataset& dataset,
                                     const Q5Params& params) {
  Stopwatch total_timer;
  ARROW_ASSIGN_OR_RAISE(const ArrowGpuInput input,
                        prepare_arrow_gpu_input(dataset, params));
  const std::size_t nation_count =
      input.plan.max_nation_key < 0
          ? 0
          : static_cast<std::size_t>(input.plan.max_nation_key) + 1;
  ARROW_RETURN_NOT_OK(validate_cuda_dimensions(input, nation_count));

  DeviceBuffer<int32_t> order_keys(input.order_keys.size());
  DeviceBuffer<int32_t> supplier_keys(input.supplier_keys.size());
  DeviceBuffer<int64_t> prices(input.price_cents.size());
  DeviceBuffer<int32_t> discounts(input.discount_hundredths.size());
  DeviceBuffer<int32_t> order_map(input.plan.order_nation_by_key.size());
  DeviceBuffer<int32_t> supplier_map(input.plan.supplier_nation_by_key.size());
  DeviceBuffer<unsigned long long> revenue(nation_count);
  DeviceBuffer<unsigned long long> matched(1);
  DeviceBuffer<int32_t> overflow(1);

  Q5Result result;
  result.timing.build_ms = input.build_ms;
  CudaEvent h2d_start;
  CudaEvent h2d_stop;
  check_cuda(cudaEventRecord(h2d_start.get()), "cudaEventRecord H2D start");
  order_keys.copy_from_host(input.order_keys.data(), input.order_keys.size());
  supplier_keys.copy_from_host(input.supplier_keys.data(),
                               input.supplier_keys.size());
  prices.copy_from_host(input.price_cents.data(), input.price_cents.size());
  discounts.copy_from_host(input.discount_hundredths.data(),
                           input.discount_hundredths.size());
  order_map.copy_from_host(input.plan.order_nation_by_key.data(),
                           input.plan.order_nation_by_key.size());
  supplier_map.copy_from_host(input.plan.supplier_nation_by_key.data(),
                              input.plan.supplier_nation_by_key.size());
  revenue.zero();
  matched.zero();
  overflow.zero();
  check_cuda(cudaEventRecord(h2d_stop.get()), "cudaEventRecord H2D stop");
  result.timing.h2d_ms = elapsed_ms(h2d_start, h2d_stop);

  result.timing.kernel_ms = launch_kernel(
      input, order_keys.data(), supplier_keys.data(), prices.data(),
      discounts.data(), order_map.data(), supplier_map.data(), revenue.data(),
      nation_count, matched.data(), overflow.data());

  std::vector<unsigned long long> host_revenue(nation_count, 0);
  unsigned long long host_matched = 0;
  int32_t host_overflow = 0;
  CudaEvent d2h_start;
  CudaEvent d2h_stop;
  check_cuda(cudaEventRecord(d2h_start.get()), "cudaEventRecord D2H start");
  revenue.copy_to_host(host_revenue.data(), host_revenue.size());
  matched.copy_to_host(&host_matched, 1);
  overflow.copy_to_host(&host_overflow, 1);
  check_cuda(cudaEventRecord(d2h_stop.get()), "cudaEventRecord D2H stop");
  result.timing.d2h_ms = elapsed_ms(d2h_start, d2h_stop);
  result.counters.h2d_bytes = input_bytes(input);
  result.counters.d2h_bytes = output_bytes(nation_count);
  return finish_result(input, std::move(host_revenue), host_matched,
                       host_overflow, std::move(result), total_timer);
}

arrow::Result<Q5Result> execute_managed(const ArrowQ5Dataset& dataset,
                                        const Q5Params& params) {
  Stopwatch total_timer;
  ARROW_ASSIGN_OR_RAISE(const ArrowGpuInput input,
                        prepare_arrow_gpu_input(dataset, params));
  const std::size_t nation_count =
      input.plan.max_nation_key < 0
          ? 0
          : static_cast<std::size_t>(input.plan.max_nation_key) + 1;
  ARROW_RETURN_NOT_OK(validate_cuda_dimensions(input, nation_count));
  int device = 0;
  check_cuda(cudaGetDevice(&device), "cudaGetDevice");

  ManagedBuffer<int32_t> order_keys(input.order_keys.size());
  ManagedBuffer<int32_t> supplier_keys(input.supplier_keys.size());
  ManagedBuffer<int64_t> prices(input.price_cents.size());
  ManagedBuffer<int32_t> discounts(input.discount_hundredths.size());
  ManagedBuffer<int32_t> order_map(input.plan.order_nation_by_key.size());
  ManagedBuffer<int32_t> supplier_map(input.plan.supplier_nation_by_key.size());
  ManagedBuffer<unsigned long long> revenue(nation_count);
  ManagedBuffer<unsigned long long> matched(1);
  ManagedBuffer<int32_t> overflow(1);

  Stopwatch managed_stage;
  order_keys.copy_from_host(input.order_keys.data(), input.order_keys.size());
  supplier_keys.copy_from_host(input.supplier_keys.data(),
                               input.supplier_keys.size());
  prices.copy_from_host(input.price_cents.data(), input.price_cents.size());
  discounts.copy_from_host(input.discount_hundredths.data(),
                           input.discount_hundredths.size());
  order_map.copy_from_host(input.plan.order_nation_by_key.data(),
                           input.plan.order_nation_by_key.size());
  supplier_map.copy_from_host(input.plan.supplier_nation_by_key.data(),
                              input.plan.supplier_nation_by_key.size());
  revenue.zero();
  matched.zero();
  overflow.zero();

  Q5Result result;
  result.timing.build_ms = input.build_ms + managed_stage.elapsed_ms();
  CudaEvent h2d_start;
  CudaEvent h2d_stop;
  check_cuda(cudaEventRecord(h2d_start.get()),
             "cudaEventRecord managed prefetch start");
  order_keys.prefetch(device);
  supplier_keys.prefetch(device);
  prices.prefetch(device);
  discounts.prefetch(device);
  order_map.prefetch(device);
  supplier_map.prefetch(device);
  revenue.prefetch(device);
  matched.prefetch(device);
  overflow.prefetch(device);
  check_cuda(cudaEventRecord(h2d_stop.get()),
             "cudaEventRecord managed prefetch stop");
  result.timing.h2d_ms = elapsed_ms(h2d_start, h2d_stop);

  result.timing.kernel_ms = launch_kernel(
      input, order_keys.data(), supplier_keys.data(), prices.data(),
      discounts.data(), order_map.data(), supplier_map.data(), revenue.data(),
      nation_count, matched.data(), overflow.data());

  CudaEvent d2h_start;
  CudaEvent d2h_stop;
  check_cuda(cudaEventRecord(d2h_start.get()),
             "cudaEventRecord managed CPU prefetch start");
  revenue.prefetch(cudaCpuDeviceId);
  matched.prefetch(cudaCpuDeviceId);
  overflow.prefetch(cudaCpuDeviceId);
  check_cuda(cudaEventRecord(d2h_stop.get()),
             "cudaEventRecord managed CPU prefetch stop");
  result.timing.d2h_ms = elapsed_ms(d2h_start, d2h_stop);

  std::vector<unsigned long long> host_revenue(nation_count, 0);
  std::copy(revenue.data(), revenue.data() + nation_count,
            host_revenue.begin());
  result.counters.h2d_bytes = checked_counter_bytes(
      checked_add(static_cast<std::size_t>(input_bytes(input)),
                  static_cast<std::size_t>(output_bytes(nation_count))));
  result.counters.d2h_bytes = output_bytes(nation_count);
  return finish_result(input, std::move(host_revenue), matched.data()[0],
                       overflow.data()[0], std::move(result), total_timer);
}

arrow::Result<Q5Result> execute_mapped(const ArrowQ5Dataset& dataset,
                                       const Q5Params& params) {
  Stopwatch total_timer;
  ARROW_ASSIGN_OR_RAISE(const ArrowGpuInput input,
                        prepare_arrow_gpu_input(dataset, params));
  const std::size_t nation_count =
      input.plan.max_nation_key < 0
          ? 0
          : static_cast<std::size_t>(input.plan.max_nation_key) + 1;
  ARROW_RETURN_NOT_OK(validate_cuda_dimensions(input, nation_count));

  MappedHostBuffer<int32_t> order_keys(input.order_keys.size());
  MappedHostBuffer<int32_t> supplier_keys(input.supplier_keys.size());
  MappedHostBuffer<int64_t> prices(input.price_cents.size());
  MappedHostBuffer<int32_t> discounts(input.discount_hundredths.size());
  MappedHostBuffer<int32_t> order_map(input.plan.order_nation_by_key.size());
  MappedHostBuffer<int32_t> supplier_map(
      input.plan.supplier_nation_by_key.size());
  DeviceBuffer<unsigned long long> revenue(nation_count);
  DeviceBuffer<unsigned long long> matched(1);
  DeviceBuffer<int32_t> overflow(1);

  Stopwatch mapped_stage;
  order_keys.copy_from_host(input.order_keys.data(), input.order_keys.size());
  supplier_keys.copy_from_host(input.supplier_keys.data(),
                               input.supplier_keys.size());
  prices.copy_from_host(input.price_cents.data(), input.price_cents.size());
  discounts.copy_from_host(input.discount_hundredths.data(),
                           input.discount_hundredths.size());
  order_map.copy_from_host(input.plan.order_nation_by_key.data(),
                           input.plan.order_nation_by_key.size());
  supplier_map.copy_from_host(input.plan.supplier_nation_by_key.data(),
                              input.plan.supplier_nation_by_key.size());
  revenue.zero();
  matched.zero();
  overflow.zero();

  Q5Result result;
  result.timing.build_ms = input.build_ms + mapped_stage.elapsed_ms();
  result.timing.kernel_ms = launch_kernel(
      input, order_keys.device_data(), supplier_keys.device_data(),
      prices.device_data(), discounts.device_data(), order_map.device_data(),
      supplier_map.device_data(), revenue.data(), nation_count, matched.data(),
      overflow.data());

  std::vector<unsigned long long> host_revenue(nation_count, 0);
  unsigned long long host_matched = 0;
  int32_t host_overflow = 0;
  CudaEvent d2h_start;
  CudaEvent d2h_stop;
  check_cuda(cudaEventRecord(d2h_start.get()),
             "cudaEventRecord mapped D2H start");
  revenue.copy_to_host(host_revenue.data(), host_revenue.size());
  matched.copy_to_host(&host_matched, 1);
  overflow.copy_to_host(&host_overflow, 1);
  check_cuda(cudaEventRecord(d2h_stop.get()),
             "cudaEventRecord mapped D2H stop");
  result.timing.d2h_ms = elapsed_ms(d2h_start, d2h_stop);
  result.counters.d2h_bytes = output_bytes(nation_count);
  result.counters.mapped_remote_read_bytes =
      logical_mapped_read_bytes(input, nation_count);
  return finish_result(input, std::move(host_revenue), host_matched,
                       host_overflow, std::move(result), total_timer);
}

arrow::Result<Q5Result> execute_mode(const ArrowQ5Dataset& dataset,
                                     const Q5Params& params, MemoryMode mode) {
  try {
    if (mode == MemoryMode::kCopy) {
      return execute_copy(dataset, params);
    }
    if (mode == MemoryMode::kManaged) {
      return execute_managed(dataset, params);
    }
    return execute_mapped(dataset, params);
  } catch (const std::bad_alloc&) {
    return arrow::Status::CapacityError("Arrow CUDA Q5 allocation failed");
  } catch (const CudaCapacityError& error) {
    return arrow::Status::CapacityError("Arrow CUDA Q5 failed: ", error.what());
  } catch (const std::overflow_error& error) {
    return arrow::Status::CapacityError("Arrow CUDA Q5 failed: ", error.what());
  } catch (const std::exception& error) {
    return arrow::Status::IOError("Arrow CUDA Q5 failed: ", error.what());
  }
}

}  // namespace

arrow::Result<Q5Result> execute_q5_arrow_gpu_copy(const ArrowQ5Dataset& dataset,
                                                  const Q5Params& params) {
  return execute_mode(dataset, params, MemoryMode::kCopy);
}

arrow::Result<Q5Result>
execute_q5_arrow_gpu_managed(const ArrowQ5Dataset& dataset,
                             const Q5Params& params) {
  return execute_mode(dataset, params, MemoryMode::kManaged);
}

arrow::Result<Q5Result>
execute_q5_arrow_gpu_mapped(const ArrowQ5Dataset& dataset,
                            const Q5Params& params) {
  return execute_mode(dataset, params, MemoryMode::kMapped);
}

}  // namespace memq5

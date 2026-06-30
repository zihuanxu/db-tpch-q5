#include "cuda/q5_cuda.hpp"

#include <cuda_runtime.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

#include "common/fixed_point.hpp"
#include "common/timer.hpp"
#include "engine/q5_plan.hpp"

namespace memq5 {
namespace {

void check_cuda(cudaError_t status, const char* context) {
  if (status != cudaSuccess) {
    throw std::runtime_error(std::string(context) + ": " +
                             cudaGetErrorString(status));
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

float elapsed_ms(const CudaEvent& start, const CudaEvent& stop) {
  check_cuda(cudaEventSynchronize(stop.get()), "cudaEventSynchronize");
  float ms = 0.0f;
  check_cuda(cudaEventElapsedTime(&ms, start.get(), stop.get()),
             "cudaEventElapsedTime");
  return ms;
}

template <class T>
class DeviceBuffer {
 public:
  explicit DeviceBuffer(std::size_t size) : size_(size) {
    if (size_ > 0) {
      check_cuda(cudaMalloc(&ptr_, size_ * sizeof(T)), "cudaMalloc");
    }
  }

  ~DeviceBuffer() {
    if (ptr_ != nullptr) {
      cudaFree(ptr_);
    }
  }

  DeviceBuffer(const DeviceBuffer&) = delete;
  DeviceBuffer& operator=(const DeviceBuffer&) = delete;

  T* data() { return ptr_; }
  const T* data() const { return ptr_; }
  std::size_t size() const { return size_; }

  void copy_from_host(const T* src, std::size_t count) {
    if (count == 0) {
      return;
    }
    check_cuda(cudaMemcpy(ptr_, src, count * sizeof(T), cudaMemcpyHostToDevice),
               "cudaMemcpy H2D");
  }

  void copy_to_host(T* dst, std::size_t count) const {
    if (count == 0) {
      return;
    }
    check_cuda(cudaMemcpy(dst, ptr_, count * sizeof(T), cudaMemcpyDeviceToHost),
               "cudaMemcpy D2H");
  }

 private:
  T* ptr_ = nullptr;
  std::size_t size_ = 0;
};

template <class T>
class ManagedBuffer {
 public:
  explicit ManagedBuffer(std::size_t size) : size_(size) {
    if (size_ > 0) {
      check_cuda(cudaMallocManaged(&ptr_, size_ * sizeof(T)), "cudaMallocManaged");
    }
  }

  ~ManagedBuffer() {
    if (ptr_ != nullptr) {
      cudaFree(ptr_);
    }
  }

  ManagedBuffer(const ManagedBuffer&) = delete;
  ManagedBuffer& operator=(const ManagedBuffer&) = delete;

  T* data() { return ptr_; }
  const T* data() const { return ptr_; }
  std::size_t size() const { return size_; }

  void copy_from_host(const T* src, std::size_t count) {
    if (count > 0) {
      std::copy(src, src + count, ptr_);
    }
  }

  void zero() {
    if (size_ > 0) {
      std::fill(ptr_, ptr_ + size_, T{});
    }
  }

  void prefetch_to_device(int device) {
    if (size_ > 0) {
      check_cuda(cudaMemPrefetchAsync(ptr_, size_ * sizeof(T), device),
                 "cudaMemPrefetchAsync device");
    }
  }

  void prefetch_to_cpu() {
    if (size_ > 0) {
      check_cuda(cudaMemPrefetchAsync(ptr_, size_ * sizeof(T), cudaCpuDeviceId),
                 "cudaMemPrefetchAsync cpu");
    }
  }

 private:
  T* ptr_ = nullptr;
  std::size_t size_ = 0;
};

template <class T>
class MappedHostBuffer {
 public:
  explicit MappedHostBuffer(std::size_t size) : size_(size) {
    if (size_ > 0) {
      check_cuda(cudaHostAlloc(&host_ptr_, size_ * sizeof(T), cudaHostAllocMapped),
                 "cudaHostAllocMapped");
      check_cuda(cudaHostGetDevicePointer(&device_ptr_, host_ptr_, 0),
                 "cudaHostGetDevicePointer");
    }
  }

  ~MappedHostBuffer() {
    if (host_ptr_ != nullptr) {
      cudaFreeHost(host_ptr_);
    }
  }

  MappedHostBuffer(const MappedHostBuffer&) = delete;
  MappedHostBuffer& operator=(const MappedHostBuffer&) = delete;

  T* host_data() { return host_ptr_; }
  const T* host_data() const { return host_ptr_; }
  T* device_data() { return device_ptr_; }
  const T* device_data() const { return device_ptr_; }
  std::size_t size() const { return size_; }

  void copy_from_host(const T* src, std::size_t count) {
    if (count > 0) {
      std::copy(src, src + count, host_ptr_);
    }
  }

 private:
  T* host_ptr_ = nullptr;
  T* device_ptr_ = nullptr;
  std::size_t size_ = 0;
};

__device__ long long compute_revenue_device(long long extendedprice_cents,
                                            int discount_basis_points) {
  return (extendedprice_cents * (10000 - discount_basis_points)) / 10000;
}

__global__ void lineitem_q5_aggregate_kernel(
    const int32_t* l_orderkey, const int32_t* l_suppkey,
    const int64_t* l_extendedprice_cents, const int32_t* l_discount_bp,
    int64_t lineitem_count, const int32_t* order_nation_by_key,
    int32_t order_map_size, const int32_t* supplier_nation_by_key,
    int32_t supplier_map_size, unsigned long long* revenue_by_nation,
    int32_t revenue_count) {
  const int64_t idx = blockIdx.x * blockDim.x + threadIdx.x;
  if (idx >= lineitem_count) {
    return;
  }

  const int32_t orderkey = l_orderkey[idx];
  const int32_t suppkey = l_suppkey[idx];
  if (orderkey < 0 || orderkey >= order_map_size || suppkey < 0 ||
      suppkey >= supplier_map_size) {
    return;
  }

  const int32_t order_nation = order_nation_by_key[orderkey];
  const int32_t supplier_nation = supplier_nation_by_key[suppkey];
  if (order_nation < 0 || order_nation != supplier_nation ||
      order_nation >= revenue_count) {
    return;
  }

  const long long revenue =
      compute_revenue_device(l_extendedprice_cents[idx], l_discount_bp[idx]);
  atomicAdd(&revenue_by_nation[order_nation],
            static_cast<unsigned long long>(revenue));
}

std::vector<int64_t> to_signed_revenue(const std::vector<unsigned long long>& input) {
  std::vector<int64_t> output(input.size());
  for (std::size_t i = 0; i < input.size(); ++i) {
    output[i] = static_cast<int64_t>(input[i]);
  }
  return output;
}

void append_rows_from_revenue(Q5Result* result, const Q5PreparedPlan& plan,
                              const std::vector<unsigned long long>& revenue_unsigned) {
  const std::vector<int64_t> revenue_by_nation = to_signed_revenue(revenue_unsigned);
  for (std::size_t nation = 0; nation < revenue_by_nation.size(); ++nation) {
    if (revenue_by_nation[nation] != 0) {
      result->rows.push_back(
          Q5ResultRow{plan.nation_name_by_key[nation], revenue_by_nation[nation]});
    }
  }

  std::sort(result->rows.begin(), result->rows.end(),
            [](const Q5ResultRow& a, const Q5ResultRow& b) {
              if (a.revenue_cents != b.revenue_cents) {
                return a.revenue_cents > b.revenue_cents;
              }
              return a.nation_name < b.nation_name;
            });
}

double launch_lineitem_kernel(const int32_t* l_orderkey, const int32_t* l_suppkey,
                              const int64_t* l_extendedprice_cents,
                              const int32_t* l_discount_bp,
                              std::size_t lineitem_count,
                              const int32_t* order_nation_by_key,
                              std::size_t order_map_size,
                              const int32_t* supplier_nation_by_key,
                              std::size_t supplier_map_size,
                              unsigned long long* revenue_by_nation,
                              std::size_t revenue_count) {
  CudaEvent kernel_start;
  CudaEvent kernel_stop;
  const int threads = 256;
  const int blocks = static_cast<int>((lineitem_count + threads - 1) / threads);

  check_cuda(cudaEventRecord(kernel_start.get()), "cudaEventRecord kernel_start");
  if (blocks > 0) {
    lineitem_q5_aggregate_kernel<<<blocks, threads>>>(
        l_orderkey, l_suppkey, l_extendedprice_cents, l_discount_bp,
        static_cast<int64_t>(lineitem_count), order_nation_by_key,
        static_cast<int32_t>(order_map_size), supplier_nation_by_key,
        static_cast<int32_t>(supplier_map_size), revenue_by_nation,
        static_cast<int32_t>(revenue_count));
    check_cuda(cudaGetLastError(), "lineitem_q5_aggregate_kernel launch");
  }
  check_cuda(cudaEventRecord(kernel_stop.get()), "cudaEventRecord kernel_stop");
  return elapsed_ms(kernel_start, kernel_stop);
}

}  // namespace

Q5Result execute_q5_gpu_copy(const TpchDatabase& db, const Q5Params& params) {
  Stopwatch total_timer;
  const Q5PreparedPlan plan = build_q5_plan_cpu(db, params);

  Q5Result result;
  result.timing.build_ms = plan.build_ms;

  const std::size_t lineitem_count = static_cast<std::size_t>(db.lineitem.size());
  const std::size_t revenue_count = static_cast<std::size_t>(plan.max_nation_key + 1);

  DeviceBuffer<int32_t> d_l_orderkey(lineitem_count);
  DeviceBuffer<int32_t> d_l_suppkey(lineitem_count);
  DeviceBuffer<int64_t> d_l_extendedprice(lineitem_count);
  DeviceBuffer<int32_t> d_l_discount(lineitem_count);
  DeviceBuffer<int32_t> d_order_nation(plan.order_nation_by_key.size());
  DeviceBuffer<int32_t> d_supplier_nation(plan.supplier_nation_by_key.size());
  DeviceBuffer<unsigned long long> d_revenue(revenue_count);

  CudaEvent h2d_start;
  CudaEvent h2d_stop;
  check_cuda(cudaEventRecord(h2d_start.get()), "cudaEventRecord h2d_start");
  d_l_orderkey.copy_from_host(db.lineitem.l_orderkey.data(), lineitem_count);
  d_l_suppkey.copy_from_host(db.lineitem.l_suppkey.data(), lineitem_count);
  d_l_extendedprice.copy_from_host(db.lineitem.l_extendedprice_cents.data(),
                                   lineitem_count);
  d_l_discount.copy_from_host(db.lineitem.l_discount_bp.data(), lineitem_count);
  d_order_nation.copy_from_host(plan.order_nation_by_key.data(),
                                plan.order_nation_by_key.size());
  d_supplier_nation.copy_from_host(plan.supplier_nation_by_key.data(),
                                   plan.supplier_nation_by_key.size());
  check_cuda(cudaMemset(d_revenue.data(), 0,
                        revenue_count * sizeof(unsigned long long)),
             "cudaMemset revenue");
  check_cuda(cudaEventRecord(h2d_stop.get()), "cudaEventRecord h2d_stop");
  result.timing.h2d_ms = elapsed_ms(h2d_start, h2d_stop);

  result.timing.kernel_ms = launch_lineitem_kernel(
      d_l_orderkey.data(), d_l_suppkey.data(), d_l_extendedprice.data(),
      d_l_discount.data(), lineitem_count, d_order_nation.data(),
      d_order_nation.size(), d_supplier_nation.data(), d_supplier_nation.size(),
      d_revenue.data(), d_revenue.size());

  std::vector<unsigned long long> revenue_unsigned(revenue_count, 0);
  CudaEvent d2h_start;
  CudaEvent d2h_stop;
  check_cuda(cudaEventRecord(d2h_start.get()), "cudaEventRecord d2h_start");
  d_revenue.copy_to_host(revenue_unsigned.data(), revenue_unsigned.size());
  check_cuda(cudaEventRecord(d2h_stop.get()), "cudaEventRecord d2h_stop");
  result.timing.d2h_ms = elapsed_ms(d2h_start, d2h_stop);
  result.timing.scan_ms =
      result.timing.h2d_ms + result.timing.kernel_ms + result.timing.d2h_ms;

  append_rows_from_revenue(&result, plan, revenue_unsigned);

  result.timing.total_ms = total_timer.elapsed_ms();
  return result;
}

Q5Result execute_q5_gpu_managed(const TpchDatabase& db, const Q5Params& params) {
  Stopwatch total_timer;
  const Q5PreparedPlan plan = build_q5_plan_cpu(db, params);

  Q5Result result;
  result.timing.build_ms = plan.build_ms;

  int device = 0;
  check_cuda(cudaGetDevice(&device), "cudaGetDevice");

  const std::size_t lineitem_count = static_cast<std::size_t>(db.lineitem.size());
  const std::size_t revenue_count = static_cast<std::size_t>(plan.max_nation_key + 1);

  ManagedBuffer<int32_t> m_l_orderkey(lineitem_count);
  ManagedBuffer<int32_t> m_l_suppkey(lineitem_count);
  ManagedBuffer<int64_t> m_l_extendedprice(lineitem_count);
  ManagedBuffer<int32_t> m_l_discount(lineitem_count);
  ManagedBuffer<int32_t> m_order_nation(plan.order_nation_by_key.size());
  ManagedBuffer<int32_t> m_supplier_nation(plan.supplier_nation_by_key.size());
  ManagedBuffer<unsigned long long> m_revenue(revenue_count);

  m_l_orderkey.copy_from_host(db.lineitem.l_orderkey.data(), lineitem_count);
  m_l_suppkey.copy_from_host(db.lineitem.l_suppkey.data(), lineitem_count);
  m_l_extendedprice.copy_from_host(db.lineitem.l_extendedprice_cents.data(),
                                   lineitem_count);
  m_l_discount.copy_from_host(db.lineitem.l_discount_bp.data(), lineitem_count);
  m_order_nation.copy_from_host(plan.order_nation_by_key.data(),
                                plan.order_nation_by_key.size());
  m_supplier_nation.copy_from_host(plan.supplier_nation_by_key.data(),
                                   plan.supplier_nation_by_key.size());
  m_revenue.zero();

  CudaEvent h2d_start;
  CudaEvent h2d_stop;
  check_cuda(cudaEventRecord(h2d_start.get()), "cudaEventRecord managed_h2d_start");
  m_l_orderkey.prefetch_to_device(device);
  m_l_suppkey.prefetch_to_device(device);
  m_l_extendedprice.prefetch_to_device(device);
  m_l_discount.prefetch_to_device(device);
  m_order_nation.prefetch_to_device(device);
  m_supplier_nation.prefetch_to_device(device);
  m_revenue.prefetch_to_device(device);
  check_cuda(cudaEventRecord(h2d_stop.get()), "cudaEventRecord managed_h2d_stop");
  result.timing.h2d_ms = elapsed_ms(h2d_start, h2d_stop);

  result.timing.kernel_ms = launch_lineitem_kernel(
      m_l_orderkey.data(), m_l_suppkey.data(), m_l_extendedprice.data(),
      m_l_discount.data(), lineitem_count, m_order_nation.data(),
      m_order_nation.size(), m_supplier_nation.data(), m_supplier_nation.size(),
      m_revenue.data(), m_revenue.size());

  CudaEvent d2h_start;
  CudaEvent d2h_stop;
  check_cuda(cudaEventRecord(d2h_start.get()), "cudaEventRecord managed_d2h_start");
  m_revenue.prefetch_to_cpu();
  check_cuda(cudaEventRecord(d2h_stop.get()), "cudaEventRecord managed_d2h_stop");
  result.timing.d2h_ms = elapsed_ms(d2h_start, d2h_stop);

  std::vector<unsigned long long> revenue_unsigned(revenue_count, 0);
  std::copy(m_revenue.data(), m_revenue.data() + revenue_count,
            revenue_unsigned.begin());

  result.timing.scan_ms =
      result.timing.h2d_ms + result.timing.kernel_ms + result.timing.d2h_ms;
  append_rows_from_revenue(&result, plan, revenue_unsigned);
  result.timing.total_ms = total_timer.elapsed_ms();
  return result;
}

Q5Result execute_q5_gpu_mapped(const TpchDatabase& db, const Q5Params& params) {
  Stopwatch total_timer;
  const Q5PreparedPlan plan = build_q5_plan_cpu(db, params);

  Q5Result result;
  result.timing.build_ms = plan.build_ms;

  const std::size_t lineitem_count = static_cast<std::size_t>(db.lineitem.size());
  const std::size_t revenue_count = static_cast<std::size_t>(plan.max_nation_key + 1);

  MappedHostBuffer<int32_t> h_l_orderkey(lineitem_count);
  MappedHostBuffer<int32_t> h_l_suppkey(lineitem_count);
  MappedHostBuffer<int64_t> h_l_extendedprice(lineitem_count);
  MappedHostBuffer<int32_t> h_l_discount(lineitem_count);
  MappedHostBuffer<int32_t> h_order_nation(plan.order_nation_by_key.size());
  MappedHostBuffer<int32_t> h_supplier_nation(plan.supplier_nation_by_key.size());
  DeviceBuffer<unsigned long long> d_revenue(revenue_count);

  h_l_orderkey.copy_from_host(db.lineitem.l_orderkey.data(), lineitem_count);
  h_l_suppkey.copy_from_host(db.lineitem.l_suppkey.data(), lineitem_count);
  h_l_extendedprice.copy_from_host(db.lineitem.l_extendedprice_cents.data(),
                                   lineitem_count);
  h_l_discount.copy_from_host(db.lineitem.l_discount_bp.data(), lineitem_count);
  h_order_nation.copy_from_host(plan.order_nation_by_key.data(),
                                plan.order_nation_by_key.size());
  h_supplier_nation.copy_from_host(plan.supplier_nation_by_key.data(),
                                   plan.supplier_nation_by_key.size());

  CudaEvent setup_start;
  CudaEvent setup_stop;
  check_cuda(cudaEventRecord(setup_start.get()), "cudaEventRecord mapped_setup_start");
  check_cuda(cudaMemset(d_revenue.data(), 0,
                        revenue_count * sizeof(unsigned long long)),
             "cudaMemset mapped revenue");
  check_cuda(cudaEventRecord(setup_stop.get()), "cudaEventRecord mapped_setup_stop");
  result.timing.h2d_ms = elapsed_ms(setup_start, setup_stop);

  result.timing.kernel_ms = launch_lineitem_kernel(
      h_l_orderkey.device_data(), h_l_suppkey.device_data(),
      h_l_extendedprice.device_data(), h_l_discount.device_data(), lineitem_count,
      h_order_nation.device_data(), h_order_nation.size(),
      h_supplier_nation.device_data(), h_supplier_nation.size(), d_revenue.data(),
      d_revenue.size());

  std::vector<unsigned long long> revenue_unsigned(revenue_count, 0);
  CudaEvent d2h_start;
  CudaEvent d2h_stop;
  check_cuda(cudaEventRecord(d2h_start.get()), "cudaEventRecord mapped_d2h_start");
  d_revenue.copy_to_host(revenue_unsigned.data(), revenue_unsigned.size());
  check_cuda(cudaEventRecord(d2h_stop.get()), "cudaEventRecord mapped_d2h_stop");
  result.timing.d2h_ms = elapsed_ms(d2h_start, d2h_stop);

  result.timing.scan_ms =
      result.timing.h2d_ms + result.timing.kernel_ms + result.timing.d2h_ms;
  append_rows_from_revenue(&result, plan, revenue_unsigned);
  result.timing.total_ms = total_timer.elapsed_ms();
  return result;
}

}  // namespace memq5

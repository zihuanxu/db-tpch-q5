#pragma once

#include <memory>

#include <arrow/result.h>

#include "engine/q5_params.hpp"
#include "engine/q5_result.hpp"
#include "io/arrow_q5_loader.hpp"
#include "session/q5_cpu_session.hpp"

namespace memq5 {

enum class ArrowCudaMemoryMode { kCopy, kManaged, kMapped };

arrow::Result<Q5Result> execute_q5_arrow_gpu_copy(const ArrowQ5Dataset& dataset,
                                                  const Q5Params& params);
arrow::Result<Q5Result>
execute_q5_arrow_gpu_managed(const ArrowQ5Dataset& dataset,
                             const Q5Params& params);
arrow::Result<Q5Result>
execute_q5_arrow_gpu_mapped(const ArrowQ5Dataset& dataset,
                            const Q5Params& params);

class ArrowCudaQ5Session {
 public:
  static arrow::Result<std::unique_ptr<ArrowCudaQ5Session>> Make(
      const ArrowQ5Dataset& dataset, const Q5Params& params,
      ArrowCudaMemoryMode mode);
  ~ArrowCudaQ5Session();

  arrow::Result<Q5Result> Execute();
  const Q5SessionSetup& setup() const;

 private:
  struct Impl;

  explicit ArrowCudaQ5Session(std::unique_ptr<Impl> impl);

  friend arrow::Result<Q5Result> execute_q5_arrow_gpu_copy(
      const ArrowQ5Dataset& dataset, const Q5Params& params);
  friend arrow::Result<Q5Result> execute_q5_arrow_gpu_managed(
      const ArrowQ5Dataset& dataset, const Q5Params& params);
  friend arrow::Result<Q5Result> execute_q5_arrow_gpu_mapped(
      const ArrowQ5Dataset& dataset, const Q5Params& params);

  std::unique_ptr<Impl> impl_;
};

}  // namespace memq5

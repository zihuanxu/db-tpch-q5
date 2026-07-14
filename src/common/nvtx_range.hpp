#pragma once

#if defined(MEMQ5_ENABLE_NVTX)
#include <nvtx3/nvToolsExt.h>
#endif

namespace memq5 {

class NvtxRange {
public:
  explicit NvtxRange(const char* name) noexcept {
#if defined(MEMQ5_ENABLE_NVTX)
    nvtxRangePushA(name);
#else
    (void)name;
#endif
  }

  ~NvtxRange() noexcept {
#if defined(MEMQ5_ENABLE_NVTX)
    nvtxRangePop();
#endif
  }

  NvtxRange(const NvtxRange&) = delete;
  NvtxRange& operator=(const NvtxRange&) = delete;
};

}  // namespace memq5

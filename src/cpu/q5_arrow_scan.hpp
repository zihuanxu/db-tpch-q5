#pragma once

#include <memory>

#include <arrow/result.h>
#include <arrow/table.h>

#include "engine/arrow_q5_plan.hpp"
#include "engine/q5_result.hpp"

namespace memq5 {

arrow::Result<Q5Result> scan_q5_arrow_lineitem(
    const std::shared_ptr<arrow::Table>& lineitem, const ArrowQ5Plan& plan,
    int threads);

}  // namespace memq5

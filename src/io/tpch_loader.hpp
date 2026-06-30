#pragma once

#include <string>

#include "io/tpch_schema.hpp"

namespace memq5 {

TpchDatabase load_tpch(const std::string& data_dir);

}  // namespace memq5

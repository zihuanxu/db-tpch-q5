#include "session/q5_cpu_session.hpp"

#include <cassert>
#include <string>

#include "engine/q5_params.hpp"
#include "engine/q5_result_io.hpp"
#include "io/arrow_q5_loader.hpp"

namespace {

arrow::Result<memq5::ArrowQ5Dataset> LoadTinyArrowDataset() {
  return memq5::load_arrow_q5_dataset(MEMQ5_ARROW_FIXTURE_DIR);
}

memq5::Q5Params Asia1994Params() {
  memq5::Q5Params params;
  params.region_name = "ASIA";
  memq5::set_q5_date(&params, "1994-01-01");
  return params;
}

}  // namespace

int main() {
  const auto dataset = LoadTinyArrowDataset().ValueOrDie();
  memq5::Q5Params params = Asia1994Params();
  params.threads = 2;
  auto session = memq5::ArrowCpuQ5Session::Make(dataset, params).ValueOrDie();
  const auto first = session->Execute().ValueOrDie();
  const auto second = session->Execute().ValueOrDie();
  assert(memq5::result_hash_hex(first) == "248d10b6ee352953");
  assert(memq5::result_hash_hex(first) == memq5::result_hash_hex(second));
  assert(first.timing.build_ms == 0.0);
  assert(second.timing.build_ms == 0.0);
  assert(session->setup().plan_build_ms >= 0.0);

  params.threads = 0;
  const auto invalid = memq5::ArrowCpuQ5Session::Make(dataset, params);
  assert(!invalid.ok());
  assert(invalid.status().IsInvalid());
  return 0;
}

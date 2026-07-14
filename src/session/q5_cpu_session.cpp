#include "session/q5_cpu_session.hpp"

#include <exception>
#include <memory>
#include <new>
#include <stdexcept>
#include <system_error>
#include <utility>

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
    Stopwatch setup_timer;
    ARROW_ASSIGN_OR_RAISE(ArrowQ5Plan plan, build_arrow_q5_plan(dataset, params));
    Q5SessionSetup setup;
    setup.plan_build_ms = plan.build_ms;
    setup.total_ms = setup_timer.elapsed_ms();
    return std::unique_ptr<ArrowCpuQ5Session>(new ArrowCpuQ5Session(
        dataset.lineitem, std::move(plan), params.threads, setup));
  });
}

arrow::Result<Q5Result> ArrowCpuQ5Session::Execute() const {
  return arrow_cpu_status_boundary([&]() -> arrow::Result<Q5Result> {
    Stopwatch total_timer;
    ARROW_ASSIGN_OR_RAISE(Q5Result result,
                          scan_q5_arrow_lineitem(lineitem_, plan_, threads_));
    result.timing.build_ms = 0.0;
    result.timing.total_ms = total_timer.elapsed_ms();
    return result;
  });
}

const Q5SessionSetup& ArrowCpuQ5Session::setup() const { return setup_; }

}  // namespace memq5

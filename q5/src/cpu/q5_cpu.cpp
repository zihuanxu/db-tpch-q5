#include "cpu/q5_cpu.hpp"

#include <algorithm>
#include <cstdint>
#include <exception>
#include <mutex>
#include <thread>
#include <vector>

#include "common/fixed_point.hpp"
#include "common/timer.hpp"
#include "engine/q5_plan.hpp"

namespace memq5 {
namespace {

void scan_lineitem_range(const TpchDatabase& db, const Q5PreparedPlan& plan,
                         std::size_t begin, std::size_t end,
                         std::vector<int64_t>* revenue_by_nation,
                         int64_t* matched_rows) {
  for (std::size_t row = begin; row < end; ++row) {
    const int32_t orderkey = db.lineitem.l_orderkey[row];
    const int32_t suppkey = db.lineitem.l_suppkey[row];
    if (!valid_key(plan.order_nation_by_key, orderkey) ||
        !valid_key(plan.supplier_nation_by_key, suppkey)) {
      continue;
    }
    const int32_t order_nation =
        plan.order_nation_by_key[static_cast<std::size_t>(orderkey)];
    const int32_t supplier_nation =
        plan.supplier_nation_by_key[static_cast<std::size_t>(suppkey)];
    if (order_nation < 0 || order_nation != supplier_nation) {
      continue;
    }
    (*revenue_by_nation)[static_cast<std::size_t>(order_nation)] +=
        compute_revenue_1e4(db.lineitem.l_extendedprice_cents[row],
                            db.lineitem.l_discount_hundredths[row]);
    ++(*matched_rows);
  }
}

}  // namespace

Q5Result execute_q5_cpu(const TpchDatabase& db, const Q5Params& params) {
  Stopwatch total_timer;
  const Q5PreparedPlan plan = build_q5_plan_cpu(db, params);

  Q5Result result;
  result.timing.build_ms = plan.build_ms;

  Stopwatch scan_timer;
  std::vector<int64_t> revenue_by_nation(
      static_cast<std::size_t>(plan.max_nation_key + 1), 0);
  const std::size_t lineitem_count = static_cast<std::size_t>(db.lineitem.size());
  const int requested_threads = std::max(1, params.threads);
  const int worker_count =
      static_cast<int>(std::min<std::size_t>(requested_threads,
                                             std::max<std::size_t>(lineitem_count, 1)));
  int64_t matched_rows = 0;

  if (worker_count <= 1 || lineitem_count == 0) {
    scan_lineitem_range(db, plan, 0, lineitem_count, &revenue_by_nation,
                        &matched_rows);
  } else {
    std::vector<std::vector<int64_t>> local_revenues(
        static_cast<std::size_t>(worker_count),
        std::vector<int64_t>(revenue_by_nation.size(), 0));
    std::vector<int64_t> local_matched_rows(
        static_cast<std::size_t>(worker_count), 0);
    std::vector<std::thread> workers;
    std::exception_ptr worker_exception;
    std::mutex worker_exception_mutex;
    workers.reserve(static_cast<std::size_t>(worker_count));

    for (int worker = 0; worker < worker_count; ++worker) {
      const std::size_t begin =
          lineitem_count * static_cast<std::size_t>(worker) /
          static_cast<std::size_t>(worker_count);
      const std::size_t end =
          lineitem_count * static_cast<std::size_t>(worker + 1) /
          static_cast<std::size_t>(worker_count);
      workers.emplace_back([&db, &plan, begin, end, &local_revenues, worker,
                            &local_matched_rows, &worker_exception,
                            &worker_exception_mutex]() {
        try {
          scan_lineitem_range(db, plan, begin, end,
                              &local_revenues[static_cast<std::size_t>(worker)],
                              &local_matched_rows[static_cast<std::size_t>(worker)]);
        } catch (...) {
          std::lock_guard<std::mutex> lock(worker_exception_mutex);
          if (!worker_exception) {
            worker_exception = std::current_exception();
          }
        }
      });
    }

    for (std::thread& worker : workers) {
      worker.join();
    }

    if (worker_exception) {
      std::rethrow_exception(worker_exception);
    }

    for (const auto& local : local_revenues) {
      for (std::size_t nation = 0; nation < revenue_by_nation.size(); ++nation) {
        revenue_by_nation[nation] += local[nation];
      }
    }
    for (const int64_t local_matched : local_matched_rows) {
      matched_rows += local_matched;
    }
  }

  result.timing.scan_ms = scan_timer.elapsed_ms();
  result.counters.input_lineitem_rows = static_cast<int64_t>(lineitem_count);
  result.counters.matched_lineitem_rows = matched_rows;
  result.counters.cpu_input_rows = static_cast<int64_t>(lineitem_count);

  for (std::size_t nation = 0; nation < revenue_by_nation.size(); ++nation) {
    if (revenue_by_nation[nation] != 0) {
      result.rows.push_back(
          Q5ResultRow{plan.nation_name_by_key[nation], revenue_by_nation[nation]});
    }
  }

  std::sort(result.rows.begin(), result.rows.end(),
            [](const Q5ResultRow& a, const Q5ResultRow& b) {
              if (a.revenue_1e4 != b.revenue_1e4) {
                return a.revenue_1e4 > b.revenue_1e4;
              }
              return a.nation_name < b.nation_name;
            });

  result.timing.total_ms = total_timer.elapsed_ms();
  return result;
}

}  // namespace memq5

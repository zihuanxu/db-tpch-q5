#include "io/tpch_loader.hpp"

#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "common/date.hpp"
#include "common/fixed_point.hpp"

namespace memq5 {
namespace {

std::string join_path(const std::string& dir, const std::string& file) {
  if (dir.empty() || dir.back() == '/') {
    return dir + file;
  }
  return dir + "/" + file;
}

std::vector<std::string> split_pipe(const std::string& line) {
  std::vector<std::string> fields;
  std::string field;
  std::istringstream in(line);
  while (std::getline(in, field, '|')) {
    fields.push_back(field);
  }
  return fields;
}

void require_fields(const std::vector<std::string>& fields, std::size_t count,
                    const std::string& table) {
  if (fields.size() < count) {
    throw std::runtime_error(table + " row has too few fields");
  }
}

template <class Fn>
void read_rows(const std::string& path, Fn&& fn) {
  std::ifstream in(path);
  if (!in) {
    throw std::runtime_error("failed to open " + path);
  }
  std::string line;
  while (std::getline(in, line)) {
    if (!line.empty()) {
      fn(split_pipe(line));
    }
  }
}

}  // namespace

TpchDatabase load_tpch(const std::string& data_dir) {
  TpchDatabase db;

  read_rows(join_path(data_dir, "region.tbl"), [&](const auto& fields) {
    require_fields(fields, 2, "region");
    db.region.r_regionkey.push_back(std::stoi(fields[0]));
    db.region.r_name_code.push_back(db.region.names.encode(fields[1]));
  });

  read_rows(join_path(data_dir, "nation.tbl"), [&](const auto& fields) {
    require_fields(fields, 3, "nation");
    db.nation.n_nationkey.push_back(std::stoi(fields[0]));
    db.nation.n_name_code.push_back(db.nation.names.encode(fields[1]));
    db.nation.n_regionkey.push_back(std::stoi(fields[2]));
  });

  read_rows(join_path(data_dir, "supplier.tbl"), [&](const auto& fields) {
    require_fields(fields, 4, "supplier");
    db.supplier.s_suppkey.push_back(std::stoi(fields[0]));
    db.supplier.s_nationkey.push_back(std::stoi(fields[3]));
  });

  read_rows(join_path(data_dir, "customer.tbl"), [&](const auto& fields) {
    require_fields(fields, 4, "customer");
    db.customer.c_custkey.push_back(std::stoi(fields[0]));
    db.customer.c_nationkey.push_back(std::stoi(fields[3]));
  });

  read_rows(join_path(data_dir, "orders.tbl"), [&](const auto& fields) {
    require_fields(fields, 5, "orders");
    db.orders.o_orderkey.push_back(std::stoi(fields[0]));
    db.orders.o_custkey.push_back(std::stoi(fields[1]));
    db.orders.o_orderdate.push_back(date_to_days(fields[4]));
  });

  read_rows(join_path(data_dir, "lineitem.tbl"), [&](const auto& fields) {
    require_fields(fields, 7, "lineitem");
    db.lineitem.l_orderkey.push_back(std::stoi(fields[0]));
    db.lineitem.l_suppkey.push_back(std::stoi(fields[2]));
    db.lineitem.l_extendedprice_cents.push_back(
        parse_fixed_decimal(fields[5], 100));
    db.lineitem.l_discount_bp.push_back(
        static_cast<int32_t>(parse_fixed_decimal(fields[6], 10000)));
  });

  return db;
}

}  // namespace memq5

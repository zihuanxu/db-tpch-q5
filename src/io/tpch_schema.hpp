#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#include "common/column.hpp"

namespace memq5 {

class StringDictionary {
 public:
  int32_t encode(const std::string& value) {
    const auto it = codes_.find(value);
    if (it != codes_.end()) {
      return it->second;
    }
    const int32_t code = static_cast<int32_t>(values_.size());
    values_.push_back(value);
    codes_.emplace(value, code);
    return code;
  }

  int32_t find(const std::string& value) const {
    const auto it = codes_.find(value);
    return it == codes_.end() ? -1 : it->second;
  }

  const std::string& value(int32_t code) const {
    if (code < 0 || static_cast<std::size_t>(code) >= values_.size()) {
      throw std::out_of_range("dictionary code out of range");
    }
    return values_[static_cast<std::size_t>(code)];
  }

  std::size_t size() const { return values_.size(); }

 private:
  std::vector<std::string> values_;
  std::unordered_map<std::string, int32_t> codes_;
};

struct RegionTable {
  Column<int32_t> r_regionkey;
  Column<int32_t> r_name_code;
  StringDictionary names;

  int64_t size() const { return r_regionkey.size(); }
};

struct NationTable {
  Column<int32_t> n_nationkey;
  Column<int32_t> n_name_code;
  Column<int32_t> n_regionkey;
  StringDictionary names;

  int64_t size() const { return n_nationkey.size(); }
};

struct SupplierTable {
  Column<int32_t> s_suppkey;
  Column<int32_t> s_nationkey;

  int64_t size() const { return s_suppkey.size(); }
};

struct CustomerTable {
  Column<int32_t> c_custkey;
  Column<int32_t> c_nationkey;

  int64_t size() const { return c_custkey.size(); }
};

struct OrdersTable {
  Column<int32_t> o_orderkey;
  Column<int32_t> o_custkey;
  Column<int32_t> o_orderdate;

  int64_t size() const { return o_orderkey.size(); }
};

struct LineitemTable {
  Column<int32_t> l_orderkey;
  Column<int32_t> l_suppkey;
  Column<int64_t> l_extendedprice_cents;
  Column<int32_t> l_discount_bp;

  int64_t size() const { return l_orderkey.size(); }
};

struct TpchDatabase {
  RegionTable region;
  NationTable nation;
  SupplierTable supplier;
  CustomerTable customer;
  OrdersTable orders;
  LineitemTable lineitem;
};

}  // namespace memq5

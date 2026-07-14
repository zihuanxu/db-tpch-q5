#pragma once

#include <cstdint>
#include <vector>

#include "common/aligned_buffer.hpp"

namespace memq5 {

template <class T>
struct ColumnView {
  const T* data = nullptr;
  const uint8_t* validity = nullptr;
  int64_t size = 0;
};

template <class T>
class Column {
 public:
  using Storage = std::vector<T, AlignedAllocator<T, 64>>;

  Column() = default;
  explicit Column(int64_t size) : data_(static_cast<std::size_t>(size)) {}

  void reserve(std::size_t size) { data_.reserve(size); }
  void push_back(const T& value) { data_.push_back(value); }
  void resize(std::size_t size) { data_.resize(size); }

  int64_t size() const { return static_cast<int64_t>(data_.size()); }
  bool empty() const { return data_.empty(); }

  const T* data() const { return data_.empty() ? nullptr : data_.data(); }
  T* mutable_data() { return data_.empty() ? nullptr : data_.data(); }

  const T& operator[](std::size_t index) const { return data_[index]; }
  T& operator[](std::size_t index) { return data_[index]; }

  ColumnView<T> view() const {
    return ColumnView<T>{data(), validity_.empty() ? nullptr : validity_.data(), size()};
  }

  const Storage& values() const { return data_; }

 private:
  Storage data_;
  std::vector<uint8_t> validity_;
};

}  // namespace memq5

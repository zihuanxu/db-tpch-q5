#pragma once

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace memq5 {

class Bitmap {
 public:
  Bitmap() = default;
  explicit Bitmap(std::size_t bits) { resize(bits); }

  void resize(std::size_t bits) {
    size_bits_ = bits;
    bytes_.assign((bits + 7) / 8, 0);
  }

  std::size_t size() const { return size_bits_; }

  void set(std::size_t bit) {
    check(bit);
    bytes_[bit / 8] |= static_cast<uint8_t>(uint8_t{1} << (bit % 8));
  }

  void clear(std::size_t bit) {
    check(bit);
    bytes_[bit / 8] &= static_cast<uint8_t>(~(uint8_t{1} << (bit % 8)));
  }

  bool test(std::size_t bit) const {
    check(bit);
    return (bytes_[bit / 8] & (uint8_t{1} << (bit % 8))) != 0;
  }

  std::size_t count_set_bits() const {
    std::size_t count = 0;
    for (uint8_t byte : bytes_) {
      count += static_cast<std::size_t>(__builtin_popcount(byte));
    }
    return count;
  }

  const uint8_t* data() const { return bytes_.empty() ? nullptr : bytes_.data(); }

 private:
  void check(std::size_t bit) const {
    if (bit >= size_bits_) {
      throw std::out_of_range("bitmap bit index out of range");
    }
  }

  std::size_t size_bits_ = 0;
  std::vector<uint8_t> bytes_;
};

}  // namespace memq5

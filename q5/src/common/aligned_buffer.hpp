#pragma once

#include <cstdlib>
#include <limits>
#include <new>
#include <type_traits>

namespace memq5 {

template <class T, std::size_t Alignment = 64>
class AlignedAllocator {
 public:
  using value_type = T;

  AlignedAllocator() noexcept = default;

  template <class U>
  AlignedAllocator(const AlignedAllocator<U, Alignment>&) noexcept {}

  [[nodiscard]] T* allocate(std::size_t n) {
    if (n == 0) {
      return nullptr;
    }
    if (n > std::numeric_limits<std::size_t>::max() / sizeof(T)) {
      throw std::bad_array_new_length();
    }

    void* ptr = nullptr;
    if (posix_memalign(&ptr, Alignment, n * sizeof(T)) != 0) {
      throw std::bad_alloc();
    }
    return static_cast<T*>(ptr);
  }

  void deallocate(T* p, std::size_t) noexcept {
    std::free(p);
  }

  template <class U>
  struct rebind {
    using other = AlignedAllocator<U, Alignment>;
  };
};

template <class T, class U, std::size_t Alignment>
bool operator==(const AlignedAllocator<T, Alignment>&,
                const AlignedAllocator<U, Alignment>&) noexcept {
  return true;
}

template <class T, class U, std::size_t Alignment>
bool operator!=(const AlignedAllocator<T, Alignment>&,
                const AlignedAllocator<U, Alignment>&) noexcept {
  return false;
}

}  // namespace memq5

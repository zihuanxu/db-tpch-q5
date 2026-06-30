cmake_minimum_required(VERSION 3.16)

project(memq5 LANGUAGES CXX)

option(MEMQ5_ENABLE_TESTS "Build tests" ON)
option(MEMQ5_ENABLE_CUDA "Build CUDA engines" OFF)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(memq5_core
  src/engine/q5_plan.cpp
  src/engine/q5_result_io.cpp
  src/io/tpch_loader.cpp
  src/cpu/q5_cpu.cpp
)

target_include_directories(memq5_core PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_compile_options(memq5_core PRIVATE
  $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra -Wpedantic>
)

add_executable(memq5
  src/cli/memq5.cpp
)

target_link_libraries(memq5 PRIVATE memq5_core)

if(MEMQ5_ENABLE_CUDA)
  if(NOT DEFINED CMAKE_CUDA_ARCHITECTURES)
    set(CMAKE_CUDA_ARCHITECTURES 75 CACHE STRING "CUDA architectures")
  endif()
  enable_language(CUDA)

  add_library(memq5_cuda
    src/cuda/q5_cuda.cu
  )

  target_include_directories(memq5_cuda PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/src
  )

  target_link_libraries(memq5_cuda PUBLIC memq5_core)
  target_compile_definitions(memq5_cuda PUBLIC MEMQ5_HAS_CUDA=1)
  target_compile_definitions(memq5 PRIVATE MEMQ5_HAS_CUDA=1)
  target_link_libraries(memq5 PRIVATE memq5_cuda)

  set_target_properties(memq5_cuda PROPERTIES
    CUDA_STANDARD 17
    CUDA_STANDARD_REQUIRED ON
    CUDA_EXTENSIONS OFF
  )
endif()

if(MEMQ5_ENABLE_TESTS)
  enable_testing()
  add_subdirectory(tests)
endif()

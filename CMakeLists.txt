cmake_minimum_required(VERSION 3.16)

project(memq5 LANGUAGES CXX)

option(MEMQ5_ENABLE_ARROW "Build the Apache Arrow IPC loader" OFF)
option(MEMQ5_ENABLE_TESTS "Build tests" ON)
option(MEMQ5_ENABLE_CUDA "Build CUDA engines" OFF)
option(MEMQ5_ENABLE_NVTX "Enable NVTX ranges" OFF)

if(MEMQ5_ENABLE_NVTX)
  find_package(CUDAToolkit REQUIRED)
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(memq5_core
  q5/src/engine/q5_plan.cpp
  q5/src/engine/q5_result_io.cpp
  q5/src/io/tpch_loader.cpp
  q5/src/cpu/q5_cpu.cpp
)

target_include_directories(memq5_core PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/q5/src
)

target_compile_options(memq5_core PRIVATE
  $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra -Wpedantic>
)

if(MEMQ5_ENABLE_ARROW)
  find_package(Arrow 23.0.1 EXACT CONFIG REQUIRED)
  find_package(ArrowAcero 23.0.1 EXACT CONFIG REQUIRED)
  find_package(OpenSSL REQUIRED)
  find_package(nlohmann_json CONFIG REQUIRED)

  add_library(memq5_arrow
    q5/src/hybrid/hybrid_cost_model.cpp
    q5/src/hybrid/batch_partition.cpp
    q5/src/cpu/q5_acero.cpp
    q5/src/cpu/q5_arrow_cpu.cpp
    q5/src/cpu/q5_arrow_scan.cpp
    q5/src/io/arrow_q5_loader.cpp
    q5/src/session/q5_cpu_session.cpp
    q5/src/session/q5_session_io.cpp
  )

  target_include_directories(memq5_arrow PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/q5/src
  )

  target_link_libraries(memq5_arrow
    PUBLIC Arrow::arrow_shared ArrowCompute::arrow_compute_shared
           ArrowAcero::arrow_acero_shared memq5_core
    PRIVATE OpenSSL::Crypto nlohmann_json::nlohmann_json
  )

  target_compile_options(memq5_arrow PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra -Wpedantic>
  )

  if(MEMQ5_ENABLE_NVTX)
    target_compile_definitions(memq5_arrow PRIVATE MEMQ5_ENABLE_NVTX=1)
    target_link_libraries(memq5_arrow PRIVATE CUDA::nvtx3)
  endif()

  add_executable(memq5_arrow_check
    q5/src/cli/memq5_arrow_check.cpp
  )
  target_link_libraries(memq5_arrow_check PRIVATE memq5_arrow)

  add_executable(memq5_arrow_query
    q5/src/cli/memq5_arrow_query.cpp
  )
  target_link_libraries(memq5_arrow_query PRIVATE memq5_arrow)

  add_executable(memq5_arrow_session
    q5/src/cli/memq5_arrow_session.cpp
  )
  target_link_libraries(memq5_arrow_session PRIVATE memq5_arrow)
  if(MEMQ5_ENABLE_NVTX)
    target_compile_definitions(memq5_arrow_session PRIVATE MEMQ5_ENABLE_NVTX=1)
    target_link_libraries(memq5_arrow_session PRIVATE CUDA::nvtx3)
  endif()
endif()

add_executable(memq5
  q5/src/cli/memq5.cpp
)

target_link_libraries(memq5 PRIVATE memq5_core)

if(MEMQ5_ENABLE_CUDA)
  if(NOT DEFINED CMAKE_CUDA_ARCHITECTURES)
    set(CMAKE_CUDA_ARCHITECTURES 75 CACHE STRING "CUDA architectures")
  endif()
  enable_language(CUDA)

  add_library(memq5_cuda
    q5/src/cuda/q5_cuda.cu
  )

  target_include_directories(memq5_cuda PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/q5/src
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

  if(MEMQ5_ENABLE_ARROW)
    add_library(memq5_arrow_cuda
      q5/src/cuda/q5_arrow_cuda.cu
      q5/src/hybrid/q5_hybrid.cpp
    )
    target_include_directories(memq5_arrow_cuda PUBLIC
      ${CMAKE_CURRENT_SOURCE_DIR}/q5/src
    )
    target_link_libraries(memq5_arrow_cuda PUBLIC memq5_arrow)
    target_compile_definitions(memq5_arrow_cuda PUBLIC
      MEMQ5_HAS_ARROW_CUDA=1
    )
    if(MEMQ5_ENABLE_NVTX)
      target_compile_definitions(memq5_arrow_cuda PRIVATE MEMQ5_ENABLE_NVTX=1)
      target_link_libraries(memq5_arrow_cuda PRIVATE CUDA::nvtx3)
    endif()
    set_target_properties(memq5_arrow_cuda PROPERTIES
      CUDA_STANDARD 17
      CUDA_STANDARD_REQUIRED ON
      CUDA_EXTENSIONS OFF
    )
    target_link_libraries(memq5_arrow_query PRIVATE memq5_arrow_cuda)
    target_link_libraries(memq5_arrow_session PRIVATE memq5_arrow_cuda)
  endif()
endif()

if(MEMQ5_ENABLE_TESTS)
  enable_testing()
  add_subdirectory(q5/tests)
endif()

function(add_memq5_test name source)
  add_executable(${name} ${source})
  target_link_libraries(${name} PRIVATE memq5_core)
  target_compile_definitions(${name} PRIVATE
    MEMQ5_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny"
  )
  add_test(NAME ${name} COMMAND ${name})
endfunction()

add_memq5_test(test_common test_common.cpp)
add_memq5_test(test_loader test_loader.cpp)
add_memq5_test(test_q5_plan test_q5_plan.cpp)
add_memq5_test(test_q5_cpu test_q5_cpu.cpp)
add_memq5_test(test_result_io test_result_io.cpp)

if(MEMQ5_ENABLE_CUDA)
  set_source_files_properties(test_q5_cuda.cpp PROPERTIES LANGUAGE CUDA)
  add_executable(test_q5_cuda test_q5_cuda.cpp)
  target_link_libraries(test_q5_cuda PRIVATE memq5_cuda)
  target_compile_definitions(test_q5_cuda PRIVATE
    MEMQ5_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny"
  )
  set_target_properties(test_q5_cuda PROPERTIES
    CUDA_STANDARD 17
    CUDA_STANDARD_REQUIRED ON
    CUDA_EXTENSIONS OFF
  )
  add_test(NAME test_q5_cuda COMMAND test_q5_cuda)
endif()

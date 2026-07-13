function(add_memq5_test name source)
  add_executable(${name} ${source})
  target_link_libraries(${name} PRIVATE memq5_core)
  target_compile_definitions(${name} PRIVATE
    MEMQ5_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny"
  )
  target_compile_options(${name} PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-UNDEBUG>
  )
  add_test(NAME ${name} COMMAND ${name})
endfunction()

add_memq5_test(test_common test_common.cpp)
add_memq5_test(test_loader test_loader.cpp)
add_memq5_test(test_q5_plan test_q5_plan.cpp)
add_memq5_test(test_q5_cpu test_q5_cpu.cpp)
add_memq5_test(test_result_io test_result_io.cpp)

if(MEMQ5_ENABLE_ARROW)
  add_executable(test_arrow_q5_loader test_arrow_q5_loader.cpp)
  target_link_libraries(test_arrow_q5_loader PRIVATE
    memq5_arrow
    nlohmann_json::nlohmann_json
  )
  target_compile_definitions(test_arrow_q5_loader PRIVATE
    MEMQ5_ARROW_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow"
    MEMQ5_TBL_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny"
  )
  target_compile_options(test_arrow_q5_loader PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-UNDEBUG>
  )
  add_test(NAME test_arrow_q5_loader COMMAND test_arrow_q5_loader)

  add_executable(test_q5_arrow_cpu test_q5_arrow_cpu.cpp)
  target_link_libraries(test_q5_arrow_cpu PRIVATE memq5_arrow)
  target_compile_definitions(test_q5_arrow_cpu PRIVATE
    MEMQ5_ARROW_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow"
  )
  target_compile_options(test_q5_arrow_cpu PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-UNDEBUG>
  )
  add_test(NAME test_q5_arrow_cpu COMMAND test_q5_arrow_cpu)

  add_executable(test_q5_acero test_q5_acero.cpp)
  target_link_libraries(test_q5_acero PRIVATE memq5_arrow)
  target_compile_definitions(test_q5_acero PRIVATE
    MEMQ5_ARROW_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow"
  )
  target_compile_options(test_q5_acero PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-UNDEBUG>
  )
  add_test(NAME test_q5_acero COMMAND test_q5_acero)

  add_executable(test_batch_partition test_batch_partition.cpp)
  target_link_libraries(test_batch_partition PRIVATE memq5_arrow)
  target_compile_options(test_batch_partition PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-UNDEBUG>
  )
  add_test(NAME test_batch_partition COMMAND test_batch_partition)

  add_test(
    NAME test_q5_arrow_cli_specialized
    COMMAND $<TARGET_FILE:memq5_arrow_query>
      --dataset ${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow
      --engine cpu-specialized --region ASIA --date 1994-01-01
      --threads 2 --format json
  )
  set_tests_properties(test_q5_arrow_cli_specialized PROPERTIES
    PASS_REGULAR_EXPRESSION "\"result_hash\": \"248d10b6ee352953\""
  )

  add_test(
    NAME test_q5_arrow_cli_acero
    COMMAND $<TARGET_FILE:memq5_arrow_query>
      --dataset ${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow
      --engine arrow-acero --region ASIA --date 1994-01-01
      --threads 2 --format json
  )
  set_tests_properties(test_q5_arrow_cli_acero PROPERTIES
    PASS_REGULAR_EXPRESSION "\"result_hash\": \"248d10b6ee352953\""
  )

  foreach(invalid_case IN ITEMS bad_threads bad_date bad_calendar_date)
    if(invalid_case STREQUAL "bad_threads")
      set(invalid_args --threads 2junk)
    elseif(invalid_case STREQUAL "bad_date")
      set(invalid_args --date 199x-01-01)
    else()
      set(invalid_args --date 1994-02-30)
    endif()
    add_test(
      NAME test_q5_arrow_cli_${invalid_case}
      COMMAND $<TARGET_FILE:memq5_arrow_query>
        --dataset ${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow
        --engine cpu-specialized ${invalid_args} --format json
    )
    set_tests_properties(test_q5_arrow_cli_${invalid_case} PROPERTIES
      WILL_FAIL TRUE
    )
  endforeach()
endif()

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
  target_compile_options(test_q5_cuda PRIVATE
    $<$<COMPILE_LANGUAGE:CUDA>:-UNDEBUG>
  )
  add_test(NAME test_q5_cuda COMMAND test_q5_cuda)
  set_tests_properties(test_q5_cuda PROPERTIES SKIP_RETURN_CODE 77)
endif()

if(MEMQ5_ENABLE_ARROW AND MEMQ5_ENABLE_CUDA)
  set_source_files_properties(test_q5_arrow_cuda.cpp PROPERTIES LANGUAGE CUDA)
  add_executable(test_q5_arrow_cuda test_q5_arrow_cuda.cpp)
  target_link_libraries(test_q5_arrow_cuda PRIVATE memq5_arrow_cuda)
  target_compile_definitions(test_q5_arrow_cuda PRIVATE
    MEMQ5_ARROW_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow"
  )
  set_target_properties(test_q5_arrow_cuda PROPERTIES
    CUDA_STANDARD 17
    CUDA_STANDARD_REQUIRED ON
    CUDA_EXTENSIONS OFF
  )
  target_compile_options(test_q5_arrow_cuda PRIVATE
    $<$<COMPILE_LANGUAGE:CUDA>:-UNDEBUG>
  )
  add_test(NAME test_q5_arrow_cuda COMMAND test_q5_arrow_cuda)
  set_tests_properties(test_q5_arrow_cuda PROPERTIES SKIP_RETURN_CODE 77)

  add_executable(test_q5_hybrid test_q5_hybrid.cpp)
  target_link_libraries(test_q5_hybrid PRIVATE memq5_arrow_cuda)
  target_compile_definitions(test_q5_hybrid PRIVATE
    MEMQ5_ARROW_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow"
  )
  target_compile_options(test_q5_hybrid PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-UNDEBUG>
  )
  add_test(NAME test_q5_hybrid COMMAND test_q5_hybrid)
  set_tests_properties(test_q5_hybrid PROPERTIES SKIP_RETURN_CODE 77)

  add_test(
    NAME test_q5_arrow_cli_hybrid
    COMMAND $<TARGET_FILE:memq5_arrow_query>
      --dataset ${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow
      --engine hybrid-arrow --cpu-ratio 0.5 --region ASIA
      --date 1994-01-01 --threads 2 --format json
  )
  set_tests_properties(test_q5_arrow_cli_hybrid PROPERTIES
    PASS_REGULAR_EXPRESSION "\"result_hash\": \"248d10b6ee352953\""
    SKIP_RETURN_CODE 77
  )

  foreach(gpu_engine IN ITEMS gpu-copy gpu-managed gpu-mapped)
    add_test(
      NAME test_q5_arrow_cli_${gpu_engine}
      COMMAND $<TARGET_FILE:memq5_arrow_query>
        --dataset ${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow
        --engine ${gpu_engine} --region ASIA --date 1994-01-01
        --threads 2 --format json
    )
    set_tests_properties(test_q5_arrow_cli_${gpu_engine} PROPERTIES
      PASS_REGULAR_EXPRESSION "\"result_hash\": \"248d10b6ee352953\""
      SKIP_RETURN_CODE 77
    )
  endforeach()
endif()

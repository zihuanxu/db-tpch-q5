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

  add_executable(test_q5_cpu_session test_q5_cpu_session.cpp)
  target_link_libraries(test_q5_cpu_session PRIVATE memq5_arrow)
  target_compile_definitions(test_q5_cpu_session PRIVATE
    MEMQ5_ARROW_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow"
  )
  target_compile_options(test_q5_cpu_session PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-UNDEBUG>
  )
  add_test(NAME test_q5_cpu_session COMMAND test_q5_cpu_session)

  add_executable(test_q5_session_io test_q5_session_io.cpp)
  target_link_libraries(test_q5_session_io PRIVATE
    memq5_arrow
    nlohmann_json::nlohmann_json
  )
  target_compile_options(test_q5_session_io PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-UNDEBUG>
  )
  add_test(NAME test_q5_session_io COMMAND test_q5_session_io)

  find_package(Python3 REQUIRED COMPONENTS Interpreter)
  set(MEMQ5_SESSION_VALIDATOR [=[
import json
import subprocess
import sys

expected_hash, expected_engine = sys.argv[1:3]
completed = subprocess.run(sys.argv[3:], capture_output=True, text=True)
if completed.returncode == 77:
    print(completed.stderr, file=sys.stderr)
    sys.exit(77)
if completed.returncode != 0:
    print(completed.stderr, file=sys.stderr)
    sys.exit(completed.returncode)
records = [json.loads(line) for line in completed.stdout.splitlines()]
setup, requests = records[0], records[1:]
assert setup["record_type"] == "session_setup"
assert setup["engine"] == expected_engine
assert setup["lifecycle"] == "resident"
assert setup["status"] == "ok"
session_id = setup["session_id"]
assert session_id
request_count = setup["warmup"] + setup["repeat"]
assert len(records) == 1 + request_count, records
assert [record["request_index"] for record in requests] == list(range(request_count))
assert [record["is_warmup"] for record in requests] == [
    index < setup["warmup"] for index in range(request_count)
]

def update_byte(value, byte):
    return ((value ^ byte) * 1099511628211) & ((1 << 64) - 1)

def result_hash(rows):
    value = 14695981039346656037
    for row in rows:
        for byte in row["nation"].encode():
            value = update_byte(value, byte)
        value = update_byte(value, 0xff)
        revenue = row["revenue_1e4"] & ((1 << 64) - 1)
        for shift in range(0, 64, 8):
            value = update_byte(value, (revenue >> shift) & 0xff)
    return f"{value:016x}"

for request in requests:
    assert request["record_type"] == "request"
    assert request["session_id"] == session_id
    assert request["status"] == "ok"
    assert request["result_hash"] == expected_hash
    assert result_hash(request["rows"]) == request["result_hash"]
    assert request["result_rows"] == len(request["rows"])
]=])

  function(add_memq5_session_cli_test engine)
    string(REPLACE "-" "_" test_suffix "${engine}")
    add_test(
      NAME test_q5_arrow_session_${test_suffix}
      COMMAND ${Python3_EXECUTABLE} -c "${MEMQ5_SESSION_VALIDATOR}"
        248d10b6ee352953 ${engine}
        $<TARGET_FILE:memq5_arrow_session>
        --dataset ${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow
        --engine ${engine} --region ASIA --date 1994-01-01
        --threads 2 --cpu-ratio 0.5 --warmup 1 --repeat 2
    )
  endfunction()

  add_memq5_session_cli_test(cpu-specialized)

  add_test(
    NAME test_q5_arrow_session_requests_alias
    COMMAND ${Python3_EXECUTABLE} -c "${MEMQ5_SESSION_VALIDATOR}"
      248d10b6ee352953 cpu-specialized
      $<TARGET_FILE:memq5_arrow_session>
      --dataset ${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow
      --engine cpu-specialized --region ASIA --date 1994-01-01
      --threads 2 --requests 1
  )

  set(MEMQ5_SESSION_INVALID_VALIDATOR [=[
import subprocess
import sys

expected_error = sys.argv[1]
completed = subprocess.run(sys.argv[2:], capture_output=True, text=True)
assert completed.returncode not in (0, 77), completed
assert completed.stdout == "", completed.stdout
assert expected_error in completed.stderr, completed.stderr
assert "manifest.json" not in completed.stderr, completed.stderr
]=])

  function(add_memq5_session_invalid_test name expected_error)
    add_test(
      NAME test_q5_arrow_session_${name}
      COMMAND ${Python3_EXECUTABLE} -c
        "${MEMQ5_SESSION_INVALID_VALIDATOR}" "${expected_error}"
        $<TARGET_FILE:memq5_arrow_session>
        --dataset ${CMAKE_CURRENT_BINARY_DIR}/missing-resident-dataset
        ${ARGN}
    )
  endfunction()

  add_memq5_session_invalid_test(
    bad_warmup "--warmup must be a nonnegative integer" --warmup -1)
  add_memq5_session_invalid_test(
    bad_repeat "--repeat must be a positive integer" --repeat 0)
  add_memq5_session_invalid_test(
    bad_requests "--requests must be a positive integer" --requests 0)
  add_memq5_session_invalid_test(
    mixed_requests "--requests cannot be combined" --requests 1 --repeat 1)
  add_memq5_session_invalid_test(
    duplicate_requests "--requests may be specified only once"
    --requests 1 --requests 1)
  add_memq5_session_invalid_test(
    bad_engine "unsupported resident Arrow engine" --engine arrow-acero)
  add_memq5_session_invalid_test(
    bad_ratio "--cpu-ratio must be between 0 and 1" --cpu-ratio 1.1)
  add_memq5_session_invalid_test(
    bad_date "day out of range" --date 1994-02-30)
  add_memq5_session_invalid_test(
    bad_threads "--threads must be a positive integer" --threads 0)

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

  add_executable(test_q5_hybrid_auto test_q5_hybrid_auto.cpp)
  target_link_libraries(test_q5_hybrid_auto PRIVATE
    memq5_arrow
    nlohmann_json::nlohmann_json
  )
  target_compile_options(test_q5_hybrid_auto PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-UNDEBUG>
  )
  add_test(NAME test_q5_hybrid_auto COMMAND test_q5_hybrid_auto)

  add_executable(test_hybrid_cost_model test_hybrid_cost_model.cpp)
  target_link_libraries(test_hybrid_cost_model PRIVATE memq5_arrow)
  target_compile_options(test_hybrid_cost_model PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-UNDEBUG>
  )
  add_test(NAME test_hybrid_cost_model COMMAND test_hybrid_cost_model)

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
  foreach(session_engine IN ITEMS
      gpu-copy gpu-managed gpu-mapped hybrid-arrow)
    add_memq5_session_cli_test(${session_engine})
    string(REPLACE "-" "_" session_test_suffix "${session_engine}")
    set_tests_properties(
      test_q5_arrow_session_${session_test_suffix}
      PROPERTIES SKIP_RETURN_CODE 77
    )
  endforeach()

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

  set_source_files_properties(test_q5_cuda_session.cpp PROPERTIES LANGUAGE CUDA)
  add_executable(test_q5_cuda_session test_q5_cuda_session.cpp)
  target_link_libraries(test_q5_cuda_session PRIVATE memq5_arrow_cuda)
  target_compile_definitions(test_q5_cuda_session PRIVATE
    MEMQ5_ARROW_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow"
  )
  set_target_properties(test_q5_cuda_session PROPERTIES
    CUDA_STANDARD 17
    CUDA_STANDARD_REQUIRED ON
    CUDA_EXTENSIONS OFF
  )
  target_compile_options(test_q5_cuda_session PRIVATE
    $<$<COMPILE_LANGUAGE:CUDA>:-UNDEBUG>
  )
  add_test(NAME test_q5_cuda_session COMMAND test_q5_cuda_session)
  set_tests_properties(test_q5_cuda_session PROPERTIES SKIP_RETURN_CODE 77)

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

  add_executable(test_q5_hybrid_session test_q5_hybrid_session.cpp)
  target_link_libraries(test_q5_hybrid_session PRIVATE memq5_arrow_cuda)
  target_compile_definitions(test_q5_hybrid_session PRIVATE
    MEMQ5_ARROW_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow"
  )
  target_compile_options(test_q5_hybrid_session PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-UNDEBUG>
  )
  add_test(NAME test_q5_hybrid_session COMMAND test_q5_hybrid_session)
  set_tests_properties(test_q5_hybrid_session PROPERTIES SKIP_RETURN_CODE 77)

  add_executable(test_q5_hybrid_auto_cuda test_q5_hybrid_auto.cpp)
  target_link_libraries(test_q5_hybrid_auto_cuda PRIVATE
    memq5_arrow_cuda
    nlohmann_json::nlohmann_json
  )
  target_compile_definitions(test_q5_hybrid_auto_cuda PRIVATE
    MEMQ5_ARROW_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow"
    MEMQ5_TEST_HYBRID_AUTO_CUDA=1
  )
  target_compile_options(test_q5_hybrid_auto_cuda PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-UNDEBUG>
  )
  add_test(NAME test_q5_hybrid_auto_cuda COMMAND test_q5_hybrid_auto_cuda)
  set_tests_properties(test_q5_hybrid_auto_cuda PROPERTIES SKIP_RETURN_CODE 77)

  set(MEMQ5_HYBRID_AUTO_SESSION_VALIDATOR [=[
import json
import subprocess
import sys

completed = subprocess.run(sys.argv[1:], capture_output=True, text=True)
if completed.returncode == 77:
    print(completed.stderr, file=sys.stderr)
    sys.exit(77)
if completed.returncode != 0:
    print(completed.stderr, file=sys.stderr)
    sys.exit(completed.returncode)

records = [json.loads(line) for line in completed.stdout.splitlines()]
setup, requests = records[0], records[1:]
assert setup["record_type"] == "session_setup"
assert setup["engine"] == "hybrid-arrow"
assert setup["hybrid_model_version"] == "hybrid-cost-v1-batch-v1"
assert setup["calibration_rows"] == 6
assert setup["cpu_calibration_requests"] == 1
assert setup["gpu_calibration_requests"] == 1
assert setup["cpu_calibration_ms"] > 0.0
assert setup["gpu_calibration_ms"] > 0.0
assert setup["gpu_kernel_calibration_ms"] > 0.0
assert setup["cpu_rows_per_ms"] > 0.0
assert setup["gpu_kernel_rows_per_ms"] > 0.0
assert "gpu_rows_per_ms" not in setup
assert setup["gpu_fixed_ms"] >= 0.0
assert 0.0 <= setup["predicted_cpu_ratio"] <= 1.0
assert 0 <= setup["selected_batch_boundary_rows"] <= 6
assert setup["realized_cpu_ratio"] == setup["selected_cpu_ratio"]
assert setup["tune_ms"] >= 0.0
assert setup["tune_ms"] <= setup["session_setup_ms"]
assert len(requests) == 2
assert [request["is_warmup"] for request in requests] == [True, False]

setup_only = {
    "hybrid_model_version",
    "calibration_rows",
    "cpu_calibration_requests",
    "gpu_calibration_requests",
    "cpu_calibration_ms",
    "gpu_calibration_ms",
    "gpu_kernel_calibration_ms",
    "cpu_rows_per_ms",
    "gpu_kernel_rows_per_ms",
    "gpu_fixed_ms",
    "predicted_cpu_ratio",
    "selected_batch_boundary_rows",
    "realized_cpu_ratio",
    "tune_ms",
}
for request in requests:
    assert request["record_type"] == "request"
    assert request["status"] == "ok"
    assert request["result_hash"] == "248d10b6ee352953"
    assert request["selected_cpu_ratio"] == setup["selected_cpu_ratio"]
    assert setup_only.isdisjoint(request)
]=])

  add_test(
    NAME test_q5_arrow_session_hybrid_auto
    COMMAND ${Python3_EXECUTABLE} -c "${MEMQ5_HYBRID_AUTO_SESSION_VALIDATOR}"
      $<TARGET_FILE:memq5_arrow_session>
      --dataset ${CMAKE_CURRENT_SOURCE_DIR}/fixtures/tpch_q5_tiny_arrow
      --engine hybrid-arrow --hybrid-selection auto
      --region ASIA --date 1994-01-01 --threads 2 --warmup 1 --repeat 1
  )
  set_tests_properties(test_q5_arrow_session_hybrid_auto PROPERTIES
    SKIP_RETURN_CODE 77
  )

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

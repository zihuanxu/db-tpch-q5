# Arrow Data, Correctness, And CPU Backends Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the custom canonical store with verified Arrow IPC data and deliver exact Arrow Acero and specialized CPU TPC-H Q5 backends.

**Architecture:** A versioned Python preparation tool converts official `.tbl` files into six schema-checked Arrow IPC files plus a checksum manifest. C++ loads only that dataset into `ArrowTpchDataset`; both CPU backends share exact scale-4 revenue semantics, deterministic result formatting, and three-layer oracle checks.

**Tech Stack:** C++17, CMake/Ninja, Apache Arrow/Acero 23.0.1, PyArrow 23.0.1, Python 3.11, DuckDB decimal SQL, OpenSSL SHA-256, nlohmann-json, CTest, pytest.

## Global Constraints

- Preserve the current implementation until baseline evidence is captured.
- Canonical table schemas and IPC files use the exact Arrow types approved in the design.
- Query backends receive `const ArrowTpchDataset&` and never parse `.tbl`.
- Official Q5 revenue is accumulated at dollar scale 4 in signed `int64_t`; all intermediate products use `__int128` on CPU.
- Key columns and Q5 decimal columns reject nulls, wrong types, malformed rows, out-of-range values, and checksum mismatches.
- Chunked arrays must work with at least two chunks per column.
- CPU-only configure and tests must not search for CUDA.
- Every task is committed separately after its named tests pass.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `environment-cpu.yml` | Reproducible CPU development/runtime dependencies. |
| `environment-gpu.yml` | GPU environment pins shared by later plans. |
| `locks/environment-cpu-linux-64.lock.yml` | Fully solved CPU package versions and hashes. |
| `locks/environment-gpu-linux-64.lock.yml` | Fully solved GPU package versions and hashes. |
| `CMakePresets.json` | CPU debug/release and GPU release configurations. |
| `scripts/build_arrow_cuda.sh` | Build Arrow 23.0.1 with the required optional components. |
| `scripts/prepare_arrow_dataset.py` | Strict `.tbl` to Arrow IPC conversion and manifest generation. |
| `scripts/tpch_arrow_schema.py` | Single Python schema/table parsing definition. |
| `scripts/generate_synthetic_tpch_q5.py` | Deterministic density/selectivity-controlled synthetic source data. |
| `src/io/arrow_tpch_schema.hpp/.cpp` | C++ expected schemas and validation. |
| `src/io/dataset_manifest.hpp/.cpp` | Structured manifest parsing and SHA-256 verification. |
| `src/io/arrow_tpch_dataset.hpp/.cpp` | Own and load the six immutable Arrow tables. |
| `src/engine/q5_decimal.hpp/.cpp` | Exact revenue arithmetic and official two-decimal formatting. |
| `src/engine/q5_result.hpp` | Exact rows, timings, and counters shared by all plans. |
| `src/engine/q5_result_io.cpp` | CSV, JSON, JSONL, stable hash, and official formatting. |
| `src/engine/q5_plan.hpp/.cpp` | Dimension-map plan built from Arrow arrays. |
| `src/cpu/q5_specialized.hpp/.cpp` | Multi-threaded specialized Arrow-buffer scan. |
| `src/cpu/q5_acero.hpp/.cpp` | Generic Arrow Acero Q5 execution. |
| `src/cli/memq5.cpp` | Dataset-based engine dispatch and machine-readable output. |
| `tests/python/test_prepare_arrow_dataset.py` | Converter/schema/manifest tests. |
| `tests/test_arrow_dataset.cpp` | C++ schema, IPC, checksum, null, and chunk tests. |
| `tests/test_q5_decimal.cpp` | Exact arithmetic, rounding, overflow, and hash tests. |
| `tests/test_q5_specialized.cpp` | Tiny and threaded specialized backend tests. |
| `tests/test_q5_acero.cpp` | Tiny Arrow Acero semantic tests. |
| `tests/test_q5_oracle.py` | Tiny, official SF1, and DuckDB decimal comparisons. |

## Task 1: Pin The Arrow Build And Preserve The Baseline

**Files:**
- Create: `environment-cpu.yml`
- Create: `environment-gpu.yml`
- Create through conda-lock: `locks/environment-cpu-linux-64.lock.yml`
- Create through conda-lock: `locks/environment-gpu-linux-64.lock.yml`
- Create: `CMakePresets.json`
- Create: `scripts/build_arrow_cuda.sh`
- Modify: `CMakeLists.txt`
- Modify: `tests/CMakeLists.txt`

**Interfaces:**
- Consumes: Existing `memq5_core`, `memq5`, and CTest targets.
- Produces: CMake targets `Arrow::arrow_shared`, `ArrowAcero::arrow_acero_shared`, and, only for GPU presets, `ArrowCUDA::arrow_cuda_shared`.

- [ ] **Step 1: Capture the old baseline before changing dependencies**

Run:

```bash
cmake -S . -B build-baseline -DMEMQ5_ENABLE_CUDA=OFF -DMEMQ5_ENABLE_TESTS=ON
cmake --build build-baseline -j
ctest --test-dir build-baseline --output-on-failure
git status --short --branch
git rev-parse HEAD
```

Expected: configure/build succeed, all existing CPU tests pass, and the command output is saved under `results/baseline/pre-arrow/logs/` by the implementer without staging user-owned document changes.

- [ ] **Step 2: Write the dependency smoke test**

Create `tests/test_arrow_link.cpp`:

```cpp
#include <arrow/api.h>
#include <arrow/acero/api.h>

#include <cassert>

int main() {
  const auto schema = arrow::schema({arrow::field("k", arrow::int32(), false)});
  assert(schema->field(0)->type()->id() == arrow::Type::INT32);
  return 0;
}
```

Add the target to `tests/CMakeLists.txt` and link it to `memq5_core`.

- [ ] **Step 3: Run configure and verify the dependency test initially fails**

Run:

```bash
cmake -S . -B build-arrow -G Ninja -DMEMQ5_ENABLE_ARROW=ON -DMEMQ5_ENABLE_CUDA=OFF
```

Expected: FAIL because `MEMQ5_ENABLE_ARROW` and Arrow package discovery have not been defined.

- [ ] **Step 4: Add reproducible environment and CMake package discovery**

Write CPU pins with Python 3.11, PyArrow 23.0.1, DuckDB 1.4.*, CMake, Ninja, OpenSSL, nlohmann-json, pytest, psutil, pandas, matplotlib, and conda-lock. Write GPU pins by extending those packages with CUDA 12.x, cuDF 26.06.0, pynvml, and Nsight command availability checks. Solve and commit platform-specific lock files with:

```bash
conda-lock lock --file environment-cpu.yml --platform linux-64 --lockfile locks/environment-cpu-linux-64.lock.yml
conda-lock lock --file environment-gpu.yml --platform linux-64 --lockfile locks/environment-gpu-linux-64.lock.yml
```

Add this CMake shape:

```cmake
option(MEMQ5_ENABLE_ARROW "Build Apache Arrow storage and CPU backends" ON)
option(MEMQ5_ENABLE_CUDA "Build CUDA engines" OFF)

if(MEMQ5_ENABLE_ARROW)
  find_package(Arrow 23.0.1 CONFIG REQUIRED)
  find_package(ArrowAcero 23.0.1 CONFIG REQUIRED)
  find_package(OpenSSL REQUIRED)
  find_package(nlohmann_json CONFIG REQUIRED)
  target_link_libraries(memq5_core PUBLIC
    Arrow::arrow_shared
    ArrowAcero::arrow_acero_shared
    OpenSSL::Crypto
    nlohmann_json::nlohmann_json
  )
endif()

if(MEMQ5_ENABLE_CUDA)
  find_package(ArrowCUDA 23.0.1 CONFIG REQUIRED)
endif()
```

`scripts/build_arrow_cuda.sh` must download Apache Arrow 23.0.1 from the Apache release archive, verify a committed SHA-256 value, and configure these exact flags:

```bash
-DARROW_ACERO=ON
-DARROW_COMPUTE=ON
-DARROW_CSV=ON
-DARROW_CUDA=ON
-DARROW_DATASET=ON
-DARROW_IPC=ON
-DARROW_BUILD_SHARED=ON
-DARROW_BUILD_STATIC=OFF
-DARROW_DEPENDENCY_SOURCE=BUNDLED
```

- [ ] **Step 5: Build and run the dependency smoke test**

Run:

```bash
cmake --preset cpu-debug
cmake --build --preset cpu-debug -j
ctest --preset cpu-debug -R test_arrow_link --output-on-failure
```

Expected: `test_arrow_link` passes and `ldd build/cpu-debug/tests/test_arrow_link` resolves Arrow and Arrow Acero from the pinned prefix.

- [ ] **Step 6: Verify CPU-only isolation**

Run:

```bash
cmake --preset cpu-release
cmake --build --preset cpu-release -j
ctest --preset cpu-release --output-on-failure
```

Expected: all tests pass on a shell with `CUDA_VISIBLE_DEVICES` unset; CMake output contains no required CUDA package error.

- [ ] **Step 7: Commit the build foundation**

```bash
git add CMakeLists.txt tests/CMakeLists.txt tests/test_arrow_link.cpp environment-cpu.yml environment-gpu.yml locks/environment-cpu-linux-64.lock.yml locks/environment-gpu-linux-64.lock.yml CMakePresets.json scripts/build_arrow_cuda.sh
git commit -m "build: pin Arrow CPU and CUDA environments"
```

## Task 2: Convert TPC-H Tables Into Canonical Arrow IPC

**Files:**
- Create: `scripts/tpch_arrow_schema.py`
- Create: `scripts/prepare_arrow_dataset.py`
- Create: `tests/python/test_prepare_arrow_dataset.py`
- Modify: `scripts/validate_tpch_q5_data.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: A directory containing official `region.tbl`, `nation.tbl`, `supplier.tbl`, `customer.tbl`, `orders.tbl`, and `lineitem.tbl`.
- Produces: `prepare_dataset(input_dir: Path, output_dir: Path, scale_factor: str, batch_rows: int, source_command: str, validate_foreign_keys: bool) -> dict[str, object]`, the six files `region.arrow`, `nation.arrow`, `supplier.arrow`, `customer.arrow`, `orders.arrow`, `lineitem.arrow`, and `manifest.json`.

- [ ] **Step 1: Write schema and strict-parser tests**

Create pytest cases that assert:

```python
def test_q5_schema_types():
    schemas = q5_schemas()
    assert schemas["orders"].field("o_orderdate").type == pa.date32()
    assert schemas["lineitem"].field("l_extendedprice").type == pa.decimal128(15, 2)
    assert schemas["lineitem"].field("l_discount").type == pa.decimal128(15, 2)
    assert pa.types.is_dictionary(schemas["region"].field("r_name").type)

def test_bad_decimal_is_rejected(tmp_path):
    fixture = copy_fixture(tmp_path)
    replace_field(fixture / "lineitem.tbl", column=5, value="12.345")
    with pytest.raises(ValueError, match="lineitem.*l_extendedprice.*scale 2"):
        prepare_dataset(fixture, tmp_path / "ipc", "tiny", 2, "fixture", False)

def test_manifest_contains_checksums(tmp_path):
    manifest = prepare_dataset(FIXTURE, tmp_path / "ipc", "tiny", 2, "fixture", True)
    assert manifest["format_version"] == 1
    assert manifest["record_batch_rows"] == 2
    assert manifest["tables"]["lineitem"]["rows"] == 6
    assert len(manifest["tables"]["lineitem"]["sha256"]) == 64
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
pytest -q tests/python/test_prepare_arrow_dataset.py
```

Expected: collection fails because `scripts.tpch_arrow_schema` and `prepare_dataset` do not exist.

- [ ] **Step 3: Define the canonical schemas**

Implement `q5_schemas()` with these exact fields:

```python
NAME_TYPE = pa.dictionary(pa.int32(), pa.string())

def q5_schemas() -> dict[str, pa.Schema]:
    return {
        "region": pa.schema([
            pa.field("r_regionkey", pa.int32(), False),
            pa.field("r_name", NAME_TYPE, False),
        ]),
        "nation": pa.schema([
            pa.field("n_nationkey", pa.int32(), False),
            pa.field("n_name", NAME_TYPE, False),
            pa.field("n_regionkey", pa.int32(), False),
        ]),
        "supplier": pa.schema([
            pa.field("s_suppkey", pa.int32(), False),
            pa.field("s_nationkey", pa.int32(), False),
        ]),
        "customer": pa.schema([
            pa.field("c_custkey", pa.int32(), False),
            pa.field("c_nationkey", pa.int32(), False),
        ]),
        "orders": pa.schema([
            pa.field("o_orderkey", pa.int32(), False),
            pa.field("o_custkey", pa.int32(), False),
            pa.field("o_orderdate", pa.date32(), False),
        ]),
        "lineitem": pa.schema([
            pa.field("l_orderkey", pa.int32(), False),
            pa.field("l_suppkey", pa.int32(), False),
            pa.field("l_extendedprice", pa.decimal128(15, 2), False),
            pa.field("l_discount", pa.decimal128(15, 2), False),
        ]),
    }
```

Use `decimal.Decimal`, `date.fromisoformat`, explicit signed-32-bit bounds, exact field counts, and dictionary encoding. Do not parse money through `float`.

- [ ] **Step 4: Implement batched IPC and manifest output**

Use `pyarrow.csv.open_csv` so SF10 is never materialized as one Python table. Convert one streaming batch at a time with exact configured types, regroup rows through `iter_fixed_batches(reader, batch_rows)` so every non-final RecordBatch has the requested row count, dictionary-encode name columns, and write with `pa.ipc.new_file`. Emit this manifest structure with sorted keys and UTF-8 JSON:

```json
{
  "format_version": 1,
  "scale_factor": "1",
  "record_batch_rows": 1048576,
  "source": {"kind": "tpch-dbgen", "command": "dbgen -s 1"},
  "tables": {
    "region": {
      "file": "region.arrow",
      "rows": 5,
      "record_batches": 1,
      "schema": "r_regionkey: int32 not null\nr_name: dictionary<values=string, indices=int32> not null",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  }
}
```

Write files to a temporary sibling directory, fsync them, then atomically rename the directory so an interrupted conversion cannot look complete.

- [ ] **Step 5: Add foreign-key validation**

When enabled, check region/nation, nation/customer, nation/supplier, customer/orders, orders/lineitem, and supplier/lineitem key membership. Return counts in `manifest["validation"]` and raise a table/column-specific error on the first invalid key.

- [ ] **Step 6: Run converter tests and a tiny CLI smoke test**

Run:

```bash
pytest -q tests/python/test_prepare_arrow_dataset.py
python scripts/prepare_arrow_dataset.py --input tests/fixtures/tpch_q5_tiny --output /tmp/memq5-tiny-arrow --scale-factor tiny --batch-rows 2 --source-command fixture --validate-foreign-keys
python -m json.tool /tmp/memq5-tiny-arrow/manifest.json
```

Expected: all pytest cases pass; six IPC files and one manifest exist; lineitem has multiple record batches.

- [ ] **Step 7: Commit the canonical converter**

```bash
git add scripts/tpch_arrow_schema.py scripts/prepare_arrow_dataset.py scripts/validate_tpch_q5_data.py tests/python/test_prepare_arrow_dataset.py .gitignore
git commit -m "feat: prepare canonical Arrow TPC-H datasets"
```

## Task 3: Load And Validate Arrow IPC In C++

**Files:**
- Create: `src/io/arrow_tpch_schema.hpp`
- Create: `src/io/arrow_tpch_schema.cpp`
- Create: `src/io/dataset_manifest.hpp`
- Create: `src/io/dataset_manifest.cpp`
- Create: `src/io/arrow_tpch_dataset.hpp`
- Create: `src/io/arrow_tpch_dataset.cpp`
- Create: `tests/test_arrow_dataset.cpp`
- Modify: `CMakeLists.txt`
- Modify: `tests/CMakeLists.txt`

**Interfaces:**
- Consumes: The Task 2 dataset directory.
- Produces: `expected_tpch_q5_schemas()`, `DatasetManifest::Load`, `verify_dataset_file`, and `ArrowTpchDataset::Load` exactly as declared below.

```cpp
using TpchSchemaMap = std::unordered_map<std::string, std::shared_ptr<arrow::Schema>>;
TpchSchemaMap expected_tpch_q5_schemas();
arrow::Status validate_q5_table(const std::string& name,
                                const arrow::Table& table);

struct TableManifest {
  std::string file;
  int64_t rows;
  int64_t record_batches;
  std::string schema;
  std::string sha256;
};

struct DatasetManifest {
  int format_version;
  std::string scale_factor;
  int64_t record_batch_rows;
  std::unordered_map<std::string, TableManifest> tables;
  static arrow::Result<DatasetManifest> Load(const std::string& path);
};
```

- [ ] **Step 1: Write C++ load/validation tests**

The test executable must cover successful load, two-chunk lineitem, wrong schema, a null key, changed IPC bytes, missing manifest table, and manifest row-count mismatch:

```cpp
int main() {
  auto loaded = memq5::ArrowTpchDataset::Load(MEMQ5_ARROW_FIXTURE_DIR);
  assert(loaded.ok());
  assert((*loaded).lineitem->num_rows() == 6);
  assert((*loaded).lineitem->column(0)->num_chunks() >= 2);

  auto corrupt = memq5::ArrowTpchDataset::Load(MEMQ5_CORRUPT_FIXTURE_DIR);
  assert(!corrupt.ok());
  assert(corrupt.status().ToString().find("sha256") != std::string::npos);
  return 0;
}
```

Generate temporary invalid datasets in the test setup script rather than committing binary duplicates.

- [ ] **Step 2: Run and verify the loader test fails**

Run:

```bash
cmake --build --preset cpu-debug -j
ctest --preset cpu-debug -R test_arrow_dataset --output-on-failure
```

Expected: build fails because `ArrowTpchDataset` is undefined.

- [ ] **Step 3: Implement schema equality and null validation**

Build schemas with `arrow::dictionary(arrow::int32(), arrow::utf8())`, `arrow::date32()`, and `arrow::decimal128(15, 2)`. Return `arrow::Status::Invalid` containing table, column, expected type, actual type, and null count.

```cpp
arrow::Status require_field(const arrow::Table& table, const arrow::Field& expected) {
  const auto field = table.schema()->GetFieldByName(expected.name());
  if (!field || !field->type()->Equals(expected.type())) {
    return arrow::Status::Invalid("schema mismatch for ", expected.name());
  }
  const auto column = table.GetColumnByName(expected.name());
  if (!column || column->null_count() != 0) {
    return arrow::Status::Invalid("nulls are not allowed in ", expected.name());
  }
  return arrow::Status::OK();
}
```

- [ ] **Step 4: Parse the manifest structurally and verify SHA-256**

Use nlohmann-json type checks for every field. Reject unknown `format_version`, non-hex or non-64-character checksums, absolute file paths, `..` traversal, and duplicate/missing table entries. Compute SHA-256 with OpenSSL EVP and compare in constant time.

- [ ] **Step 5: Load IPC tables while retaining ownership**

For each table, open `arrow::io::ReadableFile`, create `arrow::ipc::RecordBatchFileReader`, read every record batch, and call `arrow::Table::FromRecordBatches`. Keep each resulting table in the returned dataset and verify manifest row/batch/schema counts before returning.

```cpp
struct ArrowTpchDataset {
  std::shared_ptr<arrow::Table> region;
  std::shared_ptr<arrow::Table> nation;
  std::shared_ptr<arrow::Table> supplier;
  std::shared_ptr<arrow::Table> customer;
  std::shared_ptr<arrow::Table> orders;
  std::shared_ptr<arrow::Table> lineitem;
  DatasetManifest manifest;

  static arrow::Result<ArrowTpchDataset> Load(const std::string& dataset_dir,
                                               bool verify_checksums = true);
};
```

- [ ] **Step 6: Run focused and full CPU tests**

Run:

```bash
ctest --preset cpu-debug -R test_arrow_dataset --output-on-failure
ctest --preset cpu-debug --output-on-failure
```

Expected: loader tests and all preserved baseline tests pass.

- [ ] **Step 7: Commit the C++ Arrow data layer**

```bash
git add CMakeLists.txt tests/CMakeLists.txt src/io/arrow_tpch_schema.hpp src/io/arrow_tpch_schema.cpp src/io/dataset_manifest.hpp src/io/dataset_manifest.cpp src/io/arrow_tpch_dataset.hpp src/io/arrow_tpch_dataset.cpp tests/test_arrow_dataset.cpp
git commit -m "feat: load verified Arrow IPC datasets"
```

## Task 4: Establish Exact Q5 Revenue And Result Semantics

**Files:**
- Create: `src/engine/q5_decimal.hpp`
- Create: `src/engine/q5_decimal.cpp`
- Create: `tests/test_q5_decimal.cpp`
- Modify: `src/engine/q5_result.hpp`
- Modify: `src/engine/q5_result_io.hpp`
- Modify: `src/engine/q5_result_io.cpp`
- Modify: `tests/test_result_io.cpp`
- Modify: `tests/CMakeLists.txt`

**Interfaces:**
- Consumes: Raw Decimal128 integer values where both canonical inputs have scale 2.
- Produces: `compute_revenue_1e4`, checked addition, exact hash, and official decimal formatting.

```cpp
arrow::Result<int64_t> compute_revenue_1e4(int64_t extendedprice_cents,
                                           int64_t discount_hundredths);
arrow::Result<int64_t> checked_add_revenue(int64_t left, int64_t right);
std::string format_revenue_2dp(int64_t revenue_1e4);
```

- [ ] **Step 1: Write arithmetic and output tests**

```cpp
assert(compute_revenue_1e4(10000, 10).ValueOrDie() == 900000);
assert(compute_revenue_1e4(12345, 6).ValueOrDie() == 1160430);
assert(format_revenue_2dp(1160430) == "116.04");
assert(format_revenue_2dp(1160450) == "116.05");
assert(!compute_revenue_1e4(INT64_MAX, 0).ok());
assert(!checked_add_revenue(INT64_MAX, 1).ok());
```

Add a regression where two lines each contribute fractional cents and only the post-sum rounding is allowed.

- [ ] **Step 2: Verify the tests fail against cent-truncating code**

Run:

```bash
cmake --build --preset cpu-debug -j
ctest --preset cpu-debug -R 'test_q5_decimal|test_result_io' --output-on-failure
```

Expected: build or assertion failure because only `revenue_cents` exists.

- [ ] **Step 3: Implement exact scale-4 arithmetic**

Use the raw scale-2 integers directly:

```cpp
arrow::Result<int64_t> compute_revenue_1e4(int64_t price_cents,
                                           int64_t discount_hundredths) {
  if (price_cents < 0 || discount_hundredths < 0 || discount_hundredths > 100) {
    return arrow::Status::Invalid("invalid Q5 decimal input");
  }
  const __int128 value = static_cast<__int128>(price_cents) *
                         (100 - discount_hundredths);
  if (value > std::numeric_limits<int64_t>::max()) {
    return arrow::Status::CapacityError("Q5 revenue overflow");
  }
  return static_cast<int64_t>(value);
}
```

Format positive TPC-H revenue by rounding scale 4 to scale 2 half away from zero. Keep the exact scale-4 value in the stable hash; never hash the formatted string alone.

- [ ] **Step 4: Expand shared result metrics**

Replace `revenue_cents` with `revenue_1e4`; add explicit C++17 `operator==`/`operator!=` for `Q5ResultRow`; rename old timing fields to the master-plan contract and append counters. Update JSON and CSV output field names so units are explicit.

- [ ] **Step 5: Run tests and compare the known SF1 formatting values**

Run:

```bash
ctest --preset cpu-debug -R 'test_q5_decimal|test_result_io' --output-on-failure
```

Expected: both pass and formatting accepts `555020411700` as `55502041.17`.

- [ ] **Step 6: Commit exact result semantics**

```bash
git add src/engine/q5_decimal.hpp src/engine/q5_decimal.cpp src/engine/q5_result.hpp src/engine/q5_result_io.hpp src/engine/q5_result_io.cpp tests/test_q5_decimal.cpp tests/test_result_io.cpp tests/CMakeLists.txt
git commit -m "fix: preserve exact TPC-H Q5 revenue"
```

## Task 5: Refactor The Specialized CPU Backend To Arrow Buffers

**Files:**
- Modify: `src/engine/q5_plan.hpp`
- Modify: `src/engine/q5_plan.cpp`
- Create: `src/cpu/q5_specialized.hpp`
- Create: `src/cpu/q5_specialized.cpp`
- Create: `src/io/arrow_array_view.hpp`
- Create: `tests/test_q5_specialized.cpp`
- Modify: `src/cli/memq5.cpp`
- Modify: `CMakeLists.txt`
- Modify: `tests/CMakeLists.txt`

**Interfaces:**
- Consumes: `const ArrowTpchDataset&`, `const Q5Params&`, and exact Decimal128 scale-2 columns.
- Produces: `build_q5_plan_cpu(const ArrowTpchDataset&, const Q5Params&)` and `execute_q5_specialized`.

```cpp
arrow::Result<Q5PreparedPlan> build_q5_plan_cpu(const ArrowTpchDataset& dataset,
                                                const Q5Params& params);
arrow::Result<Q5Result> execute_q5_specialized(const ArrowTpchDataset& dataset,
                                               const Q5Params& params);
```

- [ ] **Step 1: Write multi-chunk and thread-determinism tests**

Load the batch-size-2 tiny dataset and compare threads 1, 2, and 8:

```cpp
const auto one = execute_q5_specialized(dataset, ParamsWithThreads(1)).ValueOrDie();
const auto two = execute_q5_specialized(dataset, ParamsWithThreads(2)).ValueOrDie();
const auto eight = execute_q5_specialized(dataset, ParamsWithThreads(8)).ValueOrDie();
assert(result_hash_hex(one) == result_hash_hex(two));
assert(result_hash_hex(one) == result_hash_hex(eight));
assert(one.rows.at(0) == Q5ResultRow{"JAPAN", 1900000});
assert(one.rows.at(1) == Q5ResultRow{"INDIA", 900000});
```

Add invalid Decimal128-high-word and missing-region cases that must return Arrow errors.

- [ ] **Step 2: Verify the test fails before the refactor**

Run:

```bash
cmake --build --preset cpu-debug -j
ctest --preset cpu-debug -R test_q5_specialized --output-on-failure
```

Expected: build fails because the Arrow-specialized entry point does not exist.

- [ ] **Step 3: Implement checked Arrow array views**

`arrow_array_view.hpp` must expose chunk iteration without combining chunks:

```cpp
template <typename ArrowArray>
arrow::Result<std::vector<std::shared_ptr<ArrowArray>>> TypedChunks(
    const arrow::ChunkedArray& column, arrow::Type::type expected_id);

arrow::Result<int64_t> Decimal128RawInt64(const arrow::Decimal128Array& array,
                                          int64_t row);
```

Check type, null count, row range, and that Decimal128 values fit signed 64-bit before returning a raw integer.

- [ ] **Step 4: Rebuild dimension maps from Arrow tables**

Preserve the direct-address plan but iterate all chunks. Decode dictionary names through each chunk's dictionary; do not assume dictionary codes are shared across chunks. Store names by nation key and return an error for duplicate primary keys.

- [ ] **Step 5: Scan RecordBatch ranges with exact local aggregation**

Split work by global lineitem row ranges, map each range to chunk slices, and give every worker its own `std::vector<int64_t>`. Use `compute_revenue_1e4` and `checked_add_revenue`; propagate the first worker error after joining all threads.

```cpp
struct Q5ScanPartial {
  std::vector<int64_t> revenue_1e4_by_nation;
  int64_t input_rows = 0;
  int64_t matched_rows = 0;
  arrow::Status status = arrow::Status::OK();
};
```

- [ ] **Step 6: Switch the CLI to Arrow datasets**

Replace `--data-dir` with `--dataset`; accept `--engine cpu-specialized|arrow-acero`; load `ArrowTpchDataset` once, record `load_ms`, and propagate `arrow::Result` failures as a nonzero exit with one clear message. Keep `--data-dir` as a temporary rejected alias that prints the conversion command rather than silently parsing text.

- [ ] **Step 7: Run specialized CPU tests and a tiny CLI smoke test**

Run:

```bash
ctest --preset cpu-debug -R 'test_arrow_dataset|test_q5_decimal|test_q5_specialized' --output-on-failure
build/cpu-debug/memq5 --engine cpu-specialized --dataset /tmp/memq5-tiny-arrow --region ASIA --date 1994-01-01 --threads 2 --format json
```

Expected: tests pass; JSON has two rows, exact scale-4 values, `input_lineitem_rows=6`, and a nonzero matched-row count.

- [ ] **Step 8: Commit the Arrow-buffer CPU backend**

```bash
git add CMakeLists.txt tests/CMakeLists.txt src/io/arrow_array_view.hpp src/engine/q5_plan.hpp src/engine/q5_plan.cpp src/cpu/q5_specialized.hpp src/cpu/q5_specialized.cpp src/cli/memq5.cpp tests/test_q5_specialized.cpp
git commit -m "feat: execute specialized Q5 over Arrow buffers"
```

## Task 6: Implement The Arrow Acero CPU Backend

**Files:**
- Create: `src/cpu/q5_acero.hpp`
- Create: `src/cpu/q5_acero.cpp`
- Create: `tests/test_q5_acero.cpp`
- Modify: `src/cli/memq5.cpp`
- Modify: `CMakeLists.txt`
- Modify: `tests/CMakeLists.txt`

**Interfaces:**
- Consumes: `const ArrowTpchDataset&` and `const Q5Params&`.
- Produces: `arrow::Result<Q5Result> execute_q5_acero(const ArrowTpchDataset&, const Q5Params&)`.

- [ ] **Step 1: Write semantic equivalence tests**

```cpp
const auto specialized = execute_q5_specialized(dataset, params).ValueOrDie();
const auto acero = execute_q5_acero(dataset, params).ValueOrDie();
assert(result_hash_hex(specialized) == result_hash_hex(acero));
assert(acero.counters.input_lineitem_rows == dataset.lineitem->num_rows());
```

Add a date-boundary fixture where `1994-01-01` is included and `1995-01-01` is excluded, a non-ASIA query, and equal-revenue nation-name tie sorting.

- [ ] **Step 2: Verify the test fails**

Run:

```bash
cmake --build --preset cpu-debug -j
ctest --preset cpu-debug -R test_q5_acero --output-on-failure
```

Expected: build fails because `execute_q5_acero` is undefined.

- [ ] **Step 3: Build the Acero declaration pipeline**

Use `arrow::acero::Declaration` nodes in this order: table source, region filter, nation join, supplier join, customer join, orders date filter, customer-orders join, lineitem join, supplier join, same-nation filter, project exact Decimal128 revenue, aggregate by nation name, and order-by sink. Execute with `arrow::acero::DeclarationToTable` and a configured `arrow::compute::ExecContext`.

Use these explicit expressions for the half-open date predicate:

```cpp
compute::and_(
    compute::greater_equal(compute::field_ref("o_orderdate"),
                           compute::literal(params.start_date_days)),
    compute::less(compute::field_ref("o_orderdate"),
                  compute::literal(params.end_date_days)))
```

Before constructing the plan, call `arrow::SetCpuThreadPoolCapacity(params.threads)` and check its status. The CLI runs one Acero thread configuration per process, so the process-global pool setting is not changed concurrently.

Cast dictionary name columns to UTF-8 only at the operator boundary that requires plain strings; do not change the stored table schema.

- [ ] **Step 4: Convert Decimal128 aggregate output to exact rows**

Require result revenue scale 4, convert each Decimal128 value to a checked `int64_t`, sort by exact revenue descending and name ascending, and compute counters from pre/post-filter row counts exposed by explicit count aggregates.

- [ ] **Step 5: Run Acero, specialized, and full CPU tests**

Run:

```bash
ctest --preset cpu-debug -R 'test_q5_acero|test_q5_specialized' --output-on-failure
ctest --preset cpu-debug --output-on-failure
```

Expected: all tests pass; the two CPU engines have identical hashes on every fixture.

- [ ] **Step 6: Commit the Acero backend**

```bash
git add CMakeLists.txt tests/CMakeLists.txt src/cpu/q5_acero.hpp src/cpu/q5_acero.cpp src/cli/memq5.cpp tests/test_q5_acero.cpp
git commit -m "feat: add Arrow Acero Q5 backend"
```

## Task 7: Add Three-Layer Correctness Gates

**Files:**
- Create: `baselines/duckdb_oracle.py`
- Create: `scripts/verify_q5_oracle.py`
- Create: `tests/test_q5_oracle.py`
- Create: `tests/fixtures/q5_expected/tiny.csv`
- Create: `tests/fixtures/q5_expected/sf1.csv`
- Modify: `scripts/self_check.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Backend JSON output, official `dbgen/answers/q5.out`, or Arrow IPC dataset.
- Produces: `load_official_q5(path: Path) -> list[ResultRow]`, `run_duckdb_oracle(dataset_dir: Path, region: str, start_date: str) -> list[ResultRow]`, and a JSON correctness report.

- [ ] **Step 1: Write parser and comparison tests**

```python
def test_official_parser_reads_sf1():
    rows = load_official_q5(OFFICIAL_Q5)
    assert rows[0] == ResultRow("INDONESIA", Decimal("55502041.17"))
    assert rows[-1] == ResultRow("JAPAN", Decimal("45410175.70"))

def test_exact_difference_is_reported():
    report = compare_results([ResultRow("A", Decimal("1.00"))],
                             [ResultRow("A", Decimal("1.01"))])
    assert report["ok"] is False
    assert report["differences"][0]["delta"] == "-0.01"
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest -q tests/test_q5_oracle.py
```

Expected: import failure because the oracle helpers do not exist.

- [ ] **Step 3: Implement DuckDB decimal SQL over Arrow tables**

Load IPC with PyArrow, register each table in DuckDB, and execute exact SQL with explicit decimal casts:

```sql
sum(
  cast(l_extendedprice as decimal(18,2)) *
  (cast(1 as decimal(18,2)) - cast(l_discount as decimal(18,2)))
) as revenue
```

Bind region and dates as parameters. Return `decimal.Decimal` values and deterministic name tie ordering.

- [ ] **Step 4: Implement official-output comparison**

Parse the pipe-delimited answer after its header, quantize backend scale-4 values to `Decimal("0.01")` using `ROUND_HALF_UP`, compare names/order/values, and include both exact and formatted values in `correctness.json`.

- [ ] **Step 5: Run tiny and SF1 correctness gates**

Run:

```bash
python scripts/verify_q5_oracle.py --dataset /tmp/memq5-tiny-arrow --backend-command 'build/cpu-debug/memq5 --engine cpu-specialized --dataset /tmp/memq5-tiny-arrow --format json' --expected tests/fixtures/q5_expected/tiny.csv
python scripts/prepare_arrow_dataset.py --input data/tpch_sf1 --output /tmp/memq5-sf1-arrow --scale-factor 1 --batch-rows 1048576 --source-command 'dbgen -s 1' --validate-foreign-keys
python scripts/verify_q5_oracle.py --dataset /tmp/memq5-sf1-arrow --engines arrow-acero,cpu-specialized --memq5 build/cpu-debug/memq5 --expected 'data/tpch_tools/TPC-H V3.0.1/dbgen/answers/q5.out'
```

Expected: tiny and both SF1 CPU engines report `ok: true`; the official nation values match to two decimals.

- [ ] **Step 6: Run the complete CPU self-check**

Run:

```bash
python scripts/self_check.py --preset cpu-debug --tiny-dataset /tmp/memq5-tiny-arrow --sf1-dataset /tmp/memq5-sf1-arrow --official-q5 'data/tpch_tools/TPC-H V3.0.1/dbgen/answers/q5.out'
```

Expected: dependency, converter, CTest, pytest, tiny oracle, and SF1 oracle sections all pass.

- [ ] **Step 7: Commit the correctness gates**

```bash
git add baselines/duckdb_oracle.py scripts/verify_q5_oracle.py scripts/self_check.py tests/test_q5_oracle.py tests/fixtures/q5_expected/tiny.csv tests/fixtures/q5_expected/sf1.csv README.md
git commit -m "test: enforce TPC-H Q5 oracle correctness"
```

## Task 8: Make Synthetic Selectivity And Key Density Reproducible

**Files:**
- Modify: `scripts/generate_synthetic_tpch_q5.py`
- Create: `tests/python/test_synthetic_generator.py`
- Modify: `scripts/prepare_tpch_q5_data.py`

**Interfaces:**
- Consumes: row counts, `--key-density`, `--match-selectivity`, `--asia-selectivity`, and `--seed`.
- Produces: deterministic valid `.tbl`, a `synthetic-manifest.json`, and optional canonical Arrow IPC conversion.

- [ ] **Step 1: Write deterministic control tests**

```python
def test_same_seed_is_byte_identical(tmp_path):
    first = generate_fixture(tmp_path / "a", seed=7, lineitems=1000)
    second = generate_fixture(tmp_path / "b", seed=7, lineitems=1000)
    assert tree_sha256(first) == tree_sha256(second)

def test_selectivity_and_density_are_recorded(tmp_path):
    output = generate_fixture(tmp_path / "data", seed=3, lineitems=10000,
                              key_density=.25, match_selectivity=.40,
                              asia_selectivity=.60)
    manifest = json.loads((output / "synthetic-manifest.json").read_text())
    assert abs(manifest["observed"]["match_selectivity"] - .40) <= .01
    assert abs(manifest["observed"]["key_density"] - .25) <= .01
```

- [ ] **Step 2: Run and verify current generator fails the contract**

Run:

```bash
pytest -q tests/python/test_synthetic_generator.py
```

Expected: failures because the current generator has no explicit density/selectivity manifest.

- [ ] **Step 3: Implement deterministic controls and non-destructive output**

Add validated float arguments in `[0,1]`; use a seeded permutation to choose exact accepted/mismatched lineitem counts; derive distinct order/supplier key counts from key density; record requested/observed parameters, row counts, seed, generator command, and per-file SHA-256. Refuse an existing output directory unless `--replace` is explicit.

- [ ] **Step 4: Add optional Arrow conversion**

`--arrow-output` calls the Task 2 preparation API with the synthetic source command and fixed RecordBatch size. The Arrow manifest links `synthetic-manifest.json` by SHA-256.

- [ ] **Step 5: Run generator, converter, and oracle tests**

Run:

```bash
pytest -q tests/python/test_synthetic_generator.py
python scripts/generate_synthetic_tpch_q5.py --output /tmp/memq5-synthetic-tbl --customers 1000 --orders 5000 --lineitems 20000 --suppliers 500 --key-density 0.5 --match-selectivity 0.4 --asia-selectivity 0.6 --seed 7 --arrow-output /tmp/memq5-synthetic-arrow --batch-rows 4096
python scripts/verify_q5_oracle.py --dataset /tmp/memq5-synthetic-arrow --engines arrow-acero,cpu-specialized --duckdb-oracle
```

Expected: deterministic tests pass; both CPU backends match DuckDB exact output.

- [ ] **Step 6: Commit the controlled generator**

```bash
git add scripts/generate_synthetic_tpch_q5.py scripts/prepare_tpch_q5_data.py tests/python/test_synthetic_generator.py
git commit -m "feat: control synthetic Q5 selectivity and density"
```

## Plan Acceptance

Run:

```bash
cmake --preset cpu-release
cmake --build --preset cpu-release -j
ctest --preset cpu-release --output-on-failure
pytest -q tests/python tests/test_q5_oracle.py
python scripts/self_check.py --preset cpu-release --tiny-dataset /tmp/memq5-tiny-arrow --sf1-dataset /tmp/memq5-sf1-arrow --official-q5 'data/tpch_tools/TPC-H V3.0.1/dbgen/answers/q5.out'
rg -n 'load_tpch|\.tbl' src/cpu src/engine src/cli
```

Expected:

- All CPU tests and both oracle gates pass.
- The final `rg` command finds no backend path that calls the old text loader.
- Arrow IPC manifest checksums and schemas are verified on load.
- `arrow-acero` and `cpu-specialized` produce identical exact hashes and official SF1 formatted values.
- Existing custom-column files may remain temporarily for baseline history but are no longer linked into the main executable.

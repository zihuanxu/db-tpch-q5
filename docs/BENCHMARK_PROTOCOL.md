# Benchmark Protocol

## 1. Goals

The benchmark must answer four questions:

1. Is the custom engine correct for TPC-H Q5?
2. How much faster or slower is GPU execution than CPU execution?
3. How much does host-device transfer policy matter?
4. How does the custom engine compare with Arrow/Acero and RAPIDS cuDF?

## 2. Environment Recording

Every benchmark run should write an environment JSON containing:

- date/time,
- hostname,
- CPU model and core count,
- cache hierarchy if available,
- total RAM,
- NUMA topology if available,
- GPU model,
- CUDA runtime/driver version,
- compiler versions,
- git commit hash or `dirty` marker,
- command-line arguments.

Recommended commands:

```bash
lscpu
numactl --hardware
nvidia-smi
nvcc --version
g++ --version
cmake --version
```

If a command is unavailable, record that fact instead of failing the entire run.

## 3. Data

### Fixture Data

Small deterministic fixture data must be committed under `tests/fixtures`.

Purpose:

- unit tests,
- CI/smoke tests,
- exact expected output,
- no external dbgen dependency.

### TPC-H Data

Generated data should use the official TPC-H dbgen toolkit.

Default query parameters:

- `REGION = ASIA`
- `DATE = 1994-01-01`

Required scale factors:

- SF1 for final minimum result.

Optional scale factors:

- SF0.01 or generated small scale for quick local checks,
- SF10 or larger when hardware allows.

## 4. Engines

Required engines:

- `cpu`
- `gpu-copy`
- `gpu-mapped`
- `gpu-managed`
- `arrow`
- `cudf`

Optional engines:

- `duckdb`

The custom CPU engine is the primary correctness oracle. DuckDB is the SQL
oracle when installed.

## 5. Repeats

For each `(engine, scale, region, date)` combination:

1. Load data.
2. Run one warm-up query.
3. Run at least five measured repeats.
4. Report median, min, max, and p95.

For GPU engines, use CUDA events for kernel and transfer timing and host timers
for total end-to-end timing.

## 6. Output CSV Schema

The benchmark runner should emit one row per measured repeat:

```text
timestamp,
engine,
scale_factor,
region,
date,
repeat_id,
status,
total_ms,
load_ms,
build_ms,
h2d_ms,
kernel_ms,
d2h_ms,
cpu_probe_ms,
rows_lineitem,
rows_orders,
rows_accepted,
bytes_h2d,
bytes_d2h,
peak_rss_bytes,
result_hash,
notes
```

`result_hash` should be a stable hash of sorted output rows. It is used to catch
silent correctness drift in benchmark runs.

## 7. Correctness Criteria

The benchmark row is valid only if:

1. output row count matches CPU output,
2. nation names match CPU output,
3. revenue values match CPU output exactly when fixed-point is used,
4. output order is revenue descending,
5. `result_hash` matches the CPU baseline for the same data and parameters.

If DuckDB is available, CPU must also match DuckDB.

## 8. Plots

Required plots:

- total query time by engine,
- throughput in lineitem rows/s by engine,
- GPU timing breakdown: H2D, kernel, D2H,
- transfer mode comparison: explicit copy vs mapped vs managed,
- memory footprint by engine,
- baseline comparison: custom CPU vs Arrow/Acero, custom GPU vs cuDF.

Optional plots:

- scale-factor sweep,
- chunk-size sweep for GPU,
- CPU thread-count sweep,
- accepted-row selectivity by region/date.

## 9. Interpretation Checklist

The final report should explicitly discuss:

- why Q5 can be implemented as filter propagation plus final fact scan,
- why `region`/`nation` fit compact bitmaps/arrays,
- why date is an integer range predicate,
- when GPU launch/transfer overhead dominates,
- when explicit copy beats mapped/UVA-style access,
- whether managed memory prefetch helps,
- where Arrow/cuDF baselines are stronger or weaker,
- limitations caused by hardware, driver, scale factor, or optional dependencies.

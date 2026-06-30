# GPU Server Runbook

This runbook is the required next step after local development. The current
machine can compile CUDA code with `nvcc`, but cannot run GPU kernels because
`nvidia-smi` cannot communicate with the NVIDIA driver.

## 1. Check The Server

Run:

```bash
nvidia-smi
nvcc --version
cmake --version
python3 --version
```

Expected:

- `nvidia-smi` prints one or more GPUs.
- `nvcc` is available.
- CMake is available.
- Python 3 is available.

Record the environment:

```bash
python3 scripts/capture_environment.py --output results/environment_gpu.json
```

## 2. Choose CUDA Architecture

Use the GPU model from `nvidia-smi` and choose the matching CMake architecture.
Common values:

| GPU family | CMake value |
| --- | --- |
| Turing T4 | `75` |
| Ampere A100 | `80` |
| Ampere RTX 30xx / A10 | `86` |
| Hopper H100 | `90` |

If uncertain, use the lowest compatible architecture for the server GPU or ask
the administrator.

## 3. Build

```bash
cmake -S . -B build-cuda \
  -DMEMQ5_ENABLE_CUDA=ON \
  -DMEMQ5_ENABLE_TESTS=ON \
  -DCMAKE_CUDA_ARCHITECTURES=<arch>

cmake --build build-cuda
```

## 4. Run Tests

```bash
ctest --test-dir build-cuda --output-on-failure
```

On a working GPU server, `test_q5_cuda` should actually run `gpu-copy`,
`gpu-managed`, and `gpu-mapped`, then verify all result hashes match CPU.

## 5. Tiny Correctness Experiment

```bash
python3 scripts/run_experiment_pipeline.py \
  --name tiny_gpu_modes \
  --memq5 build-cuda/memq5 \
  --data-dir tests/fixtures/tpch_q5_tiny \
  --engines cpu,gpu-copy,gpu-managed,gpu-mapped,python \
  --repeat 5 \
  --force
```

Check:

```bash
cat results/experiments/tiny_gpu_modes/hash_check.txt
cat results/experiments/tiny_gpu_modes/summary.md
```

Expected:

- `hash_check.txt` reports one hash for all successful engines.
- No GPU engine should be listed as an error.

## 6. Synthetic Development Experiment

Generate deterministic development data:

```bash
python3 scripts/generate_synthetic_tpch_q5.py \
  --output data/synthetic_gpu_dev \
  --customers 10000 \
  --orders 50000 \
  --lineitems 200000 \
  --suppliers 5000 \
  --asia-heavy
```

Run:

```bash
python3 scripts/run_experiment_pipeline.py \
  --name synthetic_gpu_modes \
  --memq5 build-cuda/memq5 \
  --data-dir data/synthetic_gpu_dev \
  --engines cpu,gpu-copy,gpu-managed,gpu-mapped,python \
  --thread-list 1,2,4,8 \
  --repeat 5 \
  --force
```

This experiment is for debugging and trend checks only. It is not a substitute
for final TPC-H dbgen results.

## 7. Official TPC-H Data

After obtaining official TPC-H dbgen `.tbl` files, prepare the Q5 subset:

```bash
python3 scripts/prepare_tpch_q5_data.py \
  --source-dir /path/to/dbgen-output \
  --output-dir data/tpch_sf1 \
  --scale-factor 1 \
  --mode copy \
  --force
```

Validate:

```bash
python3 scripts/validate_tpch_q5_data.py --data-dir data/tpch_sf1
```

Run:

```bash
python3 scripts/run_experiment_pipeline.py \
  --name tpch_sf1_gpu_modes \
  --memq5 build-cuda/memq5 \
  --data-dir data/tpch_sf1 \
  --engines cpu,gpu-copy,gpu-managed,gpu-mapped \
  --thread-list 1,2,4,8 \
  --repeat 5 \
  --force
```

If RAPIDS cuDF is installed:

```bash
python3 scripts/run_experiment_pipeline.py \
  --name tpch_sf1_with_cudf \
  --memq5 build-cuda/memq5 \
  --data-dir data/tpch_sf1 \
  --engines cpu,gpu-copy,gpu-managed,gpu-mapped,cudf \
  --thread-list 1,2,4,8 \
  --repeat 5 \
  --allow-benchmark-errors \
  --force
```

## 8. Required Artifacts For The Report

For every final experiment directory, keep:

- `README.md`
- `validation.json`
- `environment.json`
- `benchmarks.csv`
- `hash_check.txt`
- `summary.md`
- `assets/summary.md`
- `assets/total_time.svg`
- `assets/time_breakdown.svg`

These are enough to write the correctness, setup, result, and analysis sections
of the final report.

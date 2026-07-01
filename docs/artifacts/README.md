# GPU Validation Audit Artifacts

Date: 2026-07-01

This directory records the minimal audit evidence for the `gpu-results` branch.
Large generated directories are intentionally not committed. Raw local outputs
were produced under `results/experiments/`, while the report-ready SVG figures
were copied to `docs/assets/`.

## Environment

- GPU runtime host: NVIDIA GeForce RTX 4090 selected with `CUDA_VISIBLE_DEVICES=0`.
- GPU compute capability: 8.9.
- CMake CUDA architecture: `89`.
- Driver/runtime from `nvidia-smi`: driver `595.71.05`, CUDA runtime `13.2`.
- `nvcc --version` on `PATH`: CUDA `12.6`, `V12.6.85`.
- CMake selected CUDA compiler: `/usr/bin/nvcc`, CUDA `12.0.140`.
- CMake: `4.3.0`.
- Python: `3.11.15`.
- RAPIDS cuDF: not installed in the active Python environment.
- Official tools zip: `TPC-H-Tool.zip`.
- Official tools zip SHA256:
  `97ccb34cd122d78c2e06e2419e50957f934256868b37c02d0b88aefd9d13a84a`.

## Build And CTest

Commands:

```bash
cmake -S . -B build-cuda \
  -DMEMQ5_ENABLE_CUDA=ON \
  -DMEMQ5_ENABLE_TESTS=ON \
  -DCMAKE_CUDA_ARCHITECTURES=89
cmake --build build-cuda
CUDA_VISIBLE_DEVICES=0 ctest --test-dir build-cuda --output-on-failure
```

Result:

- `ctest`: 6/6 tests passed.
- `test_q5_cuda`: passed on a real NVIDIA GPU; it did not skip for missing
  devices.

## Hash Evidence

Tiny GPU correctness experiment:

```text
ok ASIA 1994-01-01 hash=1e07d78fa8eededb engines=cpu,gpu-copy,gpu-managed,gpu-mapped,python
```

Synthetic GPU development experiment:

```text
ok ASIA 1994-01-01 hash=d5ffe393223a207e engines=cpu,gpu-copy,gpu-managed,gpu-mapped,python
```

Official TPC-H SF1 experiment:

```text
ok ASIA 1994-01-01 hash=9f1f5f7578dd816e engines=cpu,gpu-copy,gpu-managed,gpu-mapped
```

The hash checks show that successful CPU, GPU, and Python runs agreed within
each experiment.

## Official SF1 Data Evidence

The Q5 subset prepared by `scripts/prepare_tpch_q5_data.py` contained:

- `region.tbl`: 5 rows
- `nation.tbl`: 25 rows
- `supplier.tbl`: 10,000 rows
- `customer.tbl`: 150,000 rows
- `orders.tbl`: 1,500,000 rows
- `lineitem.tbl`: 6,001,215 rows
- orders in the `1994-01-01` to `1995-01-01` date window: 227,597

## Committed Report Assets

- `docs/assets/tiny_gpu_modes_total_time.svg`
- `docs/assets/tiny_gpu_modes_time_breakdown.svg`
- `docs/assets/synthetic_gpu_modes_total_time.svg`
- `docs/assets/synthetic_gpu_modes_time_breakdown.svg`
- `docs/assets/tpch_sf1_gpu_modes_total_time.svg`
- `docs/assets/tpch_sf1_gpu_modes_time_breakdown.svg`

## Open Items

- The cuDF baseline was not run because RAPIDS/cuDF was not installed in the
  active Python environment.
- `build/`, `build-cuda/`, `data/`, `dist/`, and `results/` remain excluded
  from version control.

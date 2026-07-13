#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
: "${CONDA_PREFIX:?scripts/ci_cpu.sh must run inside the Arrow CPU Conda environment}"

cmake --fresh -S . -B build-ci-arrow \
  -G Ninja \
  -DMEMQ5_ENABLE_ARROW=ON \
  -DMEMQ5_ENABLE_CUDA=OFF \
  -DMEMQ5_ENABLE_TESTS=ON \
  -DOPENSSL_ROOT_DIR="$CONDA_PREFIX" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build-ci-arrow --parallel 2
ctest --test-dir build-ci-arrow --output-on-failure

python -m pytest -q tests/python tests/test_release_files.py tests/test_ci_config.py

python scripts/prepare_arrow_dataset.py \
  --input tests/fixtures/tpch_q5_tiny \
  --output /tmp/memq5-ci-arrow \
  --scale-factor tiny \
  --batch-rows 2 \
  --source-command "CI tiny fixture" \
  --replace

for engine in cpu-specialized arrow-acero; do
  build-ci-arrow/memq5_arrow_query \
    --dataset /tmp/memq5-ci-arrow \
    --engine "$engine" \
    --region ASIA \
    --date 1994-01-01 \
    --threads 2 \
    --format csv > "/tmp/memq5-${engine}.csv"
  python scripts/verify_q5_oracle.py \
    --actual "/tmp/memq5-${engine}.csv" \
    --oracle tests/fixtures/tpch_q5_tiny_oracle.out
done

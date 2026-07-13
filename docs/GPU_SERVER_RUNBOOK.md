# GPU Server Runbook

V6 runbook，最后核对 2026-07-14。正式环境为 GPU 0 RTX 4090、compute
capability 8.9、driver 595.71.05、nvcc 12.6、Arrow 23.0.1、cuDF 26.06.00。
正式 SF1 hash 为 `542abf4003633c7c`。

## 1. 检查环境

```bash
nvidia-smi
nvcc --version
cmake --version
conda run -n memq5-cudf python -c \
  'import pyarrow,cudf; print(pyarrow.__version__, cudf.__version__)'
```

RTX 4090/L20 使用 `CMAKE_CUDA_ARCHITECTURES=89`。驱动显示的 CUDA Version
是驱动支持上限，不等于 `nvcc` 工具链版本。

## 2. Arrow+CUDA Release 构建

```bash
cmake -S . -B build-arrow-cuda-release \
  -DMEMQ5_ENABLE_ARROW=ON \
  -DMEMQ5_ENABLE_CUDA=ON \
  -DMEMQ5_ENABLE_TESTS=ON \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=89
cmake --build build-arrow-cuda-release -j 8
```

## 3. 测试门禁

```bash
CUDA_VISIBLE_DEVICES=0 ctest \
  --test-dir build-arrow-cuda-release \
  --output-on-failure
```

真实 GPU 上应通过 21/21。无 GPU 环境中 GPU runtime tests 返回 77，由 CTest
标成 skipped；不能把 skipped 说成真实运行通过。

cuDF/PyArrow Python 3.11 测试：

```bash
CUDA_VISIBLE_DEVICES=0 conda run -n memq5-cudf \
  python -m pytest -q tests/python/test_arrow_dataset_mvp.py \
  tests/python/test_baseline_exact_mvp.py tests/python/test_cudf_q5.py
```

如果 RAPIDS 环境没有 pytest，可把同为 Python 3.11 的临时 pytest site-packages
加入 `PYTHONPATH`；不要混用 Python 3.13 site-packages。

## 4. tiny 正确性

```bash
CUDA_VISIBLE_DEVICES=0 ./build-arrow-cuda-release/memq5_arrow_query \
  --engine gpu-copy \
  --dataset tests/fixtures/tpch_q5_tiny_arrow \
  --region ASIA --date 1994-01-01 --format rows
```

预期 JAPAN 190.00、INDIA 90.00，hash `248d10b6ee352953`。再对 managed、
mapped、hybrid 和 CPU 重复，不能只看进程退出码。

## 5. compute-sanitizer

```bash
CUDA_VISIBLE_DEVICES=0 compute-sanitizer --tool memcheck \
  ./build-arrow-cuda-release/memq5_arrow_query \
  --engine hybrid-arrow --cpu-ratio 0.5 \
  --dataset tests/fixtures/tpch_q5_tiny_arrow \
  --region ASIA --date 1994-01-01 --format benchmark
```

预期 `ERROR SUMMARY: 0 errors`。

## 6. 准备官方 Arrow 数据

TPC-H tools 和生成的 `.tbl` 不随仓库分发。先合法获得 dbgen，再运行：

```bash
python3 scripts/prepare_tpch_q5_data.py \
  --source-dir /path/to/dbgen-output \
  --output-dir data/tpch_sf1 \
  --scale-factor 1 --mode copy --force

conda run -n memq5-cudf python scripts/prepare_arrow_dataset.py \
  --input data/tpch_sf1 \
  --output data/tpch_sf1_arrow \
  --scale-factor 1 \
  --batch-rows 262144 \
  --source-command 'TPC-H V3.0.1 dbgen -s 1' \
  --replace
```

## 7. 正式 V5 矩阵

矩阵由 `experiments/v5_formal_sf1.yml` 冻结，不手工修改 engine/thread 组合：

```bash
CUDA_VISIBLE_DEVICES=0 python3 scripts/run_benchmarks.py \
  --matrix experiments/v5_formal_sf1.yml \
  --arrow-cli build-arrow-cuda-release/memq5_arrow_query \
  --arrow-dataset data/tpch_sf1_arrow \
  --output results/v5_sf1/raw.csv
```

运行结束后生成 summary/environment，再 finalize。已冻结结果在
`docs/artifacts/v5_sf1`，通常只需审计：

```bash
python3 scripts/benchmark_schema.py validate docs/artifacts/v5_sf1/raw.csv
python3 scripts/evidence_bundle.py audit --directory docs/artifacts/v5_sf1
```

预期：190 measured、57 warmups、checksum/matrix/coverage/summary 全部无错误。

## 8. profiler 边界

当前只证明 hybrid backend duration overlap，没有 Nsight timeline。若新增 profiler：

```bash
CUDA_VISIBLE_DEVICES=0 nsys profile \
  -o results/profiles/hybrid_sf1 \
  ./build-arrow-cuda-release/memq5_arrow_query \
  --engine hybrid-arrow --cpu-ratio 0.75 \
  --dataset data/tpch_sf1_arrow \
  --region ASIA --date 1994-01-01 --format benchmark
```

只有 timeline 能定位 CPU scan 与 CUDA kernel 是否重叠。生成的 `.nsys-rep` 不放
入最小源码包，报告图应带 profiler 版本、命令和 checksum。

## 9. 失败分类

- return 77：`SKIPPED_NO_GPU`；
- CUDA allocation failure：`ERROR_CUDA_OOM`；
- timeout：`ERROR_TIMEOUT`；
- executable/Conda 启动失败：`ERROR_PROCESS_LAUNCH`；
- 其他非零退出：`ERROR_PROCESS_EXIT`。

失败记录必须保留 stdout/stderr，不能从 CSV 删除后声称矩阵完整。

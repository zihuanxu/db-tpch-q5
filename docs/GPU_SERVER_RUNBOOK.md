# GPU Server Runbook

V7 runbook，最后核对 2026-07-14。正式环境为 GPU 0 RTX 4090、compute
capability 8.9、driver 595.71.05、nvcc 12.6、Arrow 23.0.1、cuDF 26.06.00。
正式 SF1 hash 为 `542abf4003633c7c`，SF10 hash 为 `b1351a421ba8dcfd`。

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

真实 GPU 上当前应通过 45/45。无 GPU 环境中 GPU runtime tests 返回 77，由 CTest
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

## 7. 正式 V7 常驻矩阵

SF1、SF10矩阵分别由 `experiments/v7_formal_sf1.yml` 和
`experiments/v7_formal_sf10.yml` 冻结。runner会为每个配置启动一次进程，先完成
setup，再在同一常驻会话内执行3次warmup和10次measured request。不要把setup
时间混入request延迟。

```bash
CUDA_VISIBLE_DEVICES=0 python3 scripts/run_v7_benchmarks.py \
  --matrix experiments/v7_formal_sf1.yml \
  --session-cli build-arrow-cuda-release/memq5_arrow_session \
  --cudf-env memq5-cudf --gpu-index 0 \
  --output-dir results/v7_sf1_resident

CUDA_VISIBLE_DEVICES=0 python3 scripts/run_v7_benchmarks.py \
  --matrix experiments/v7_formal_sf10.yml \
  --session-cli build-arrow-cuda-release/memq5_arrow_session \
  --cudf-env memq5-cudf --gpu-index 0 \
  --output-dir results/v7_sf10_resident
```

对两个目录分别生成独立oracle核对记录并finalize。下面以SF1为例，SF10只需替换
目录、矩阵、数据manifest和oracle路径：

```bash
python3 scripts/materialize_v7_correctness.py \
  --bundle results/v7_sf1_resident \
  --matrix experiments/v7_formal_sf1.yml \
  --oracle experiments/oracles/v7_sf1_q5.json \
  --output results/v7_sf1_resident/correctness.json

python3 scripts/v7_evidence_bundle.py finalize \
  --directory results/v7_sf1_resident \
  --matrix experiments/v7_formal_sf1.yml \
  --dataset-manifest data/tpch_sf1_arrow/manifest.json \
  --oracle experiments/oracles/v7_sf1_q5.json \
  --correctness results/v7_sf1_resident/correctness.json

python3 scripts/v7_evidence_bundle.py audit \
  --directory results/v7_sf1_resident
```

每个规模预期为18个配置、54次warmup、180次measured request，且8类正确性
后端全部通过。仓库冻结副本位于 `docs/artifacts/v7_sf1_resident` 和
`docs/artifacts/v7_sf10_resident`。

## 8. hybrid模型与profiler

固定比例与auto模型的比较由正式结果自动生成：

```bash
python3 scripts/evaluate_hybrid_model.py \
  results/v7_sf1_resident results/v7_sf10_resident \
  --json-out results/v7_hybrid_model/model.json \
  --csv-out results/v7_hybrid_model/model.csv \
  --markdown-out results/v7_hybrid_model/model.md
```

正式剖析使用编排器采集SF1/SF10的copy、managed、mapped、hybrid-fixed，共10个
NSYS/NCU profile。参数必须引用已经通过审计的两个V7证据包；GPU UUID可由
`nvidia-smi -L` 获取。仓库的精简、可校验副本位于
`docs/artifacts/v7_profiler`，原始完整bundle不放进最小交付包。
NSYS会把目标进程环境写进report，因此采集器默认使用
`--inherit-environment=false`。不要把未经 `export_v7_profiler_evidence.py`
处理的 `.nsys-rep` 放入公开仓库。

```bash
CUDA_VISIBLE_DEVICES=0 python3 scripts/run_v7_profilers.py \
  --output-dir results/v7_profiler \
  --session-cli build-arrow-cuda-release/memq5_arrow_session \
  --sf1-evidence results/v7_sf1_resident \
  --sf10-evidence results/v7_sf10_resident \
  --sf1-data data/tpch_sf1_arrow \
  --sf10-data data/tpch_sf10_arrow \
  --gpu-index 0 --gpu-uuid GPU-3bbdf12f-4f01-2280-3744-e42f3544e76e \
  --sf1-hybrid-fixed-ratio 0.5 \
  --sf10-hybrid-fixed-ratio 0.5

python3 scripts/v7_profiler_bundle.py audit --directory results/v7_profiler
```

profiler数据只用于解释kernel、内存访问和阶段重叠，不与普通请求延迟混算。

## 9. 失败分类

- return 77：`SKIPPED_NO_GPU`；
- CUDA allocation failure：`ERROR_CUDA_OOM`；
- timeout：`ERROR_TIMEOUT`；
- executable/Conda 启动失败：`ERROR_PROCESS_LAUNCH`；
- 其他非零退出：`ERROR_PROCESS_EXIT`。

失败记录必须保留 stdout/stderr，不能从 CSV 删除后声称矩阵完整。

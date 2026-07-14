# TPC-H Q5 CPU-GPU 协同查询实验

这是内存数据库课程的实验代码。项目固定实现 TPC-H Q5，比较 CPU、Apache
Arrow、CUDA 不同内存访问方式、cuDF 和 CPU-GPU 混合执行的查询时间。它只实现
Q5 的物理执行过程，不是带 SQL 解析器和通用优化器的完整数据库。

## 目录

| 路径 | 内容 |
|---|---|
| `hashjoin-cpu/` | 前期 CPU 哈希连接平时作业 |
| `q5/` | Q5 源码、对照实现、实验配置、测试和环境文件 |
| `scripts/` | 数据准备、实验执行和结果校验脚本 |
| `results/` | SF1、SF10 和 profiler 实验结果 |

## 实现方法

- `cpu-specialized`：针对 Q5 构造索引，然后并行扫描 `lineitem`。
- `arrow-acero`：使用 Arrow Acero 的 filter、hash join 和 aggregate 算子。
- `gpu-copy`：先把输入复制到 GPU 显存，再执行 CUDA kernel。
- `gpu-managed`：使用 CUDA managed memory 和预取。
- `gpu-mapped`：GPU 通过 PCIe 读取映射的主机内存。
- `cuDF`：使用 RAPIDS cuDF 完成关系算子查询。
- `hybrid`：按比例把 `lineitem` 分给 CPU 和 GPU，再合并聚合结果。

所有实现使用相同的 Q5 条件和定点整数收入计算，并与独立 oracle 比较结果。

## 编译与测试

CPU 和 Arrow 环境：

```bash
conda env create -f q5/environment-arrow-cpu.yml
conda activate memq5-arrow-cpu
cmake -S . -B build -G Ninja \
  -DMEMQ5_ENABLE_ARROW=ON -DMEMQ5_ENABLE_CUDA=OFF \
  -DMEMQ5_ENABLE_TESTS=ON -DOPENSSL_ROOT_DIR="$CONDA_PREFIX"
cmake --build build
ctest --test-dir build --output-on-failure
python -m pytest -q q5/tests/python \
  q5/tests/test_repository_layout.py q5/tests/test_results_layout.py
```

CUDA 构建示例（Ada GPU）：

```bash
conda env create -f q5/environment-gpu.yml
conda activate memq5-cudf
cmake -S . -B build-cuda \
  -DMEMQ5_ENABLE_ARROW=ON -DMEMQ5_ENABLE_CUDA=ON \
  -DMEMQ5_ENABLE_TESTS=ON -DCMAKE_CUDA_ARCHITECTURES=89
cmake --build build-cuda
ctest --test-dir build-cuda --output-on-failure
```

## 数据与运行

生成小型合成数据：

```bash
python scripts/generate_synthetic_tpch_q5.py \
  --output data/synthetic --lineitems 20000 --asia-heavy
```

把 TPC-H `dbgen` 生成的六张表整理并转换为 Arrow IPC：

```bash
python scripts/prepare_tpch_q5_data.py \
  --source-dir /path/to/dbgen-output \
  --output-dir data/tpch_sf1 --scale-factor 1

python scripts/prepare_arrow_dataset.py \
  --input data/tpch_sf1 --output data/tpch_sf1_arrow \
  --scale-factor 1 --batch-rows 262144 \
  --source-command "TPC-H dbgen -s 1" --replace
```

运行正式配置：

```bash
python scripts/run_formal_benchmarks.py \
  --matrix q5/experiments/formal_sf1.json \
  --session-cli build-cuda/memq5_arrow_session \
  --output-dir results/tmp/sf1
```

## 实验结果

下表是数据和查询结构准备完成后的 request 中位数，单位为毫秒，显示值四舍五入
到小数点后三位。精确值见 `results/sf1/summary.csv` 和
`results/sf10/summary.csv` 的 `query_total_ms_median` 列。

| 方法 | SF1 | SF10 |
|---|---:|---:|
| CPU specialized | 3.201 | 14.955 |
| Arrow Acero | 311.461 | 3124.388 |
| gpu-copy | 1.267 | 15.416 |
| gpu-managed | 1.440 | 15.066 |
| gpu-mapped | 22.971 | 358.582 |
| cuDF | 12.773 | 27.906 |
| hybrid fixed | 1.160 | 10.054 |
| hybrid auto | 1.553 | 10.980 |

SF1 的结果哈希为 `542abf4003633c7c`，SF10 为 `b1351a421ba8dcfd`。

## 已知不足

- 正式结果来自同一台实验服务器，没有进行跨机器重复测量。
- GPU、cuDF 和 profiler 实验需要 NVIDIA 环境，普通电脑只能复现 CPU 路径。
- 自动 hybrid 的比例选择还没有达到每个规模的最佳固定比例。
- 当前实现针对 Q5 做了专门优化，结论不能直接推广到所有 SQL 查询。

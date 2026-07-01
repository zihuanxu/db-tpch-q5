# 内存数据库课程大作业整合仓库

本仓库是课程最终提交版本，将两个实验方向整合到同一个 GitHub 项目中：

| 目录 | 内容 |
|---|---|
| `hashjoin-cpu/` | 基于 ETH Zurich VLDB 2013 main-memory hash join 开源代码扩展的 CPU 端连接算法框架 |
| 仓库根目录 | TPC-H Q5 CPU/GPU 查询引擎，包含 CPU 执行、手写 CUDA、不同 GPU 内存模式和 cuDF 对照 |
| `docs/` | TPC-H Q5/GPU 项目文档、图表和最终报告 |
| `hashjoin-cpu/docs/` | CPU hashjoin 统一课程报告、期中报告、期末报告、原始数据和图表 |

## 提交入口

建议老师首先阅读以下文档：

| 文档 | 路径 |
|---|---|
| 课程大作业整合说明 | `docs/INTEGRATED_SUBMISSION.docx` / `docs/INTEGRATED_SUBMISSION.md` |
| CPU hashjoin 统一课程报告 | `hashjoin-cpu/docs/COURSE_REPORT.docx` / `hashjoin-cpu/docs/COURSE_REPORT.md` |
| CPU hashjoin 期中 starjoin 报告 | `hashjoin-cpu/docs/MIDTERM_REPORT.docx` / `hashjoin-cpu/docs/MIDTERM_REPORT.md` |
| CPU hashjoin 期末 full sweep 报告 | `hashjoin-cpu/docs/EXPERIMENT_REPORT.docx` / `hashjoin-cpu/docs/EXPERIMENT_REPORT.md` |
| TPC-H Q5/GPU 最终报告 | `docs/FINAL_REPORT.docx` / `docs/FINAL_REPORT.md` |

## 完成情况

CPU hashjoin 部分已经完成：

- 将 NPO、PRO、sort-merge、VJ、PRVJ 和三表 starjoin 整合进原 hashjoin 框架。
- 支持通过算法开关参数执行不同算法和实验配置。
- 完成 NUMA、MLC、starjoin、full sweep、VJ cache/TLB、PRVJ 分区调参等实验。
- 保留原始 CSV、图表、报告和自检脚本。

TPC-H Q5/GPU 部分已经完成：

- 实现 TPC-H Q5 所需列式内存布局、加载器和 CPU 执行路径。
- 实现 `gpu-copy`、`gpu-managed`、`gpu-mapped` 三种手写 CUDA 执行模式。
- 实现 Python、DuckDB、RAPIDS cuDF 对照脚本。
- 在 RTX 4090 服务器上完成 CUDA 构建、CTest、tiny、synthetic、官方 TPC-H SF1 和 cuDF 对照实验。
- 所有成功运行的 CPU、GPU、Python、cuDF 路径在同一数据集上输出一致 result hash。

## CPU Hashjoin 复现

进入 CPU hashjoin 子目录：

```bash
cd hashjoin-cpu
scripts/self_check_assignment.sh
```

主要实验脚本：

```bash
scripts/run_extended_algo_comparison.sh
scripts/run_starjoin_comparison.sh
scripts/run_prvj_tuning.sh
```

关键结果位置：

- `hashjoin-cpu/docs/data/`
- `hashjoin-cpu/docs/assets/`
- `hashjoin-cpu/docs/COURSE_REPORT.md`
- `hashjoin-cpu/docs/EXPERIMENT_REPORT.md`
- `hashjoin-cpu/docs/MIDTERM_REPORT.md`

## TPC-H Q5/GPU 复现

CPU 构建与测试：

```bash
cmake -S . -B build -DMEMQ5_ENABLE_CUDA=OFF -DMEMQ5_ENABLE_TESTS=ON
cmake --build build
ctest --test-dir build --output-on-failure
python3 scripts/self_check.py
```

tiny fixture 示例：

```bash
./build/memq5 --engine cpu --data-dir tests/fixtures/tpch_q5_tiny \
  --region ASIA --date 1994-01-01 --format rows
```

CUDA 构建需要带 `nvcc` 和 NVIDIA 驱动的服务器。RTX 4090 / L20 这类 Ada 架构 GPU 使用 `89`：

```bash
cmake -S . -B build-cuda \
  -DMEMQ5_ENABLE_CUDA=ON \
  -DMEMQ5_ENABLE_TESTS=ON \
  -DCMAKE_CUDA_ARCHITECTURES=89
cmake --build build-cuda
CUDA_VISIBLE_DEVICES=0 ctest --test-dir build-cuda --output-on-failure
```

GPU 实验流水线示例：

```bash
CUDA_VISIBLE_DEVICES=0 python3 scripts/run_experiment_pipeline.py \
  --name tiny_gpu_modes \
  --memq5 build-cuda/memq5 \
  --data-dir tests/fixtures/tpch_q5_tiny \
  --engines cpu,gpu-copy,gpu-managed,gpu-mapped,python \
  --repeat 5 \
  --force
```

官方 TPC-H SF1 数据需要先通过 TPC 官方工具生成，不随仓库提交：

```bash
python3 scripts/prepare_tpch_q5_data.py \
  --source-dir /path/to/dbgen-output \
  --output-dir data/tpch_sf1 \
  --scale-factor 1 \
  --mode copy
```

## 版本控制说明

仓库提交源码、脚本、报告、图表和必要 CSV。以下内容不提交到 Git：

- CMake/autotools 构建目录。
- TPC-H 官方工具和生成的 `.tbl` 数据。
- 大规模实验原始中间结果。
- 本地打包产物。

这些内容可按报告中的复现步骤重新生成。

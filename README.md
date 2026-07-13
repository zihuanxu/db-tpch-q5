# TPC-H Q5 CPU-GPU 内存查询实验

这是课程最终提交主线：固定实现 TPC-H Q5，比较专用 C++ CPU、Apache
Arrow/PyArrow、三种 CUDA 内存模式和 RAPIDS cuDF。它不是通用数据库，
没有 SQL parser 和成本优化器，研究重点是查询物理计划、列式数据和 CPU/GPU
数据移动成本。

| 目录 | 内容 |
|---|---|
| `src/` | 专用 C++ CPU/CUDA Q5 执行器 |
| `baselines/` | Python、PyArrow、DuckDB 和 cuDF 对照 |
| `scripts/` | 数据准备、Arrow IPC、实验、校验和打包工具 |
| `docs/` | TPC-H Q5/GPU 项目文档、图表和最终报告 |

开发仓库还保留 `hashjoin-cpu/` 前期实验，但它不属于本次最小 Q5 提交包。

## 提交入口

建议老师首先阅读以下文档：

| 文档 | 路径 |
|---|---|
| 最小交付说明（从这里开始） | `docs/DELIVERY_GUIDE.md` |
| 计算机学报模板期末论文（最终版） | `docs/FINAL_REPORT.pdf` / `docs/paper/paper.tex` |
| 正式 SF1 证据 | `docs/artifacts/mvp_sf1/` |
| 答辩速查 | `docs/DEFENSE_CHEATSHEET.md` |

## 完成情况

当前最小版本已经完成：

- 实现 TPC-H Q5 所需列式内存布局、加载器和 CPU 执行路径。
- 实现 `gpu-copy`、`gpu-managed`、`gpu-mapped` 三种手写 CUDA 执行模式。
- 实现 Python、PyArrow、DuckDB、RAPIDS cuDF 对照脚本。
- 在 RTX 4090 服务器上完成 CUDA 构建、CTest、tiny、synthetic、官方 TPC-H SF1、cuDF 对照和 CPU/PyArrow/GPU/cuDF full matrix 实验。
- 修复逐行截断问题，正式 SF1 的六个后端输出一致 hash
  `542abf4003633c7c`，并与官方 `q5.out` 五行结果逐行一致。
- 加入 Arrow IPC 数据生成和 manifest，PyArrow 与 cuDF 正式基线共用同一份
  Arrow 数据集；C++/CUDA 仍从 `.tbl` 构造自定义连续数组。

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

再生成 PyArrow/cuDF 共用的 Arrow IPC：

```bash
conda run -n memq5-cudf python scripts/prepare_arrow_dataset.py \
  --input data/tpch_sf1 --output data/tpch_sf1_arrow \
  --scale-factor 1 --batch-rows 262144 \
  --source-command "TPC-H V3.0.1 dbgen -s 1" --replace
```

## 版本控制说明

最小提交包包含源码、脚本、tiny 数据、最终论文和必要 SF1 CSV/manifest，以下
内容不放入压缩包：

- CMake/autotools 构建目录。
- TPC-H 官方工具和生成的 `.tbl` 数据。
- 旧版报告和前期 `hashjoin-cpu` 实验。
- 大规模实验中间目录。
- 本地打包产物。

这些内容可按报告中的复现步骤重新生成。

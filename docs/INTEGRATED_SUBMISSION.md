# 课程大作业整合说明

本仓库将本课程两个交付部分整合到同一个 GitHub 项目中：

| 目录 | 内容 |
|---|---|
| `hashjoin-cpu/` | 基于 ETH Zurich VLDB 2013 hashjoin 开源代码扩展的 CPU 哈希连接框架 |
| 仓库根目录 | TPC-H Q5 CPU/GPU 查询引擎 |
| `docs/` | TPC-H Q5/GPU 项目文档 |
| `hashjoin-cpu/docs/` | CPU hashjoin 报告、图表和原始实验数据 |

## 主要报告

| 文档 | 路径 |
|---|---|
| 统一课程完整报告 | `hashjoin-cpu/docs/COURSE_REPORT.docx` |
| CPU hashjoin 前期/扩展算法报告 | `hashjoin-cpu/docs/EXPERIMENT_REPORT.docx` |
| Starjoin 期中报告 | `hashjoin-cpu/docs/MIDTERM_REPORT.docx` |
| CPU hashjoin 交付索引 | `hashjoin-cpu/docs/FINAL_REPORT.md` |
| 期末 TPC-H Q5/GPU 报告 | `docs/FINAL_REPORT.md` |

## CPU Hashjoin 复现方式

```bash
cd hashjoin-cpu
scripts/self_check_assignment.sh
```

主要实验脚本：

- `hashjoin-cpu/scripts/run_extended_algo_comparison.sh`
- `hashjoin-cpu/scripts/run_starjoin_comparison.sh`
- `hashjoin-cpu/scripts/run_prvj_tuning.sh`

最终 CPU hashjoin 数据和图表：

- `hashjoin-cpu/docs/data/`
- `hashjoin-cpu/docs/assets/`

## TPC-H Q5/GPU 复现方式

CPU 构建与测试：

```bash
cmake -S . -B build -DMEMQ5_ENABLE_CUDA=OFF -DMEMQ5_ENABLE_TESTS=ON
cmake --build build
ctest --test-dir build --output-on-failure
python3 scripts/self_check.py
```

CUDA 构建需要带 `nvcc` 和 NVIDIA 驱动的服务器：

```bash
cmake -S . -B build-cuda -DMEMQ5_ENABLE_CUDA=ON -DMEMQ5_ENABLE_TESTS=ON \
  -DCMAKE_CUDA_ARCHITECTURES=89
cmake --build build-cuda
ctest --test-dir build-cuda --output-on-failure
```

## 说明

CPU hashjoin 框架放在 `hashjoin-cpu/` 子目录中，保留其 autotools 构建方式、实验脚本、报告和原始数据。统一课程完整报告 `hashjoin-cpu/docs/COURSE_REPORT.docx` 按 `/home/xuzihuan/内存连接算法探索.pdf` 的阶段要求组织：第 1 到第 5 项作为前期硬件、连接算法、VJ/PRVJ 扩展实验，第 6 项作为期中 Star Join 实验；期末报告则是仓库根目录的 TPC-H Q5/GPU 项目。

TPC-H Q5/GPU 项目保留在仓库根目录，使用独立的 CMake 构建方式，作为本课程期末项目单独报告。这样一个 GitHub 仓库即可同时覆盖老师要求的前期 CPU hashjoin 实验、期中 Star Join 实验和期末 TPC-H Q5/GPU 实验。

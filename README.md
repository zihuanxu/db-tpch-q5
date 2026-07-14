# TPC-H Q5 CPU-GPU 内存查询实验

这是课程最终提交主线：固定实现 TPC-H Q5，比较专用 C++ CPU、Apache
Arrow/PyArrow、三种 CUDA 内存模式和 RAPIDS cuDF。它不是通用数据库，
没有 SQL parser 和成本优化器，研究重点是查询物理计划、列式数据和 CPU/GPU
数据移动成本。

| 目录 | 内容 |
|---|---|
| `src/` | 专用 CPU、Acero、CUDA、hybrid 和 resident session 执行器 |
| `baselines/` | Python、PyArrow、DuckDB 和 cuDF 对照 |
| `scripts/` | 数据准备、Arrow IPC、实验、校验和打包工具 |
| `docs/` | TPC-H Q5/GPU 项目文档、图表和最终报告 |

开发仓库还保留 `hashjoin-cpu/` 前期实验，但它不属于本次最小 Q5 提交包。

## 提交入口

建议老师首先阅读以下文档：

| 文档 | 路径 |
|---|---|
| 最小交付说明（从这里开始） | `docs/DELIVERY_GUIDE.md` |
| 计算机学报模板期末论文 | `docs/paper/paper.pdf` / `docs/paper/paper.tex` |
| 正式 SF1/SF10 常驻证据 | `docs/artifacts/v7_sf1_resident/` / `v7_sf10_resident/` |
| CUDA profiler 证据 | `docs/artifacts/v7_profiler/` |
| 答辩速查 | `docs/DEFENSE_CHEATSHEET.md` |
| 从原理到讲解的学习材料 | `docs/learning/README.md` |
| 实验过程记录 | `docs/process/README.md` |

## 完成情况

当前最小版本已经完成：

- 实现 TPC-H Q5 所需列式内存布局、加载器和 CPU 执行路径。
- 实现 `gpu-copy`、`gpu-managed`、`gpu-mapped` 三种手写 CUDA 执行模式。
- 实现 Python、PyArrow、DuckDB、RAPIDS cuDF 对照脚本。
- 在 RTX 4090 服务器上完成 CUDA 构建、CTest、tiny、synthetic、官方 TPC-H SF1、cuDF 对照和 CPU/PyArrow/GPU/cuDF full matrix 实验。
- 修复逐行截断问题，正式 SF1 的六个后端输出一致 hash
  `542abf4003633c7c`，并与官方 `q5.out` 五行结果逐行一致。
- 加入 Arrow IPC 数据生成和 manifest，PyArrow、cuDF 和 C++ Arrow CPU
  共用同一份 Arrow 数据集；旧 C++/CUDA 路径仍从 `.tbl` 构造连续数组。
- V2.1 新增可选 C++ Arrow IPC loader，可在 C++ 层校验六表 schema、行数、
  record batch、字节数、SHA256 和非空约束。
- V2.2 新增直接遍历 Arrow batch/buffer 的专用 CPU 引擎，以及使用 Acero
  filter/hash join 的关系算子引擎；两者在 tiny 和 SF1 上结果一致。
- V3 让 `gpu-copy`、`gpu-managed`、`gpu-mapped` 从同一 Arrow dataset 读取，
  共用精确 CUDA kernel，并记录 H2D、D2H 和 mapped 远程读取字节。
- V4 增加 cuDF 与按比例切分的 CPU-GPU hybrid 路径，并统一结果校验协议。
- V5 冻结了 SF1 cold-process 历史矩阵；它只作为冷启动对照，不与常驻请求
  延迟直接混排。
- V7 实现真正的 resident session，分别冻结 SF1 和 SF10 的 18 组配置。每个
  规模包含 54 次预热和 180 次正式请求，8 类后端全部通过 oracle。
- V7 固定比例 hybrid 在 SF1/SF10 的最佳常驻中位数为 1.160/10.054 ms；
  hybrid-auto 能跟随规模改变比例，但相对最佳 fixed 仍有 33.89%/9.21% regret。
- 完成 SF1/SF10 × copy/managed/mapped/hybrid-fixed/hybrid-auto 共 10 组
  Nsight Systems/Compute 采集；完整 bundle 通过严格审计，仓库保存轻量发布副本。

## TPC-H Q5/GPU 复现

CPU 构建与测试：

```bash
cmake -S . -B build -DMEMQ5_ENABLE_CUDA=OFF -DMEMQ5_ENABLE_TESTS=ON
cmake --build build
ctest --test-dir build --output-on-failure
python3 scripts/self_check.py
```

安装 Arrow CPU 环境后，可以执行和 GitHub Actions 相同的完整 CPU 验收：

```bash
conda env create -f environment-arrow-cpu.yml
conda run -n memq5-arrow-cpu bash scripts/ci_cpu.sh
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

C++ Arrow loader 的独立构建与验证见
`docs/STAGE_V2_1_ARROW_LOADER.md`。Arrow CPU 查询见
`docs/STAGE_V2_2_ARROW_CPU.md`，Arrow 输入的 CUDA 三模式见
`docs/STAGE_V3_ARROW_CUDA.md`。

## 证据审计与发布

正式结果不是从论文正文手工抄写的。`scripts/import_paper_evidence.py` 从两组
V7 resident 证据和 hybrid 模型生成 `docs/paper/generated/results.tex`，主张状态记录在
`docs/research/CLAIM_LEDGER.md`。交付前运行：

```bash
python3 scripts/v7_evidence_bundle.py audit --directory docs/artifacts/v7_sf1_resident
python3 scripts/v7_evidence_bundle.py audit --directory docs/artifacts/v7_sf10_resident
python3 scripts/validate_claim_ledger.py docs/research/CLAIM_LEDGER.md
python3 scripts/check_paper.py
python3 scripts/release_audit.py --json
python3 scripts/package_submission.py
```

CPU 路径由 `.github/workflows/cpu-ci.yml` 自动构建和测试；CUDA、cuDF、SF10
和 profiler 仍必须在 NVIDIA GPU 机器上按 `docs/GPU_SERVER_RUNBOOK.md` 复验。

## 许可与引用

本项目自有代码采用 `Apache-2.0` 许可，见 `LICENSE` 和 `NOTICE`。论文或项目
引用信息见 `CITATION.cff`。TPC-H 工具、生成数据、论文模板以及前期对照目录
可能有各自许可或使用条款，不由本项目许可证重新授权。

## 版本控制说明

V7 提交包包含源码、脚本、tiny 数据、论文、两组 resident 证据和 compact
profiler 证据，以下
内容不放入压缩包：

- CMake/autotools 构建目录。
- TPC-H 官方工具和生成的 `.tbl` 数据。
- 旧版报告和前期 `hashjoin-cpu` 实验。
- 大规模实验中间目录。
- 本地打包产物。

这些内容可按报告中的复现步骤重新生成。

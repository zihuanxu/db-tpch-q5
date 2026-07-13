# 最小可交付版本说明

这份文件是本项目最终提交和复现的入口。项目已经达到“代码可运行、数据可生成、结果可核对、报告可讲解”的课程作业最低完整度，不再继续扩展功能。

## 交什么

提交 `dist/memory-db-tpch-q5-final.tar.gz`，并把同样内容推送到 GitHub。建议老师先看：

1. `docs/FINAL_REPORT.pdf`：计算机学报模板期末论文，最终提交版本。
2. `docs/paper/paper.tex`：论文源文件和模板构建入口。
3. `README.md`：项目结构和复现命令。
4. `docs/artifacts/mvp_sf1/`：18 条 SF1 原始 benchmark、环境、Arrow manifest 和官方答案校验。
5. `docs/DEFENSE_CHEATSHEET.md`：答辩前速记。
6. `docs/PROJECT_HANDOVER_GUIDE.md`：接手源码时的详细讲解。

## 最快复现

CPU 环境只需 CMake、C++17 编译器和 Python 3：

```bash
python3 scripts/self_check.py --skip-cuda
```

有 NVIDIA GPU 和 `nvcc` 时：

```bash
python3 scripts/self_check.py
```

运行 tiny 数据上的五种核心路径：

```bash
CUDA_VISIBLE_DEVICES=0 python3 scripts/run_experiment_pipeline.py \
  --name delivery_tiny \
  --memq5 build-cuda/memq5 \
  --data-dir tests/fixtures/tpch_q5_tiny \
  --engines cpu,gpu-copy,gpu-managed,gpu-mapped,python \
  --repeat 3 --force
```

最后打包：

```bash
python3 scripts/package_submission.py \
  --output dist/memory-db-tpch-q5-final.tar.gz
```

## 和课程要求的对应关系

| 课程要求 | 最小版本中的实现 | 状态 |
|---|---|---|
| TPC-H Q5 CPU-GPU 协同查询 | CPU 构造过滤传播 map，CPU 或 CUDA 扫描 `lineitem` 聚合 | 已实现 |
| CPU 端 Arrow | Arrow IPC 数据集；C++ loader 校验 schema/manifest；`cpu-specialized` 直接读 Arrow buffers；`arrow-acero` 使用 Acero filter/hash join | 已实现（V2） |
| PCIe 数据传输 | V3 `gpu-copy` 从 Arrow staging buffers 显式 `cudaMemcpy` 到显存 | 已实现 |
| UVA/统一地址访问 | V3 `gpu-mapped` 使用 mapped pinned host memory；`gpu-managed` 显式 prefetch | 已实现 |
| GPU 算子库 | RAPIDS cuDF Q5 baseline | 已实现并跑过 SF1 |
| 可共享、可重现、可验证 | CMake、CTest、tiny fixture、数据生成器、Arrow manifest、实验流水线、官方 oracle | 已实现 |
| 论文形式报告 | 计算机学报模板 `docs/FINAL_REPORT.pdf` 和 LaTeX 源稿 | 已实现 |

## 已知缺陷

这些缺陷保留在最终版本中，并在报告里直接说明：

- 只做了 SF1，没有 SF10 或更大规模。
- GPU 只负责最后的 `lineitem` 扫描聚合，查询前半段仍在 CPU。
- 当前 Arrow 23 环境没有 Arrow CUDA 扩展；V3 从 Arrow Table staging 到原生
  CUDA device/managed/mapped buffer，不能写成使用了 `arrow::cuda::CudaBuffer`。
- C++ 与 PyArrow/cuDF 的内部计时边界不同，不能把 `total_ms` 直接当成严格公平排名。
- 最小正式实验只使用固定 8 线程 C++ 设置、一次预热和三次重复，没有置信区间。

## 现在能得出的结论

在 SF1 上，六个后端得到一致 hash `542abf4003633c7c`，并与官方 `q5.out` 一致。GPU kernel 扫描很快，但数据准备、CUDA 初始化、分配和传输让手写 GPU 路径的查询内总时间仍慢于 8 线程 CPU。`gpu-copy` 在三种 GPU 模式中最快；`gpu-mapped` 几乎没有显式 H2D 时间，但 GPU 通过 PCIe 读取主机内存使 kernel 变慢。这个结果支持课程主题中的核心观点：CPU-GPU 查询优化不能只看 kernel，还要同时考虑数据位置和移动成本。

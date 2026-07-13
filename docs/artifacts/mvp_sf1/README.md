# SF1 最小正式实验包

本目录保存 2026-07-14 在同一台服务器上完成的最小可交付实验。这里的
CSV、环境记录和 oracle 核对结果是论文数值的直接来源，不使用之前逐行截断
收入的旧结果。

## 实验设置

- 数据：TPC-H V3.0.1 dbgen，SF1，Q5 参数 `ASIA` 与 `1994-01-01`。
- 数据规模：`lineitem` 6,001,215 行；Arrow IPC 数据约 248 MiB。
- 硬件：2 路 AMD EPYC 9654；GPU 0 为 NVIDIA GeForce RTX 4090。
- 软件：CUDA Toolkit 12.6，PyArrow 23.0.1，cuDF 26.06.00。
- 后端：8 线程 C++ CPU、`gpu-copy`、`gpu-managed`、`gpu-mapped`、
  PyArrow、cuDF。
- 每个后端预热 1 次，独立进程重复 3 次，表中报告中位数。

六个后端的 `result_hash` 都是 `542abf4003633c7c`。`oracle_check.json`
进一步证明五行国家收入及顺序与官方 `q5.out` 的两位小数输出完全一致。

## 计时边界

`elapsed_ms` 是 benchmark 控制器从启动子进程到结束测得的冷启动墙钟时间，
可用于观察文本或 Arrow IPC 装载的整体影响。`total_ms` 是各后端内部报告的
查询时间，但当前口径并不完全相同：C++/CUDA 在 `.tbl` 读入并转换成内存列
之后开始计时；PyArrow/cuDF 则把 Arrow IPC 读取和 DataFrame 构造包括在内。
因此论文同时给出两列，并把跨实现的 `total_ms` 对比作为有限观察，不当成严格
公平的引擎排名。

C++/CUDA 和 Python 环境分两次执行。原因是本地构建的 Arrow CUDA 动态库与
conda 中 PyArrow 的完整动态库集合不能混在同一个 `LD_LIBRARY_PATH` 中。
两组都在 GPU 0、同一数据、相同参数和同一时间段运行，最后只用结构化 CSV
合并成功记录。

## 文件

- `benchmarks.csv`：18 条原始成功记录，每后端 3 条。
- `summary.md`：六个后端的中位数摘要。
- `cpu_rows.csv`：C++ CPU 的完整 SF1 输出。
- `oracle_check.json`：与官方 `q5.out` 的逐行核对。
- `environment.json`：CPU、GPU、驱动、CUDA 和 Python 包版本。
- `arrow_manifest.json`：六张 Arrow IPC 表的 schema、行数和 SHA256。
- `total_time.svg`、`time_breakdown.svg`：由 CSV 自动生成的图。

## 已知不足

- 只做 SF1、1 次预热和 3 次正式重复，没有 SF10 和统计置信区间。
- C++/CUDA 仍从 `.tbl` 构造自定义连续数组；只有 PyArrow/cuDF 共用 Arrow IPC。
- 没有 GPU 常驻数据、CPU--GPU 同时执行的 hybrid 后端或 Nsight 深度分析。
- 三种 CUDA 后端每次进程都会初始化 CUDA 并重新分配缓冲区。

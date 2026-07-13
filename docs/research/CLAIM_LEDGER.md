# 研究论断与证据账本

这张表约束论文和答辩中的说法。`VERIFIED` 表示现有正式证据支持该说法，
`REJECTED` 表示原假设被正式结果否定。这里记录的是本项目和本机 SF1 实验，
不能直接外推到其他查询、硬件或更大规模。

| ID | RQ/H | Statement | State | Code | Test | Evidence | Paper Location | Limitation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C001 | RQ1 | 六个 Q5 输入表由同一 Arrow IPC 数据集承载，C++ CPU、CUDA、混合和 cuDF 都读取该数据集。 | VERIFIED | `src/io/arrow_q5_loader.cpp` | `tests/test_arrow_q5_loader.cpp` | `docs/artifacts/v5_sf1` | `docs/paper/paper.tex` | Arrow 是统一输入格式，不等于各后端内部布局和准备工作完全相同。 |
| C002 | RQ1 | 正式 SF1 的 190 次测量均通过 oracle，唯一结果哈希为 542abf4003633c7c。 | VERIFIED | `scripts/verify_q5_oracle.py` | `tests/python/test_verify_q5_oracle.py` | `docs/artifacts/v5_sf1` | `docs/paper/paper.tex` | 结果只覆盖 ASIA 和 1994 年这一组 Q5 参数。 |
| C003 | RQ2/H1 | SF1 中专用 CPU 的最佳查询中位数为 61.414 ms，低于 Arrow Acero 的最佳 321.535 ms。 | VERIFIED | `src/cpu/q5_arrow_cpu.cpp` | `tests/test_q5_arrow_cpu.cpp` | `docs/artifacts/v5_sf1` | `docs/paper/paper.tex` | 专用计划只实现固定 Q5，通用性明显低于 Acero。 |
| C004 | RQ3/H2 | 三种 CUDA 模式中 copy 的查询中位数最低，其次为 managed，mapped 最慢。 | VERIFIED | `src/cuda/q5_arrow_cuda.cu` | `tests/test_q5_arrow_cuda.cpp` | `docs/artifacts/v5_sf1` | `docs/paper/paper.tex` | 这是每次重新准备数据的冷进程 SF1 结果。 |
| C005 | RQ3 | mapped 没有显式输入 H2D 阶段，但 GPU 仍通过 PCIe 读取锁页主机内存，不能称为没有传输。 | VERIFIED | `src/cuda/q5_arrow_cuda.cu` | `tests/test_q5_arrow_cuda.cpp` | `docs/artifacts/v5_sf1` | `docs/paper/paper.tex` | 当前计数是逻辑远程读取量，不是硬件总线分析器的实测流量。 |
| C006 | RQ4/H3 | cuDF 查询阶段中位数为 116.427 ms，但冷进程中位数为 3682.381 ms。 | VERIFIED | `baselines/cudf_q5.py` | `tests/python/test_cudf_q5.py` | `docs/artifacts/v5_sf1` | `docs/paper/paper.tex` | 查询时间与进程时间口径不同，不能挑选其中一列制造排名。 |
| C007 | RQ5/H4 | CPU--GPU 混合执行在 SF1 上可以超过专用 CPU。 | REJECTED | `src/hybrid/q5_hybrid.cpp` | `tests/test_q5_hybrid.cpp` | `docs/artifacts/v5_sf1` | `docs/paper/paper.tex` | 最好的 75% CPU 比例仍为 222.832 ms，当前实现会在两侧重复准备计划和 GPU 输入。 |
| C008 | RQ5 | 混合路径的 CPU 与 GPU 后端执行时段存在正的持续时间重叠。 | VERIFIED | `src/hybrid/q5_hybrid.cpp` | `tests/test_q5_hybrid.cpp` | `docs/artifacts/v5_sf1` | `docs/paper/paper.tex` | 没有 Nsight 时间线，因此不能进一步声称 CPU scan 与 CUDA kernel 本身重叠。 |
| C009 | H5 | 当前正式证据可以说明常驻数据库场景下的冷启动摊销效果。 | REJECTED | `scripts/run_benchmarks.py` | `tests/python/test_run_benchmarks_v5.py` | `docs/artifacts/v5_sf1` | `docs/paper/paper.tex` | 正式矩阵只有 cold；resident 生命周期未实现，工具会明确拒绝而不是伪装成 cold。 |
| C010 | RQ6 | SF10 和多查询并发可用于判断规模交叉点。 | PLANNED | - | - | - | - | 当前没有 SF10 或并发证据，论文不得给出对应性能结论。 |

# 研究论断与证据账本

这张表约束论文和答辩中的说法。`VERIFIED` 表示现有正式证据支持该说法，
`REJECTED` 表示原假设被正式结果否定。V5 是 SF1 冷进程历史对照，V7 是
SF1/SF10 常驻会话实验；两种生命周期不能直接混排。结论也不能外推到其他
查询、硬件或规模。

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
| C010 | RQ6 | SF10 常驻实验可与 SF1 一起观察规模变化。 | VERIFIED | `scripts/run_v7_benchmarks.py` | `tests/python/test_run_v7_benchmarks.py` | `docs/artifacts/v7_sf10_resident` | `docs/paper/paper.tex` | 只有两个规模点，不能拟合普遍的性能曲线；多查询并发仍未实现。 |
| C011 | RQ1 | V7 把数据装载和会话准备记为 setup，把重复请求单独计时，并给出 1、10、100 次请求的摊销值。 | VERIFIED | `src/session/q5_cpu_session.cpp` | `tests/test_q5_cpu_session.cpp` | `docs/artifacts/v7_sf1_resident` | `docs/paper/paper.tex` | 常驻请求不包含首次装载，不能与 V5 冷进程墙钟直接比较。 |
| C012 | RQ1 | SF1 的 180 次常驻正式请求均通过 oracle，结果哈希为 542abf4003633c7c。 | VERIFIED | `scripts/materialize_v7_correctness.py` | `tests/python/test_materialize_v7_correctness.py` | `docs/artifacts/v7_sf1_resident` | `docs/paper/paper.tex` | 仍只覆盖 Q5 的 ASIA 和 1994 年参数。 |
| C013 | RQ3 | SF1 常驻请求中，copy、managed、mapped 中位数为 1.267、1.440、22.971 ms。 | VERIFIED | `src/cuda/q5_arrow_cuda.cu` | `tests/test_q5_arrow_cuda.cpp` | `docs/artifacts/v7_sf1_resident` | `docs/paper/paper.tex` | 排名只针对本机 RTX 4090；setup 和请求时间需要分别解释。 |
| C014 | RQ5 | SF1 固定比例混合执行的最佳常驻中位数为 1.160 ms（CPU 比例 0.125），低于 3.201 ms 的专用 CPU。 | VERIFIED | `src/hybrid/q5_hybrid.cpp` | `tests/test_q5_hybrid.cpp` | `docs/artifacts/v7_sf1_resident` | `docs/paper/paper.tex` | 最优比例来自离散扫描，且较高的 setup 成本会影响少量请求场景。 |
| C015 | H6 | 当前 hybrid-auto 模型能够选择到 SF1 离线扫描的最佳固定比例。 | REJECTED | `scripts/evaluate_hybrid_model.py` | `tests/python/test_evaluate_hybrid_model.py` | `docs/artifacts/v7_sf1_resident` | `docs/paper/paper.tex` | auto 相对最佳 fixed 的 regret 为 33.89%；模型样本和特征都较少。 |
| C016 | RQ4 | SF1 的 Arrow Acero 常驻中位数约 311.461 ms，cuDF 为 12.773 ms，通用算子路径与专用固定查询路径存在明显差距。 | VERIFIED | `src/cpu/q5_acero.cpp` | `tests/test_q5_acero.cpp` | `docs/artifacts/v7_sf1_resident` | `docs/paper/paper.tex` | 执行计划和内部数据结构不同，不能把差距完全归因于 Arrow 或 GPU。 |
| C017 | RQ1 | SF10 的 180 次常驻正式请求均通过 oracle，结果哈希为 b1351a421ba8dcfd。 | VERIFIED | `scripts/materialize_v7_correctness.py` | `tests/python/test_materialize_v7_correctness.py` | `docs/artifacts/v7_sf10_resident` | `docs/paper/paper.tex` | 仍只覆盖 Q5 的 ASIA 和 1994 年参数。 |
| C018 | RQ3 | SF10 常驻请求中，copy、managed、mapped 中位数为 15.416、15.066、358.582 ms。 | VERIFIED | `src/cuda/q5_arrow_cuda.cu` | `tests/test_q5_arrow_cuda.cpp` | `docs/artifacts/v7_sf10_resident` | `docs/paper/paper.tex` | managed 与 copy 的差距较小，不能据此断言所有规模下 managed 更优。 |
| C019 | RQ5 | SF10 固定比例混合执行的最佳常驻中位数为 10.054 ms（CPU 比例 0.375），低于 14.955 ms 的专用 CPU。 | VERIFIED | `src/hybrid/q5_hybrid.cpp` | `tests/test_q5_hybrid.cpp` | `docs/artifacts/v7_sf10_resident` | `docs/paper/paper.tex` | 最优比例来自离散扫描，只有一个硬件环境。 |
| C020 | H6 | 当前 hybrid-auto 模型能够选择到 SF10 离线扫描的最佳固定比例。 | REJECTED | `scripts/evaluate_hybrid_model.py` | `tests/python/test_evaluate_hybrid_model.py` | `docs/artifacts/v7_sf10_resident` | `docs/paper/paper.tex` | auto 相对最佳 fixed 的 regret 为 9.21%，没有达到最佳点。 |
| C021 | RQ4 | SF10 的 Arrow Acero 常驻中位数约 3124.388 ms，cuDF 为 27.906 ms。 | VERIFIED | `src/cpu/q5_acero.cpp` | `tests/test_q5_acero.cpp` | `docs/artifacts/v7_sf10_resident` | `docs/paper/paper.tex` | 两者算法和准备方式不同，这不是单一框架开销的纯比较。 |

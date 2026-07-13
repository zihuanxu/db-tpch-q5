# 05 CPU--GPU 混合执行

## 你需要先知道

混合执行不是“CPU 负责 join、GPU 负责 group by”。本项目是把同一份 lineitem
按行分成互不重叠的两块，CPU 和 GPU 各算一部分，再合并结果。

## 核心原理

分区器按 Arrow batch 累计行数，在目标比例附近切分，最多拆一个边界 batch，
并保证每行恰好出现一次。GPU copy 路径异步启动，主线程执行 CPU prefix；两侧
都结束后，用 checked int64 加法合并国家收入和计数。即使一侧报错，也会等待
另一侧结束，避免后台任务越过资源生命周期。

## 代码入口

- batch 分区：`src/hybrid/batch_partition.cpp`
- 混合执行：`src/hybrid/q5_hybrid.cpp`
- 分区测试：`tests/test_batch_partition.cpp`
- 混合测试：`tests/test_q5_hybrid.cpp`

## 自己检查

50% 比例下，SF1 的 CPU/GPU 行数为什么可能相差 1？

**检查答案：** 总行数 6,001,215 是奇数，两个不重叠整数分区无法完全相等；
3,000,608 与 3,000,607 合计仍应严格等于总行数。

## 老师可能追问

有 duration overlap 是否证明 CPU scan 与 kernel 重叠？不能。计时只说明两个
后端区间相交；需要 Nsight 时间线才能定位具体 scan 和 kernel。

## 一分钟复述

混合路径是 Arrow 行分区的数据并行。它正确且并发，但 75% CPU 的最佳中位数
222.832 ms 仍慢于纯 CPU 61.414 ms，所以加速假设被否定。

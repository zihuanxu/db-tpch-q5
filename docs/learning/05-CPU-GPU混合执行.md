# 05 CPU--GPU 混合执行

## 你需要先知道

混合执行不是“CPU负责join、GPU负责group by”。本项目把同一份lineitem按
Arrow batch分成互不重叠的两块，CPU和GPU各算一部分，再合并结果。

## 核心原理

分区器在目标比例附近选择batch边界，并保证CPU行数加GPU行数严格等于总行数。
GPU异步启动，主线程扫描CPU区间；两侧使用独立聚合数组，最后用checked int64
加法合并。fixed模式扫描0.125到0.875共7个CPU比例。auto模式在setup中做一次
CPU/GPU校准，用简单模型预测比例，再映射到可用batch边界。

SF1的fixed最佳CPU比例为0.125，请求中位数1.160 ms；SF10最佳比例变为0.375，
中位数10.054 ms。auto分别选择0.262和0.288，对应1.553和10.980 ms。它能判断
规模变大后CPU应多承担一些，但没有找到最佳点，regret为33.89%和9.21%。

## 代码入口

- batch分区：`src/hybrid/batch_partition.cpp`
- 混合执行：`src/hybrid/q5_hybrid.cpp`
- auto模型测试：`tests/test_q5_hybrid_auto.cpp`
- 模型评估：`scripts/evaluate_hybrid_model.py`

## 自己检查

为什么“请求1.160 ms”不等于“第一次查询1.160 ms”？

**检查答案：** fixed hybrid还需要约609.624 ms的SF1 setup；1次请求摊销约
610.784 ms。1.160 ms只表示已经建立好session后的重复请求。

## 老师可能追问

Nsight是否证明CPU与GPU重叠？SF10 fixed-0.5 profile中，CPU scan约12.31 ms，
GPU request约8.27 ms，二者位于约12.51 ms的measured request内，支持阶段
重叠。profiler会扰动时间，因此这组数不参与普通性能排名。

## 一分钟复述

混合路径按Arrow batch做数据并行。常驻状态下fixed hybrid在SF1和SF10都比
专用CPU请求快，但setup较高；auto能跟随规模调比例，却仍有明显regret。

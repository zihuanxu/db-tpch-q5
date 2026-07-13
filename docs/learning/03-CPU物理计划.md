# 03 CPU 物理计划

## 你需要先知道

项目有两条 CPU 路径：固定 Q5 的 specialized executor，以及通用 Arrow Acero
算子计划。它们不是同一个算法换了名字。

## 核心原理

specialized 先构造 `supplier -> nation`、`customer -> nation`、`order -> nation`
直接索引，未命中写 `-1`。扫描 lineitem 时只做数组查找、国家相等判断和整数
累加。多线程把行分段，每个线程使用本地聚合数组，结束后归并，避免每行都抢
全局原子。Acero 则执行 filter、hash join、aggregate 和排序，更接近关系算子库。

## 代码入口

- specialized：`src/cpu/q5_arrow_cpu.cpp`
- Acero：`src/cpu/q5_acero.cpp`
- 计划数据：`src/engine/arrow_q5_plan.hpp`
- 测试：`tests/test_q5_arrow_cpu.cpp`

## 自己检查

说明为什么 16 线程到 32 线程没有继续加速。

**检查答案：** SF1 工作量较小，计划准备和内存访问占比高，线程同步、调度和
NUMA 也可能抵消收益。现有证据只能确认“扩展变平”，不能单独确定是哪一项。

## 老师可能追问

specialized 比 Acero 快是否证明 Acero 不好？不能。specialized 利用了固定查询
和稠密键，牺牲通用性换取更少的算子和中间结果。

## 一分钟复述

专用 CPU 计划把六表连接压成三个按键索引，最后只扫描 lineitem；Acero 保留
通用关系算子。SF1 最佳中位数分别是 61.414 ms 和 321.535 ms。

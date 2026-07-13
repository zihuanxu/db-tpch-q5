# 五分钟答辩稿

## 0:00-0:40 研究问题

我的项目不是通用数据库，而是固定实现 TPC-H Q5。Q5 会连接 region、nation、
customer、orders、supplier 和 lineitem 六表，统计 ASIA 地区 1994 年、客户与
供应商同国家的收入。我想比较同一查询在 CPU、GPU 和 CPU--GPU 混合执行下的
数据组织与移动成本。

## 0:40-1:30 数据与正确性

所有正式后端都读取同一 Arrow IPC 数据集。CPU 先把维表条件传播成按 key 的
直接索引，最后扫描 6,001,215 条 lineitem。收入使用 `revenue_1e4` 整数累加，
避免旧版本逐行截断。19 组配置的 190 次正式运行都得到
`542abf4003633c7c`，并通过官方五行答案核对。<!-- C001 C002 -->

## 1:30-2:40 执行路径

CPU 有 specialized 和 Arrow Acero 两条路径。GPU 使用同一个 kernel，只改变
内存方式：copy 显式传到显存；managed 使用统一地址并预取，仍然会迁移；mapped
让 GPU 经 PCIe 读 pinned host memory。cuDF 是通用算子库对照。hybrid 按 Arrow
batch 把 lineitem 分给 CPU 和 GPU 并发处理，再精确合并。<!-- C003 C004 C005 -->

## 2:40-4:10 实验结果

正式矩阵每组 3 次预热、10 次冷进程测量。specialized CPU 16 线程查询中位数
61.414 ms，Acero 最佳 321.535 ms。cuDF 查询阶段是 116.427 ms，但冷进程是
3682.381 ms，说明库加载和转换不能忽略。三种 CUDA 查询中位数依次是 copy
314.151、managed 358.158、mapped 412.264 ms。mapped 没有显式大块 H2D，
但 kernel 远程读 PCIe，所以不是“没有传输”。<!-- C003 C004 C005 C006 -->

hybrid 从 25% CPU 到 75% CPU 越来越快，最好是 222.832 ms，但仍慢于纯 CPU。
所以“混合一定加速”的假设在 SF1 上被否定。我们记录到并发时段，但没有 Nsight
时间线，不能声称 CPU scan 和 kernel 本身重叠。<!-- C007 C008 -->

## 4:10-5:00 限制与结论

目前只有固定 Q5、SF1 和 cold process；GPU 只负责 lineitem 扫描，没有 resident、
SF10、并发查询和完整 profiler 证据。我的结论是：内存模式会改变 PCIe 传输发生
的位置；并行执行本身不等于端到端优化，计划和缓冲区复用可能比继续微调单次
kernel 更重要。<!-- C009 -->

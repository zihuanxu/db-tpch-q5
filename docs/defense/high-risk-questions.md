# 高风险问题与回答

## 这是不是数据库

不是完整 DBMS，是固定 Q5 物理执行器。没有 SQL parser、代价优化、事务和索引
管理，但包含列式数据、连接过滤传播、并行聚合、CPU/GPU 数据移动和实验优化问题。

## 为什么必须六表和同国家

region/nation 确定 ASIA，customer/orders 确定客户和 1994 年订单，supplier/
lineitem 确定供应明细。同国家条件把跨国供应排除，结果才是国家内部收入。

## Arrow 到底做了什么

统一六表 schema、列 buffer、record batch 和跨语言输入。它不自动执行查询，
也不保证 specialized、Acero、CUDA 和 cuDF 的内部准备成本相同。<!-- C001 -->

## copy、managed、mapped 的一句话区别

copy 显式 H2D 到显存；managed 使用统一地址并由 runtime 迁移/预取；mapped 让
GPU 远程读 pinned host pages。三种方式都有数据移动或远程访问。<!-- C004 C005 -->

## managed 是不是复制一份 CPU 内存等 GPU 用

不是这个定义。managed allocation 只有统一虚拟地址，物理页面可按需迁移或被
prefetch。CPU 和 GPU 看到同一分配，不等于永远维持两份同步副本。

## mapped 为什么仍有 CPU copy

Arrow 列要被准备到可映射的 pinned buffer；`h2d_ms=0` 只表示没有大块显式输入
cudaMemcpy。kernel 读取时仍可能产生 PCIe 事务。<!-- C005 -->

## cuDF 比较公平吗

输入和查询语义相同，但执行层级不同。cuDF 是通用 DataFrame 算子，手写 CUDA
只扫描 lineitem。论文同时报告 query 和 process 时间，并明确这个限制。<!-- C006 -->

## hash 一样能否证明一定正确

不能单独证明，所以项目还用独立脚本按十进制定点数与官方 q5.out 五行逐行比较。
hash 用于快速发现后端不一致，oracle 用于检查标准答案。<!-- C002 -->

## cold 与 resident 有什么区别

cold 每个样本重新启动、加载、准备、执行和退出；resident 会复用进程、计划和
缓冲区。当前正式矩阵只有 cold，resident 请求会明确报 unsupported。<!-- C009 -->

## hybrid 为什么没有加速

当前两侧重复准备计划，GPU 还要 staging，SF1 又不大。75% CPU 最好但仍是
222.832 ms，慢于 CPU 61.414 ms。这个解释是根据实现和计时推断，不是假装已经
被 profiler 完全证明。<!-- C007 -->

## overlap 证据够吗

只够说明 CPU/GPU 后端持续时间区间相交，不够证明 scan 与 kernel 的硬件时间线
重叠。后一个结论需要 Nsight。<!-- C008 -->

## 项目最大缺陷是什么

只有固定 Q5、SF1 和 cold process；GPU 不是完整查询计划；没有 resident、SF10、
并发、GPU 峰值显存和 profiler 时间线。结论不能外推到所有数据库负载。

## 哪部分你现在能自己讲清楚

能从 Q5 条件讲到过滤传播数组，从 Arrow batch 讲到三种 CUDA 内存，再解释正式
矩阵、两种计时、oracle 和 hybrid 负结果；也能指出下一步应复用计划和缓冲区。

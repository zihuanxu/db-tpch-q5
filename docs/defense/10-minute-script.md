# 十分钟答辩稿

## 1. 题目与范围（1 分钟）

课题要求研究 CPU--GPU 协同查询处理。我选择 TPC-H Q5，做一个固定物理计划，
而不是 SQL parser、优化器和事务系统。这样可以把范围集中在 Arrow 列式输入、
连接过滤传播、GPU 内存方式和混合分区。

## 2. Q5 与数据（1.5 分钟）

lineitem 是订单明细。只有订单日期在 1994 年、客户和供应商都在 ASIA、并且两者
属于同一国家的明细才计入。六表只保留必需列，转换为 Arrow IPC，并用 manifest
校验 schema、行数、batch 和 SHA256。价格和折扣使用整数定点数，先在
`revenue_1e4` 尺度聚合，最后再显示两位小数。<!-- C001 C002 -->

## 3. CPU 与 GPU 计划（2 分钟）

specialized CPU 把 supplier、customer、orders 映射到 nation，扫描 lineitem
时只做数组查找和整数累加；每个线程有本地聚合数组。Acero 使用 filter、hash
join、aggregate 和排序，通用性更强。GPU 复用过滤传播思路，一个线程处理一条
lineitem，对 nation 聚合数组 atomicAdd。<!-- C003 -->

copy 把输入显式复制到显存；managed 由 runtime 管理迁移并提前 prefetch；mapped
把 pinned host pages 映射给 GPU，经 PCIe 远程读。三种模式不是三种 join 算法，
只是同一 kernel 的内存策略。cuDF 使用 DataFrame merge、filter、groupby 和 sort。
<!-- C004 C005 C006 -->

## 4. 混合执行（1 分钟）

混合路径按 Arrow batch 切分 lineitem，保证每行恰好属于 CPU 或 GPU。GPU 异步
启动，主线程执行 CPU 部分，最后检查溢出并合并结果。测试 25%、50%、75% CPU
比例。duration overlap 证明两个后端执行区间有交集，但没有 profiler 就不能把它
解释成 scan 与 kernel 的精确重叠。<!-- C007 C008 -->

## 5. 实验方法（1 分钟）

SF1 正式矩阵有 19 组，每组 3 次 warmup 和 10 次 measured cold process。保存
stdout、stderr、退出码、RSS、原始 CSV 和所有失败；摘要从 raw.csv 重算。正确性
先比较精确 hash，再对官方 q5.out 五行金额。`query_total_ms` 和
`process_elapsed_ms` 回答不同问题，不能混成同一排行榜。

## 6. 结果（2 分钟）

190 次正式运行和 57 次预热全部成功。specialized CPU 16 线程 61.414 ms，Acero
最佳 321.535 ms，说明固定查询直接索引减少了通用算子开销。cuDF 查询 116.427
ms，但 cold process 3682.381 ms。CUDA 三模式 query 中位数为 314.151、358.158、
412.264 ms，copy 最好，mapped 最慢。<!-- C002 C003 C004 C006 -->

hybrid 的三组结果是 295.435、254.478、222.832 ms。CPU 比例越高越快，说明当前
GPU 侧准备成本较重；最好一组仍慢于 pure CPU。因此原来的 hybrid speedup 假设
被拒绝，但实现的正确性和并发性成立。<!-- C007 C008 -->

## 7. 限制与后续（1.5 分钟）

结论只覆盖本机 RTX 4090、固定 Q5、SF1 和 cold process。没有 SF10、resident、
并发查询、NVML GPU 峰值显存和 Nsight kernel timeline。GPU 也没有承担完整六表
计划。下一步应该先复用 CPU plan 和 GPU buffers，再做 SF10 与 resident 测量。
我不会把当前结果推广成“GPU 数据库普遍更慢”。<!-- C009 -->

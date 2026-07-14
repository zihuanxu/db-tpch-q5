# 十分钟答辩稿

## 1. 题目与范围（1分钟）

课题要求研究CPU--GPU协同查询处理。我选择TPC-H Q5，做固定物理执行计划，而
不是SQL parser、优化器和事务系统。重点是Arrow列式输入、连接过滤传播、GPU
内存位置、resident生命周期和混合分片。

## 2. Q5与数据（1.5分钟）

lineitem是一条订单明细。只有1994年的订单、客户和供应商都在ASIA、二者同国家
的明细才计入。六表只保留必需列并转换为Arrow IPC。价格与折扣用整数定点数，
先在`revenue_1e4`尺度聚合。hash用于快速比较，官方oracle用于防止所有后端
一起算错。<!-- C001 C012 C017 -->

## 3. CPU与GPU计划（2分钟）

specialized CPU把supplier、customer、orders映射到nation，扫描lineitem时只
做数组查找和整数累加；Acero使用filter、hash join、aggregate和排序。SF1
resident p50是3.201与311.461 ms，SF10是14.955与3124.388 ms。这个差距是
专用计划与通用计划的综合差距，不是简单证明Arrow不好。<!-- C016 C021 -->

copy显式H2D到显存；managed使用统一地址并迁移/预取；mapped把pinned host
pages映射给GPU远程读。三者执行同一kernel。cuDF用DataFrame通用算子执行Q5。

## 4. Resident与混合（1.5分钟）

V7把Arrow加载、索引、分配和首次传输放入setup，request只使用已准备的数据。
混合路径按batch切分lineitem，保证每行只属于CPU或GPU。fixed扫描7个比例；
auto在setup里校准一次，再选择比例。摊销公式是setup/n加request中位数。
<!-- C011 -->

## 5. 实验方法（1分钟）

SF1/SF10各18组，每组3次warmup和10次measured request，总计各54/180。每条
记录绑定Git commit、数据manifest、二进制、GPU UUID、命令和结果。profiler
另跑10组，只解释NVTX、内存操作和kernel指标，不进入普通延迟表。

## 6. 结果（2分钟）

copy/managed/mapped在SF1为1.267/1.440/22.971 ms，在SF10为
15.416/15.066/358.582 ms。managed在SF10略低于copy，但差距小，不能外推成
所有规模都更好。mapped在两个规模都慢，NSYS与NCU都显示其SF10 kernel约
130 ms，原因是远程PCIe访问而不是显存带宽。<!-- C013 C018 -->

fixed hybrid最佳点从SF1的CPU=0.125、1.160 ms变为SF10的CPU=0.375、
10.054 ms。auto选择0.262和0.288，延迟1.553和10.980 ms，regret为33.89%和
9.21%。它判断了变化方向，却没有找到最优点。<!-- C014 C015 C019 C020 -->

## 7. 限制与结论（1分钟）

setup仍很重，SF10最佳fixed约4997 ms；只有重复很多次后，较低request才有
意义。项目只覆盖固定Q5、两个规模和一台GPU，setup也只测一次，没有多查询并发、
NUMA和完整GPU连接计划。我不会把结果推广成“GPU数据库普遍更快或更慢”。

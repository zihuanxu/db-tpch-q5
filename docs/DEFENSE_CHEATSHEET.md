# 答辩速查表

> 用法：先背讲解主线，不要背所有数字。老师追问时再看对应小节。

## 30 秒版本

这个仓库有两部分。前期在 ETH Zurich 内存哈希连接代码上比较 NPO、radix PRO、直接地址 VJ 和 PRVJ，研究 cache、TLB、NUMA 与空间开销。后期实现了一个固定的 TPC-H Q5 列式执行器，把六表连接预处理成按 key 直接索引的数组，CPU 或 CUDA 只扫描最大的 `lineitem` 表并按国家聚合。接手审计发现并修复了逐行截断收入的问题；最终 SF1 六个后端 hash 一致，而且与官方 `q5.out` 五行结果完全一致。

## 3 分钟版本

1. **问题**：Q5 要找指定地区和一年内，客户与供应商同国家的订单明细，按国家汇总 `extendedprice * (1-discount)`。
2. **数据**：只加载六张表里 Q5 需要的列；日期转 `int32` 天数，字符串字典编码，price 存分，discount 存基点。
3. **计划**：CPU 先建 `supplier -> nation`、`customer -> nation`、`order -> nation` 三类映射，地区和日期不满足的槽位写 `-1`。
4. **CPU**：多线程只切分最终 `lineitem`；每线程本地按 nation 聚合，最后归并，避免共享原子竞争。
5. **GPU**：复用 CPU plan，一个 CUDA 线程处理一条 lineitem，命中后对全局 nation 数组 `atomicAdd`。比较 device copy、managed memory、mapped zero-copy 三种内存模式。
6. **结果**：SF1 有 6,001,215 条 lineitem。CPU 8 线程查询内中位数约 22.86 ms；GPU kernel 约 0.22 至 2.70 ms，但 GPU total 约 238.70 至 323.24 ms，因为每次新进程都要初始化、分配和传输。
7. **边界**：C++ internal timing 不含文本加载，而 Arrow/cuDF internal timing 含 Arrow IPC 读取；所以跨实现不能只看 `total_ms` 排名。
8. **正确性**：六个后端哈希 `542abf4003633c7c`，再通过独立 oracle 工具与官方 `q5.out` 五行逐行核对。
9. **接手结论**：decimal 语义和 Arrow IPC 输入已补齐；下一步是统一 C++/Arrow 数据入口、GPU 常驻数据和 block-local aggregation。

## 必画的图

```text
region -> nation mask
               |-> supplier_nation_by_key --\
               |                              lineitem scan -> nation sum -> sort
               -> customer_nation_by_key -> order_nation_by_key --/
                                      + date filter
```

一句话解释：把通用六表 join 变成 CPU 预处理的直接索引映射，最终只保留一次大表扫描。

## 三种 GPU 模式

| 模式 | 一句话 |
| --- | --- |
| copy | 输入先拷进显存，kernel 最快，但有显式 H2D |
| managed | 统一地址，prefetch 到 GPU，编程方便但仍有迁移和管理成本 |
| mapped | GPU 直接读 pinned host memory，省显式大拷贝，但 PCIe 读取让 kernel 变慢 |

不要说“mapped 没有拷贝成本”。代码仍会先把原 C++ 列复制进 pinned buffer，只是这部分没有记在 `h2d_ms`。

## 必记数字

### 顶层 Q5

| 项 | 数字 |
| --- | ---: |
| SF1 lineitem | 6,001,215 |
| 正式结果行数 | 5 |
| 项目哈希 | `542abf4003633c7c` |
| CPU 8t internal median | 22.859 ms |
| CPU 8t scan median | 4.574 ms |
| gpu-copy kernel | 0.231 ms |
| gpu-copy total | 238.698 ms |
| gpu-mapped kernel | 2.703 ms |
| PyArrow total | 1,069.345 ms |
| cuDF total | 1,907.409 ms |
| 最小正式矩阵 | 18 rows, 0 errors；1 次预热 + 3 次重复 |

### 前期 hashjoin

| 项 | 数字 |
| --- | ---: |
| sweep | `S=2^30`, `R=2^5..2^30` |
| 每点重复 | 正式 CSV 主要为 1 次 |
| `R=S=2^30` PRO | 2.08 s, 516 M tuples/s |
| `R=S=2^30` PRVJ | 2.25 s, 477 M tuples/s |
| `R=S=2^30` VJ pw1 | 46.17 s |
| starjoin SF100 rows | 600,000,000 facts |
| starjoin SF100 VJ | 3.65 s |
| starjoin SF100 NPO | 15.55 s |
| starjoin SF100 `pro` label | 17.36 s |

## 五个最危险的问题

### 1. 结果是否严格正确？

回答：

> 现在每条收入保留到 `revenue_1e4`，聚合完成后才四舍五入到分。CPU、三种 CUDA、PyArrow、cuDF 的 hash 一致，并通过独立脚本与官方 `q5.out` 逐行核对。旧 hash `9f1f...` 是修复前结果，不能再作为最终证据。

### 2. 为什么 GPU 不如 CPU？

回答：

> 不是 kernel 慢，而是一次性 SF1 查询没有摊薄 CUDA context、分配、CPU plan 和传输。当前结果只适用于这套端到端执行方式，不能推广为 GPU 普遍慢。

### 3. GPU 的 8 threads 是什么？

回答：

> 最小正式实验固定命令参数为 8，但 GPU 代码不使用 `params.threads`。真正的 CUDA 并行度是固定 256-thread blocks 乘输入规模，因此不能把这个 8 解释成 GPU 线程数。

### 4. starjoin 的 PRO 真是 radix PRO 吗？

回答：

> 不是。代码中的 `pro` 标签实际使用开放寻址哈希表，并物化第一阶段中间结果。原版 radix PRO 在另一个 binary join 路径里。报告命名需要更正。

### 5. VJ 是 SIMD 吗？

回答：

> 不是。VJ 的 Vector 指按 key 直接索引的数组。它快在省掉哈希计算和冲突，代价是 key 稀疏或范围大时占空间，并增加 cache/TLB 压力。

## 其他高频问答

| 问题 | 回答关键词 |
| --- | --- |
| 为什么列式？ | 只读需要列、连续访问、便于 GPU copy |
| 为什么字典编码？ | GPU 不处理变长字符串，比较 code 更简单 |
| 为什么线程本地聚合？ | 避免共享 cache line 和 atomic 竞争 |
| CPU 为什么不线性加速？ | plan 单线程、线程开销、内存带宽、归并 |
| 为什么 direct array 可行？ | TPC-H key 较稠密；稀疏 key 时不一定合适 |
| 哈希证明什么？ | 证明后端结果一致；官方语义还要看 `oracle_check.json` |
| mapped 为什么 kernel 慢？ | GPU 经 PCIe 访问 host pinned memory |
| VJ 为什么大表退化？ | vector 超 cache/TLB，随机页访问 |
| PRVJ 解决什么？ | 先 radix 分区，让局部 vector 变小 |
| THP 是否保证大页？ | 否，`madvise` 只是请求 |
| sort-merge 是多线程吗？ | 否，忽略 `nthreads`，libc `qsort` 单线程 |
| full sweep 稳定吗？ | 只跑 1 次，能看趋势，不能做统计显著性结论 |

## 诚实说明 AI 参与

推荐表述：

> 代码和初始报告主要由 AI 辅助生成。我接手后核对了源码、Git、实验数据和日志，重新跑了 CPU/CUDA 自检，发现并修复了官方精度偏差，又补了 Arrow IPC、官方 oracle 和最小正式实验。现在我能按函数解释数据流，也清楚剩余的计时和实现边界。

这比声称“全部亲手从零写完”更经得住追问。

## 上台前自检

- [ ] 能手画六表到三个映射，再到 lineitem scan。
- [ ] 能解释 `total_ms` 和 `elapsed_ms` 的边界。
- [ ] 能解释为什么 result hash 和官方 oracle 是两层验证。
- [ ] 能说出 copy、managed、mapped 的数据位置。
- [ ] 能解释 GPU 1/2/4/8 标签为什么无效。
- [ ] 能区分 NPO、PRO、VJ、PRVJ。
- [ ] 能指出 VJ 不是 SIMD，present 也不是 bit bitmap。
- [ ] 能指出 starjoin `pro` 不是真 radix PRO。
- [ ] 能说明本次正式矩阵在 RTX 4090 上包含三种 CUDA、PyArrow 和 cuDF。
- [ ] 能给出三个接手后的改进优先级。

## 最后一句

> 这个项目真正有价值的地方，不只是哪个引擎快，而是它把 join、内存布局、CPU/GPU 数据移动和实验口径放在同一条执行链上；源码审计也说明，性能结果必须和正确性语义一起看。

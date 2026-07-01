# 内存连接算法探索课程报告

## 报告说明

本报告是在前期《内存连接算法报告》的基础上整理形成的最终版。前期报告已经完成服务器硬件参数测试、开源连接算法研究和部分综合对比测试。本次最终版保留前期报告的写法和章节组织，同时对前期内容中不够准确或不够完整的实验数据进行修订，并在后续章节继续补充期中 Star Join 多表连接实验和期末 VJ/PRVJ 扩展算法实验。

因此，本文不是重新写一个独立综述，而是按课程进度形成的连续报告：

1. 前期实验：硬件参数、NUMA、开源连接算法和基础综合测试。
2. 期中报告：基于 NPO/PRO/VJ 的 Star Join 多表连接实现与实验。
3. 期末报告：VJ、PRVJ 扩展算法、full sweep、工作集与 cache/TLB 分析。

开源项目链接：

```text
https://github.com/zihuanxu/db-tpch-q5
```

CPU 端代码位于 `hashjoin-cpu/` 子目录；GPU 端 TPC-H Q5 扩展实验位于仓库根目录和 `docs/` 目录。

## 1 通过测试工具了解服务器 CPU 关键硬件配置

### 1.1 实验环境

本实验在一台双路 Intel Xeon 服务器上完成，系统为 Linux。硬件与系统关键信息通过 `lscpu`、`numactl --hardware`、`/sys/devices/system/cpu/cpu0/cache/*` 和 Intel MLC v3.12 获取。

| 项目 | 实测结果 |
|---|---:|
| CPU 型号 | Intel Xeon Gold 5220 @ 2.20GHz |
| Socket 数 | 2 |
| 每路核心数 | 18 |
| SMT | 2 线程/核 |
| 总逻辑核数 | 72 |
| NUMA 节点数 | 2 |
| NUMA 距离 | 本地 10，远端 21 |
| L1d cache | 1.1 MiB, 36 instances |
| L2 cache | 36 MiB, 36 instances |
| L3 cache | 49.5 MiB, 2 instances |
| Memory | node0 128569 MB, node1 128963 MB |

说明：

1. 本机是典型双路 NUMA 多核平台。
2. 前期报告初版中使用 mbw 和 lmbench 进行近似测试；最终版补充了 Intel MLC v3.12 的延迟和带宽矩阵。
3. 后续实验同时关注算法吞吐和空间开销，因为连接算法性能与工作集是否落在 cache/本地 NUMA 内存中直接相关。

### 1.2 NUMA 延迟测试

使用 Intel MLC 的 `--latency_matrix` 测得 idle random access 延迟如下，单位为 ns：

| 来源 NUMA node | 目标 node 0 | 目标 node 1 |
|---:|---:|---:|
| node 0 | 82.8 | 140.8 |
| node 1 | 155.6 | 81.5 |

分析：

1. 本地 NUMA 访问约 81 到 83 ns。
2. 远端 NUMA 访问约 141 到 156 ns，约为本地访问的 1.7 到 1.9 倍。
3. 对 NPO/PRO 这类大量访问 hash table 或 partition buffer 的算法，线程与数据的 NUMA 放置会显著影响性能。

### 1.3 NUMA 带宽测试

使用 Intel MLC 的 `--bandwidth_matrix` 测得 read-only bandwidth 如下，单位为 MB/s：

| 来源 NUMA node | 目标 node 0 | 目标 node 1 |
|---:|---:|---:|
| node 0 | 74078.8 | 34280.9 |
| node 1 | 34360.1 | 74238.6 |

结论：

1. 本地读带宽约 74 GB/s。
2. 远端读带宽约 34 GB/s。
3. 本地读带宽约为远端读带宽的 2.16 倍。
4. 这说明硬件感知型连接算法不仅要减少计算，还要尽量减少远端访存和随机访存。

### 1.4 前期 NUMA 对比实验

在 `R=S=2^24`、16 线程下，对 NPO、PRO、VJ 分别测试默认分配、`--basic-numa` 和 `numactl --interleave=all`。

| 算法 | 内存放置 | 时间 ms | 结果数 |
|---|---|---:|---:|
| NPO | none | 478.582 | 16777216 |
| NPO | `--basic-numa` | 257.520 | 16777216 |
| NPO | interleave | 241.643 | 16777216 |
| PRO | none | 202.307 | 16777216 |
| PRO | `--basic-numa` | 120.857 | 16777216 |
| PRO | interleave | 145.072 | 16777216 |
| VJ-1B | none | 430.644 | 16777216 |
| VJ-1B | `--basic-numa` | 458.470 | 16777216 |
| VJ-1B | interleave | 458.208 | 16777216 |

结果分析：

1. NPO 和 PRO 明显受益于 NUMA 优化。
2. PRO 的 `--basic-numa` 效果最好，说明 radix partition 之后的局部访问更容易从本地 NUMA 分配中受益。
3. VJ 在该点没有受益，说明当 vector index 较紧凑时，瓶颈不一定是 NUMA 放置，而可能是随机数组访问、cache/TLB 或后续更大工作集问题。

## 2 开源连接算法研究

### 2.1 研究对象与代码基线

本实验基于 ETH Zurich VLDB 2013 main-memory hash join 开源代码框架进行扩展。最终统一关注以下代表算法：

| 算法 | 类型 | 特点 |
|---|---|---|
| NPO | 哈希连接 | 无分区，固定开销小，小数据下通常最快 |
| PRO | 哈希连接 | Parallel Radix Join，硬件感知型分区实现 |
| sort-merge | 排序归并 | 作为排序归并代表实现 |
| VJ | Vector Join | 利用连续主键构造 vector index，用数组访问替代 hash probe |
| PRVJ | Partitioned Vector Join | 将 radix partition 与 VJ 结合，缩小局部 vector 工作集 |

前期报告初版主要比较 NPO、PRO 和排序归并代表实现。后续期末作业在同一代码框架中继续加入 VJ、PRVJ，并将所有算法放入同一 full sweep 中比较。

### 2.2 代码增强与统计项补充

为了满足课程要求，源码做了以下增强：

1. 在 `mchashjoins` 中增加统一算法开关，支持 `NPO`、`PRO`、`sort-merge`、`VJ`、`PRVJ` 和 `starjoin`。
2. NPO 增加 hash table 空间统计：

```text
NPO_STATS_CSV_HEADER,hash_table_bytes,total_extra_space_bytes,primary_bucket_bytes,overflow_bucket_bytes
```

3. VJ 增加 vector index 空间统计：

```text
VJ_STATS_CSV_HEADER,payload_width_bytes,vector_index_bytes,vector_total_bytes,total_extra_space_bytes
```

4. PRVJ 和 sort-merge 接入统一统计脚本，便于汇总吞吐率和空间开销。
5. 新增 Star Join 数据生成和执行路径，用于期中多表连接实验。
6. 新增 `scripts/self_check_assignment.sh`，用于构建和小规模正确性自检。

关键实现文件如下：

- `src/main.c`
- `src/no_partitioning_join.c`
- `src/parallel_radix_join.c`
- `src/vector_join.c`
- `src/starjoin.c`
- `src/starjoin.h`

这些改动使实验结果不仅能比较“谁更快”，还能比较“为什么快”和“空间代价是什么”。

### 2.3 算法执行入口

典型命令如下：

```bash
./src/mchashjoins -a NPO
./src/mchashjoins -a PRO
./src/mchashjoins -a sort-merge
./src/mchashjoins -a VJ --payload-width=1
./src/mchashjoins -a VJ --payload-width=2
./src/mchashjoins -a VJ --payload-width=4
./src/mchashjoins -a VJ --payload-width=1 --vj-hugepage
./src/mchashjoins -a PRVJ --payload-width=4
./src/mchashjoins --starjoin=npo --sf=100 -n 64
./src/mchashjoins --starjoin=pro --sf=100 -n 64
./src/mchashjoins --starjoin=vj  --sf=100 -n 64 --payload-width=1
```

## 3 前期综合对比测试修订

### 3.1 实验设计

前期报告中按照老师要求做 full sweep 综合对比。最终版继续保留这一设计，并在期末扩展到 VJ/PRVJ。

| 项目 | 取值 |
|---|---|
| 固定探测表 `|S|` | `2^30` tuples |
| 构建表 `|R|` | `2^5 ~ 2^30`，共 26 个点 |
| 哈希连接代表算法 | NPO, PRO |
| 排序归并代表算法 | sort-merge |
| 扩展算法 | VJ-1B, VJ-2B, VJ-4B, PRVJ-4B |
| 性能指标 | 吞吐率 MTuples/s |
| 空间指标 | extra space / input |

说明：

1. 前期报告初版只展示 NPO、PRO 和排序归并代表实现。
2. 最终版将期末扩展算法纳入同一张 full sweep 表和图中，避免分散比较。
3. 原始 CSV 保存在 `docs/data/final_full_all_s2e30_summary.csv` 和对应 raw CSV 中。

### 3.2 综合性能结果

合并后 7 个变体各 26 个点全部为 `OK`：

- NPO
- PRO
- sort-merge
- VJ-1B、VJ-2B、VJ-4B
- PRVJ-4B

吞吐曲线如下：

![final full throughput](assets/final-full-all-s2e30_throughput.svg)

图 3-1 Full sweep 吞吐率总览

空间开销曲线如下：

![final full space](assets/final-full-all-s2e30_space.svg)

图 3-2 Full sweep 空间开销对比

选取关键点的吞吐率如下，单位 MTuples/s：

| log2(|R|) | NPO | PRO | sort-merge | VJ-1B | VJ-2B | VJ-4B | PRVJ-4B |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 | 6686.06 | 1266.16 | 9.24 | 6944.26 | 5351.32 | 7605.43 | 1165.40 |
| 20 | 3690.51 | 1062.53 | 6.43 | 4535.82 | 4653.81 | 4178.67 | 1075.71 |
| 24 | 784.09 | 1126.27 | 5.86 | 1035.26 | 716.63 | 606.64 | 1077.05 |
| 28 | 373.69 | 772.47 | 4.60 | 89.43 | 73.70 | 71.69 | 842.03 |
| 30 | 136.19 | 516.02 | 2.79 | 23.26 | 20.40 | 19.20 | 476.67 |

关键空间开销比如下：

| log2(|R|) | NPO | sort-merge | VJ-1B | VJ-2B | VJ-4B | PRVJ-4B |
|---:|---:|---:|---:|---:|---:|---:|
| 24 | 0.031010 | 1.000000 | 0.003846 | 0.005769 | 0.009615 | 0.009615 |
| 30 | 1.000122 | 1.000000 | 0.125000 | 0.187500 | 0.312500 | 0.312500 |

结果分析：

1. 小 R 下，NPO 和直接 VJ 的固定开销低，吞吐最高。
2. PRO 在大 R 下更稳定，说明 radix partition 能有效缓解大 hash table 的随机访存问题。
3. sort-merge 在本实现中吞吐明显低于 hash/vector 方案，但空间开销可预测。
4. VJ 在小工作集时很快，但当 vector index 工作集变大后吞吐快速下降。
5. PRVJ 在大 R 下明显优于直接 VJ，说明分区可以恢复一部分局部性。

## 4 期中课程报告：Star Join 多表连接

### 4.1 任务要求

期中任务要求在原 hashjoin 框架中增加 star join 算法，支持基于 `npo`、`pro`、`vj` 的多表连接：

```bash
./src/mchashjoins --starjoin=npo --sf=100 -n <threads>
./src/mchashjoins --starjoin=pro --sf=100 -n <threads>
./src/mchashjoins --starjoin=vj  --sf=100 -n <threads>
```

数据模型如下：

| 表 | 结构 | 规模 |
|---|---|---:|
| L | `<FKO, FKPS, PAYLOAD>` | `sf * 6000000` |
| O | `<PK, PAYLOAD>` | `sf * 1500000` |
| PS | `<PK, PAYLOAD>` | `sf * 800000` |

查询任务为：

```sql
select sum(O.PAYLOAD - (L.PAYLOAD - PS.PAYLOAD))
from L, O, PS
where L.FKO = O.PK
  and L.FKPS = PS.PK;
```

PDF 中表达式结果与“结果等于记录数”的文字说明存在差异。按表达式和常量 `O.PAYLOAD=3`、`L.PAYLOAD=1`、`PS.PAYLOAD=1`，每行贡献为 3。因此本实验使用 `matches` 验证连接基数，用 `aggregate_sum` 验证表达式计算。

### 4.2 实现方式

NPO star join 采用 pipeline 方式，为 O 和 PS 构建 hash table，扫描 L 表时依次 probe 两个维表，不物化中间结果。

PRO star join 采用物化方式，先执行 L 与 PS 的分区哈希连接，生成中间表，再与 O 表进行分区哈希连接。该方式有利于局部性，但空间开销较高。

VJ star join 利用 O 和 PS 的连续主键，为两个维表建立 vector index。扫描 L 表时，外键直接映射到 vector index 下标，减少 hash probe 和链式访问。

### 4.3 `sf=100` 主实验

正式实验使用 `sf=100`、64 线程、VJ `payload_width=1`。三种模式均输出：

```text
matches=600000000
aggregate_sum=1800000000
```

主结果如下：

| mode | threads | time ms | aux/input |
|---|---:|---:|---:|
| npo | 64 | 15552.289 | 1.455 |
| pro | 64 | 17355.132 | 2.178 |
| vj | 64 | 3645.527 | 0.069 |

分析：

1. VJ 比 NPO 快约 4.27 倍，比 PRO 快约 4.76 倍。
2. VJ 辅助空间约为输入的 6.9%，明显低于 NPO 和 PRO。
3. Star join 中 O/PS 都是连续主键维表，正好符合 vector index 的假设。
4. PRO 由于需要物化中间表，空间开销最高。

### 4.4 NUMA、Prefetch 与 PRO 顺序对比

`--basic-numa` 对 star join 没有带来收益：

| mode | non-NUMA ms | `--basic-numa` ms | 变化 |
|---|---:|---:|---:|
| npo | 15552.289 | 16666.803 | +7.2% |
| pro | 17355.132 | 18066.061 | +4.1% |
| vj | 3645.527 | 4354.036 | +19.4% |

原因是 star join 的维表结构会被所有线程共享访问，简单按线程本地分配不能保证 probe 阶段都是本地访问。

prefetch distance 对 VJ 有小幅收益：

| prefetch distance | npo ms | vj ms |
|---:|---:|---:|
| 0 | 15623.447 | 3729.132 |
| 8 | 15206.068 | 3602.398 |
| 32 | 15187.158 | 3441.220 |

PRO 两种物化顺序都正确，order 0 略快：

| pro order | time ms | matches |
|---:|---:|---:|
| 0 | 17205.709 | 600000000 |
| 1 | 17613.673 | 600000000 |

### 4.5 期中小结

期中实验完成了 NPO、PRO、VJ 三种 star join 模式。结果说明，在维表主键连续且 key range 可控时，VJ 能将 hash probe 转换为数组下标访问，因此同时获得更好的性能和空间效率。但这个结论依赖 vector index 工作集不太大；如果 key range 继续增大，VJ 的数组随机访问会受到 cache/TLB 限制。这一问题在期末实验中继续分析。

## 5 期末课程报告：VJ / PRVJ 扩展算法与性能分析

### 5.1 VJ 与 PRVJ 算法设计

VJ 的核心思想是利用 R 表主键连续整数的特点，用 vector index 替代 hash table。构建阶段按 key 将 payload 写入 vector；probe 阶段用 S 表 key 直接作为 vector 下标访问。

VJ 的优点是：

1. 避免 hash 计算。
2. 避免链式 bucket 访问和冲突处理。
3. 在 key range 小、vector index 可缓存时性能很高。
4. 可以通过 `payload_width=1/2/4` 模拟不同 payload 压缩率。

VJ 的问题是：

1. vector index 大小由 `max_key` 决定，不只由 `|R|` 决定。
2. key range 过大时，vector 工作集会超过 L3 cache 和 TLB 覆盖能力。
3. probe 阶段退化为大量随机内存访问。

PRVJ 的设计目标是缓解这个问题。它先对数据进行 radix partition，使每个分区内的 key range 变小，再在分区内部使用 VJ，从而缩小局部 vector 工作集。

### 5.2 VJ 工作集实验

VJ cache-size sweep 固定 `S=2^30`、64 线程、`payload_width=1`，让 vector index 大小覆盖 L1/L2/L3 的 10% 到 150%，并继续扩展到 L3 的 10 倍。

![VJ cache sweep](assets/vj-cache-sweep-s2e30.svg)

图 5-1 VJ vector 工作集大小与吞吐关系

关键点如下：

| label | vector bytes | cache ratio | time ms | throughput MT/s |
|---|---:|---:|---:|---:|
| L1_10 | 3,278 | 0.10 | 137.894 | 7786.72 |
| L2_50 | 524,290 | 0.50 | 131.454 | 8168.19 |
| L2_150 | 1,572,866 | 1.50 | 205.453 | 5226.22 |
| L3_100 | 51,904,514 | 1.00 | 1861.238 | 576.90 |
| L3_10x | 519,045,122 | 10.00 | 12352.788 | 86.92 |

可以看到，VJ 在工作集落入 cache 时吞吐很高；当 vector 工作集接近或超过 L3 后，性能迅速下降。

### 5.3 性能模型与 perf 观察

简化代价模型如下：

```text
T ~= T_build_vector(R, W) + T_probe(S, W)
W ~= max_key(R) * (present_bytes + payload_width)
```

其中 `W` 是 vector index 工作集大小。该模型强调：VJ 的性能不只由 `|R|` 决定，还由 key range 和 payload width 决定。

perf 代表性计数如下：

| payload width | log2(|R|) | vector bytes | time ms | cache miss rate | dTLB miss rate |
|---:|---:|---:|---:|---:|---:|
| 1 | 20 | 2,097,154 | 24.214 | 46.36% | 4.03% |
| 1 | 24 | 33,554,434 | 466.315 | 52.33% | 7.68% |
| 4 | 24 | 83,886,085 | 595.599 | 63.02% | 8.03% |

分析：

1. 随着 vector bytes 增大，cache miss rate 上升。
2. payload width 从 1 增加到 4 后，vector 工作集变大，执行时间进一步增加。
3. dTLB miss rate 也上升，说明大工作集下 TLB 覆盖能力是重要因素之一。

当前机器 `HugePages_Total=0`，THP 为 `madvise` 模式。代码实现了 `--vj-hugepage`，在 VJ vector 分配后调用 `madvise(MADV_HUGEPAGE)`；实测中 dTLB miss rate 未出现稳定改善，因此将其记录为大页尝试和环境限制。

### 5.4 期末综合结论

期末实验把 NPO、PRO、sort-merge、VJ、PRVJ 放到同一 full sweep 中比较，最终结论如下：

1. 小 R 或小 key range 下，VJ/NPO 固定开销低，吞吐最高。
2. 当 vector index 工作集超过 cache/TLB 覆盖能力后，直接 VJ 性能明显下降。
3. PRVJ 通过 radix partition 缩小局部 vector 工作集，在大 R 下显著优于直接 VJ。
4. PRO 在大规模区间保持稳定，是较稳健的通用选择。
5. sort-merge 空间和性能模式可预测，但在本实现和本实验配置下不是最快方案。

因此，期末阶段对前期报告的补充不是简单增加一个算法，而是进一步说明了 hardware-conscious join 的核心规律：算法性能取决于工作集与 cache、TLB、NUMA 的匹配关系。

## 6 复现方式与自检

完整自检：

```bash
cd hashjoin-cpu
scripts/self_check_assignment.sh
```

期中 star join 复现：

```bash
scripts/run_starjoin_comparison.sh --run \
  --sf 100 \
  --threads-list "64" \
  --modes "npo pro vj" \
  --prefetch-distances "0" \
  --payload-width 1 \
  --timeout-seconds 3600
```

期末 full sweep 复现：

```bash
scripts/run_extended_algo_comparison.sh --run \
  --threads 64 \
  --repeats 1 \
  --s-size 1073741824 \
  --r-exp-min 5 \
  --r-exp-max 30 \
  --algorithms "NPO PRO VJ_pw1 VJ_pw2 VJ_pw4 PRVJ_best" \
  --prvj-radix-bits 18 \
  --prvj-payload-width 4 \
  --timeout-seconds 3600 \
  --out-prefix final-full-main-s2e30
```

已完成的检查：

```text
[PASS] assignment self-check passed
```

补充检查：

1. full sweep summary 中 7 个变体各 26 个点，bad=0。
2. star join `sf=100` CSV 所有点 status=OK。
3. `COURSE_REPORT.docx`、`MIDTERM_REPORT.docx`、`EXPERIMENT_REPORT.docx` 已生成。
4. 最终代码包已生成在仓库根目录 `dist/memory-db-tpch-q5-final.tar.gz`。

## 7 总结

本报告沿用前期《内存连接算法报告》的写法，并在其后续写期中和期末大作业内容。最终完成情况如下：

1. 前期实验修订了 CPU/NUMA/MLC 硬件测试结果，并保留 NPO、PRO、sort-merge 的基础综合对比。
2. 期中实验实现了 NPO、PRO、VJ 三种 Star Join，多表连接在 `sf=100` 下全部正确，VJ 在性能和空间上明显占优。
3. 期末实验实现并分析了 VJ 和 PRVJ，把 NPO、PRO、sort-merge、VJ、PRVJ 放入同一 full sweep 中比较。
4. VJ 的优势来自数组访问和低空间开销，但其边界来自 vector index 工作集超过 cache/TLB 后的随机访存。
5. PRVJ 通过分区恢复局部性，是对直接 VJ 的有效扩展。

总体规律可以概括为：小范围连续 key 场景下优先考虑 VJ；大范围工作集下直接 VJ 会退化，需要 PRVJ 或 PRO 这样的分区方案；排序归并作为代表实现可以提供稳定但不占优的参考曲线。最终代码、实验脚本、原始 CSV、图表和报告已经整合到同一开源仓库中。

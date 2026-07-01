# 内存连接算法课程完整报告

## 摘要

本报告按照课程推进顺序整理本学期工作：先通过平常实验观察硬件内存层次和 NUMA 对连接算法的影响，再在期中阶段把二表连接扩展到三表 star join，最后在期末阶段系统比较 NPO、PRO、sort-merge、VJ 和 PRVJ 等连接算法在不同数据规模下的吞吐、空间开销和 cache/TLB 行为。

工程实现基于 ETH Zurich VLDB 2013 main-memory hash join 开源代码框架扩展。所有 CPU 端算法统一保留在 `hashjoin-cpu/` 目录下，通过命令行参数选择算法和实验配置。GPU 端 TPC-H Q5 实验作为同一 GitHub 仓库中的独立扩展项目提交，最终报告位于仓库根目录 `docs/`。

开源项目链接：

```text
https://github.com/zihuanxu/db-tpch-q5
```

本报告的定位是统一课程报告；单独的期中和期末详细报告作为附件保留：

| 文档 | 位置 |
|---|---|
| 统一课程报告 | `hashjoin-cpu/docs/COURSE_REPORT.md/.docx` |
| 期中 Star Join 报告 | `hashjoin-cpu/docs/MIDTERM_REPORT.md/.docx` |
| 期末 CPU hashjoin 报告 | `hashjoin-cpu/docs/EXPERIMENT_REPORT.md/.docx` |
| TPC-H Q5/GPU 扩展报告 | `docs/FINAL_REPORT.md/.docx` |

## 1. 研究主线

本学期实验围绕一个核心问题展开：内存数据库连接算法的性能瓶颈究竟来自哪里，算法设计应该如何适配现代多核 NUMA 机器的内存层次。

平常实验先从硬件出发。MLC 和 NUMA 对比表明，本地内存与远端内存的访问代价差异很大；同一个连接算法在不同内存放置策略下会有明显性能变化。这一阶段的结论是：连接算法不能只比较计算复杂度，还必须分析内存访问模式、数据放置和 cache locality。

期中实验在这个基础上进一步扩展问题规模。二表连接只暴露一次 build/probe，而 star join 需要同时访问事实表和多个维表。该实验用于观察 pipeline hash join、partitioned/materialized join 和 vector index join 在多表连接中的差异。结果显示，当维表主键连续且范围可控时，VJ 能把 hash probe 变成数组访问，在性能和空间上都明显优于 NPO 和 PRO。

期末实验继续追问 VJ 的边界。VJ 在小 key range 下很快，但它的 vector index 工作集会随 `max_key` 增大。当工作集超过 L3 cache 和 TLB 覆盖能力后，随机数组访问会退化，VJ 性能快速下降。因此期末阶段加入 full sweep、VJ cache-size sweep、perf 计数和 PRVJ 分区实验，最终形成结论：VJ 适合小范围连续 key；大范围场景下，PRVJ 通过 radix partition 缩小局部工作集，能恢复一部分局部性。

## 2. 工程整合

### 2.1 代码框架

CPU 端代码保留在原 hashjoin 框架中，并新增或扩展以下文件：

- `src/main.c`
- `src/no_partitioning_join.c`
- `src/vector_join.c`
- `src/vector_join.h`
- `src/parallel_radix_join.c`
- `src/parallel_radix_join.h`
- `src/starjoin.c`
- `src/starjoin.h`

算法通过统一命令行入口运行：

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

### 2.2 统计输出

为了让不同算法可以统一汇总，实验中增加了吞吐和空间统计。

NPO 输出 hash table 空间统计：

```text
NPO_STATS_CSV_HEADER,hash_table_bytes,total_extra_space_bytes,primary_bucket_bytes,overflow_bucket_bytes
```

VJ 输出 vector index 空间统计：

```text
VJ_STATS_CSV_HEADER,payload_width_bytes,vector_index_bytes,vector_total_bytes,total_extra_space_bytes
```

PRVJ 和 sort-merge 也接入统一统计脚本，用于生成最终报告中的吞吐曲线和空间开销曲线。

### 2.3 作业要求对照

| 要求 | 完成情况 | 位置 |
|---|---|---|
| 扩展 hashjoin 开源框架 | 已完成 | `src/`、`scripts/` |
| 通过算法开关执行测试 | 已完成 | `-a VJ/PRVJ/sort-merge`、`--starjoin=...` |
| 平常硬件与 NUMA 实验 | 已完成 | `docs/data/mlc_*.txt`、`docs/data/numa_comparison.csv` |
| 期中 star join 实验与报告 | 已完成 | `docs/MIDTERM_REPORT.md/.docx` |
| 期末 full sweep 实验与报告 | 已完成 | `docs/EXPERIMENT_REPORT.md/.docx` |
| 统一课程报告 | 已完成 | `docs/COURSE_REPORT.md/.docx` |
| 原始数据和图表 | 已完成 | `docs/data/`、`docs/assets/` |
| 代码打包 | 已完成 | 仓库根目录 `dist/memory-db-tpch-q5-final.tar.gz` |

## 3. 实验环境

硬件信息由 `lscpu`、`numactl --hardware` 和 Intel MLC 获取。

| 项目 | 配置 |
|---|---:|
| CPU | Intel Xeon Gold 5220 @ 2.20GHz |
| Socket | 2 |
| Core / socket | 18 |
| SMT | 2 |
| Logical CPUs | 72 |
| L1d cache | 1.1 MiB, 36 instances |
| L2 cache | 36 MiB, 36 instances |
| L3 cache | 49.5 MiB, 2 instances |
| NUMA nodes | 2 |
| NUMA distance | local 10, remote 21 |
| Memory | node0 128569 MB, node1 128963 MB |

Intel MLC v3.12 测得本地 NUMA 延迟约 81 到 83 ns，远端 NUMA 延迟约 141 到 156 ns。MLC read-only bandwidth 显示本地读带宽约 74 GB/s，远端读带宽约 34 GB/s。原始输出保存在：

- `docs/data/mlc_latency_matrix.txt`
- `docs/data/mlc_bandwidth_matrix.txt`
- `docs/data/mlc_idle_latency.txt`
- `docs/data/mlc_loaded_latency.txt`

这些测量结果解释了后续连接实验中的一个基本现象：当算法访问模式导致大量跨 NUMA 访问或随机内存访问时，即使计算量不大，执行时间也会明显增加。

## 4. 平常实验：NUMA 与内存层次

平常实验的目的不是比较所有算法的最终优劣，而是先建立对机器内存层次的认识。实验使用 `R=S=2^24`、16 线程，对 NPO、PRO、VJ 分别测试默认分配、`--basic-numa` 和 `numactl --interleave=all`。

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

NPO 和 PRO 明显受益于 NUMA 优化。NPO 的 hash table probe 和 PRO 的分区访问都会受到内存放置影响；改善本地访问比例后，时间明显下降。VJ 在该点没有受益，原因是 vector index 较紧凑，主要瓶颈不在 NUMA 放置，而在后续更大 key range 下的随机数组访问。

这一阶段得到的结论是：连接算法的比较必须同时看时间、空间和访存模式。这个结论直接引出了期中 star join 实验，因为多表连接会放大维表结构访问和中间结果物化的代价。

## 5. 从平常实验到期中实验

平常实验关注的是二表连接和硬件局部性，期中实验则把问题推进到多表连接。Star join 是分析型数据库中常见形态：一个大事实表连接多个维表。它比二表 join 更能体现以下问题：

- 是否需要物化中间结果。
- 维表结构是否可以常驻 cache。
- hash probe 是否可以被更简单的数组下标访问替代。
- 多表连接中的空间开销是否会随中间表快速放大。

因此，期中阶段实现了三种 star join 模式：NPO pipeline、PRO materialized 和 VJ pipeline。三种模式共享同一数据生成器和结果校验方式，方便比较算法本身差异。

## 6. 期中实验：Star Join

### 6.1 数据模型

期中任务要求实现三表 star join：

| 表 | 结构 | 规模 |
|---|---|---:|
| L | `<FKO, FKPS, PAYLOAD>` | `sf * 6000000` |
| O | `<PK, PAYLOAD>` | `sf * 1500000` |
| PS | `<PK, PAYLOAD>` | `sf * 800000` |

查询为：

```sql
select sum(O.PAYLOAD - (L.PAYLOAD - PS.PAYLOAD))
from L, O, PS
where L.FKO = O.PK
  and L.FKPS = PS.PK;
```

PDF 中表达式结果与“结果等于记录数”的文字说明存在矛盾。按表达式和常量 O=3、L=1、PS=1，每行贡献为 3。因此实验使用 `matches` 验证连接基数，用 `aggregate_sum` 验证表达式计算。

### 6.2 实现方式

NPO star join 先为 O 和 PS 构建 hash table，然后扫描 L 表并依次 probe 两个维表。它不物化中间表，逻辑简单，但每行需要两次 hash probe。

PRO star join 使用分区和物化策略。它可以改善局部性，但 L 与 O/PS 的连接结果规模等于 L 表规模，物化中间结果会显著增加空间开销。

VJ star join 利用 O 和 PS 的连续主键性质，为维表建立 vector index。扫描 L 表时，外键可以直接作为数组下标访问维表 payload，避免 hash 计算、链式访问和冲突处理。

### 6.3 `sf=100` 主结果

正式实验使用 `sf=100`、64 线程、VJ `payload_width=1`。三种模式均输出 `matches=600000000`、`aggregate_sum=1800000000`。

| mode | threads | time ms | aux/input |
|---|---:|---:|---:|
| npo | 64 | 15552.289 | 1.455 |
| pro | 64 | 17355.132 | 2.178 |
| vj | 64 | 3645.527 | 0.069 |

VJ 比 NPO 快约 4.27 倍，比 PRO 快约 4.76 倍。空间上，VJ 辅助空间约为输入的 6.9%，显著低于 NPO 和 PRO。

### 6.4 NUMA、Prefetch 与 PRO 顺序

`--basic-numa` 在 star join 上没有带来收益：

| mode | non-NUMA ms | `--basic-numa` ms | 变化 |
|---|---:|---:|---:|
| npo | 15552.289 | 16666.803 | +7.2% |
| pro | 17355.132 | 18066.061 | +4.1% |
| vj | 3645.527 | 4354.036 | +19.4% |

原因是 star join 的维表结构会被所有线程共享访问，简单按线程本地分配并不能保证 probe 阶段都是本地访问，反而可能增加初始化和跨 socket 访问成本。

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

期中实验的结论是：在维表主键连续、key range 可控的 star join 中，VJ 的数组访问模型明显优于 hash table 和物化分区方案。但这个结论也留下一个问题：如果 key range 继续扩大，vector index 工作集超过 cache/TLB 覆盖能力，VJ 是否还能保持优势。这正是期末实验继续研究的问题。

## 7. 从期中实验到期末实验

期中结果说明 VJ 在 star join 中效果很好，但这个优势依赖一个前提：vector index 的工作集不能太大。平常实验已经说明内存层次会显著影响连接性能，因此期末实验不再只看一个固定规模点，而是固定 `S` 表为大表，系统扫描 `R` 表规模和 key range，观察算法性能随工作集扩大如何变化。

期末阶段的核心问题包括：

- VJ 在小 R 和大 R 下是否都快。
- payload width 增大后，vector index 空间和 cache miss 如何变化。
- PRVJ 是否能通过分区缩小局部工作集。
- PRO、NPO、sort-merge 在大规模点是否更稳定。
- 空间开销和吞吐之间如何权衡。

## 8. 期末实验：Full Sweep

期末主实验固定 `|S|=2^30`，扫描 `|R|=2^5..2^30`。最终覆盖 7 个算法变体：

- NPO
- PRO
- sort-merge
- VJ-1B、VJ-2B、VJ-4B
- PRVJ-4B

合并后 7 个变体各 26 个点全部为 `OK`。`R=2^5` 在 64 线程下触发原始生成器极小 R 边界问题，因此该点使用 32 线程补跑；其余 full sweep 点均为 64 线程。

吞吐曲线：

![final full throughput](assets/final-full-all-s2e30_throughput.svg)

空间开销曲线：

![final full space](assets/final-full-all-s2e30_space.svg)

关键吞吐结果如下，单位 MTuples/s：

| log2(|R|) | NPO | PRO | sort-merge | VJ-1B | VJ-2B | VJ-4B | PRVJ-4B |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 | 6686.06 | 1266.16 | 9.24 | 6944.26 | 5351.32 | 7605.43 | 1165.40 |
| 20 | 3690.51 | 1062.53 | 6.43 | 4535.82 | 4653.81 | 4178.67 | 1075.71 |
| 24 | 784.09 | 1126.27 | 5.86 | 1035.26 | 716.63 | 606.64 | 1077.05 |
| 28 | 373.69 | 772.47 | 4.60 | 89.43 | 73.70 | 71.69 | 842.03 |
| 30 | 136.19 | 516.02 | 2.79 | 23.26 | 20.40 | 19.20 | 476.67 |

关键空间开销比：

| log2(|R|) | NPO | sort-merge | VJ-1B | VJ-2B | VJ-4B | PRVJ-4B |
|---:|---:|---:|---:|---:|---:|---:|
| 24 | 0.031010 | 1.000000 | 0.003846 | 0.005769 | 0.009615 | 0.009615 |
| 30 | 1.000122 | 1.000000 | 0.125000 | 0.187500 | 0.312500 | 0.312500 |

实验结果表明，小 R 下直接 VJ 很快；当 vector working set 超过 L3 后，VJ 性能快速下降；PRVJ 能在大 R 下通过分区显著恢复性能；PRO 在最大规模点表现稳定。

## 9. VJ 工作集与 TLB 分析

为了验证 VJ 下降是否与工作集大小相关，期末进一步做了 cache-size sweep。实验固定 `S=2^30`、64 线程、`payload_width=1`，让 vector index 大小覆盖 L1/L2/L3 的 10% 到 150%，并继续扩展到 L3 的 10 倍。

![VJ cache sweep](assets/vj-cache-sweep-s2e30.svg)

| label | vector bytes | cache ratio | time ms | throughput MT/s |
|---|---:|---:|---:|---:|
| L1_10 | 3,278 | 0.10 | 137.894 | 7786.72 |
| L2_50 | 524,290 | 0.50 | 131.454 | 8168.19 |
| L2_150 | 1,572,866 | 1.50 | 205.453 | 5226.22 |
| L3_100 | 51,904,514 | 1.00 | 1861.238 | 576.90 |
| L3_10x | 519,045,122 | 10.00 | 12352.788 | 86.92 |

简化代价模型：

```text
T ~= T_build_vector(R, W) + T_probe(S, W)
W ~= max_key(R) * (present_bytes + payload_width)
```

当工作集小于 cache/TLB 覆盖能力时，probe 是低延迟数组访问；当工作集接近或超过 L3 后，probe 退化为高比例随机内存访问。

perf 代表性计数：

| payload width | log2(|R|) | vector bytes | time ms | cache miss rate | dTLB miss rate |
|---:|---:|---:|---:|---:|---:|
| 1 | 20 | 2,097,154 | 24.214 | 46.36% | 4.03% |
| 1 | 24 | 33,554,434 | 466.315 | 52.33% | 7.68% |
| 4 | 24 | 83,886,085 | 595.599 | 63.02% | 8.03% |

当前机器 `HugePages_Total=0`，THP 为 `madvise` 模式。代码实现了 `--vj-hugepage`，在 VJ vector 分配后调用 `madvise(MADV_HUGEPAGE)`；实测中 dTLB miss rate 未出现稳定改善，因此报告将其作为大页尝试和环境限制记录。

## 10. 阶段性结论

按课程推进顺序，三个阶段形成了连续关系：

1. 平常实验说明硬件内存层次和 NUMA 对连接算法有实际影响，不能只看算法形式。
2. 期中 star join 把二表连接扩展到多表连接，证明在连续主键维表场景下，VJ 的数组访问可以显著减少时间和空间开销。
3. 期末 full sweep 进一步说明 VJ 的优势有边界：工作集超过 cache/TLB 后性能下降，而 PRVJ 可以通过分区恢复局部性。

因此，本课程最终形成的算法理解是：NPO 简单但容易受 hash table 随机访问影响；PRO 通过分区改善局部性但空间和物化代价较高；VJ 在连续 key 和小工作集下最好；PRVJ 是对 VJ 大工作集问题的改进。

## 11. 复现方式

完整自检：

```bash
scripts/self_check_assignment.sh
```

期末 full sweep：

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

期中 star join：

```bash
scripts/run_starjoin_comparison.sh --run \
  --sf 100 \
  --threads-list "64" \
  --modes "npo pro vj" \
  --prefetch-distances "0" \
  --payload-width 1 \
  --timeout-seconds 3600
```

## 12. 自检结果

已运行：

```text
[PASS] assignment self-check passed
```

补充检查：

- `git diff --check` 通过。
- full sweep summary 中 7 个变体各 26 个点，bad=0。
- star join `sf=100` CSV 所有点 status=OK。
- `docs/EXPERIMENT_REPORT.docx`、`docs/MIDTERM_REPORT.docx`、`docs/COURSE_REPORT.docx` 已生成。

## 13. 局限

1. `R=2^5` 在 64 线程下触发原始生成器的极小 R 边界问题，因此该点用 32 线程补跑。
2. PRO 当前未输出统一 extra-space 字段，空间表只比较已实现统计的算法。
3. 当前环境没有预留 hugetlb 大页；THP `madvise` 路径已实现并测试，但没有稳定收益。
4. GPU 端实验位于同一整合仓库根目录，最终报告为 `docs/FINAL_REPORT.md/.docx`，不混入本 CPU hashjoin 主线报告的详细实验表。

## 14. 总结

本项目已经把平常实验、期中 star join 和期末连接算法研究按发展顺序整合进 hashjoin 开源代码框架。平常实验建立硬件背景，期中实验验证多表连接中的 vector index 优势，期末实验进一步分析 VJ 的 cache/TLB 边界并引入 PRVJ 作为改进方案。

最终交付内容包括代码、统一课程报告、期中报告、期末报告、原始实验 CSV、图表和代码压缩包。整个项目既保留了课程每个阶段的实验结果，也形成了从硬件观察到算法设计再到性能边界分析的完整研究链条。

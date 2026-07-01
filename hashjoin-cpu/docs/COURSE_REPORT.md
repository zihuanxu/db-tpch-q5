# 内存连接算法课程统一报告

## 1. 项目信息

本课程项目以 ETH Zurich VLDB 2013 main-memory hash join 开源代码为基础，将本学期练习、期中报告和期末报告统一整合到同一个 hashjoin 框架中。CPU 端实验通过命令行开关选择不同算法；GPU 端实验可作为独立扩展项目提交。

开源项目链接建议使用个人 fork：

```text
https://github.com/zihuanxu/vldb13-eth-hashjoin
```

当前本地远端仍是原始上游：

```text
https://github.com/mars-research/vldb13-eth-hashjoin.git
```

提交前需要把远端改为个人仓库或 fork，并推送本项目分支。

## 2. 作业要求对照

| 要求 | 完成情况 | 位置 |
|---|---|---|
| 扩展 hashjoin 开源框架 | 已完成 | `src/`、`scripts/` |
| 通过算法开关执行测试 | 已完成 | `-a VJ/PRVJ/sort-merge`、`--starjoin=...` |
| 前期硬件实验 | 已完成 | `docs/data/mlc_*.txt`、`docs/data/numa_comparison.csv` |
| 期中 starjoin 报告 | 已完成 | `docs/MIDTERM_REPORT.md/.docx` |
| 期末 full sweep 报告 | 已完成 | `docs/EXPERIMENT_REPORT.md/.docx` |
| 统一课程报告 | 已完成 | `docs/COURSE_REPORT.md/.docx` |
| 原始数据和图 | 已完成 | `docs/data/`、`docs/assets/` |
| 代码打包 | 已准备 | `deliverables/hashjoin-course-deliverable.tar.gz` |

## 3. 工程扩展

### 3.1 算法入口

新增或扩展的主要命令行入口如下：

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

关键实现文件：

- `src/main.c`
- `src/no_partitioning_join.c`
- `src/vector_join.c`
- `src/vector_join.h`
- `src/parallel_radix_join.c`
- `src/parallel_radix_join.h`
- `src/starjoin.c`
- `src/starjoin.h`

### 3.2 空间统计

NPO 增加 hash table 空间统计：

```text
NPO_STATS_CSV_HEADER,hash_table_bytes,total_extra_space_bytes,primary_bucket_bytes,overflow_bucket_bytes
```

VJ 增加 vector index 空间统计：

```text
VJ_STATS_CSV_HEADER,payload_width_bytes,vector_index_bytes,vector_total_bytes,total_extra_space_bytes
```

PRVJ 和 sort-merge 也接入统一统计输出，方便脚本汇总吞吐和空间开销曲线。

## 4. 实验环境

硬件信息由 `lscpu` 和 `numactl --hardware` 获取。

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

## 5. 前期实验：NUMA 与内存层次

代表性 NUMA 实验使用 `R=S=2^24`、16 线程，对 NPO、PRO、VJ 分别测试默认分配、`--basic-numa` 和 `numactl --interleave=all`。

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

NPO 和 PRO 明显受益于 NUMA 优化；VJ 在该点没有收益，原因是 vector index 较紧凑，内存放置不是主要瓶颈。

## 6. 期中实验：Star Join

### 6.1 数据模型

期中任务要求实现三表 starjoin：

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

PDF 中表达式结果与“结果等于记录数”的文字说明存在矛盾。按表达式和常量 O=3、L=1、PS=1，每行贡献为 3。因此报告使用 `matches` 验证连接基数，用 `aggregate_sum` 验证表达式计算。

### 6.2 `sf=100` 主结果

64 线程、VJ `payload_width=1`，三种模式均输出 `matches=600000000`、`aggregate_sum=1800000000`。

| mode | threads | time ms | aux/input |
|---|---:|---:|---:|
| npo | 64 | 15552.289 | 1.455 |
| pro | 64 | 17355.132 | 2.178 |
| vj | 64 | 3645.527 | 0.069 |

VJ 比 NPO 快约 4.27 倍，比 PRO 快约 4.76 倍。空间上 VJ 辅助空间约为输入的 6.9%，显著低于 NPO 和 PRO。

### 6.3 NUMA、Prefetch 与 PRO 顺序

`--basic-numa` 在 starjoin 上没有带来收益：

| mode | non-NUMA ms | `--basic-numa` ms | 变化 |
|---|---:|---:|---:|
| npo | 15552.289 | 16666.803 | +7.2% |
| pro | 17355.132 | 18066.061 | +4.1% |
| vj | 3645.527 | 4354.036 | +19.4% |

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

## 7. 期末实验：Full Sweep

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

结论是：小 R 下直接 VJ 最快；当 vector working set 超过 L3 后，VJ 性能快速下降；PRVJ 能在大 R 下通过分区显著恢复性能；PRO 在最大规模点表现最稳定。

## 8. VJ 工作集与 TLB 分析

VJ cache-size sweep 固定 `S=2^30`、64 线程、`payload_width=1`，让 vector index 大小覆盖 L1/L2/L3 的 10% 到 150%，并继续扩展到 L3 的 10 倍。

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

## 9. 复现方式

完整自检：

```bash
scripts/self_check_assignment.sh
```

full sweep：

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

starjoin：

```bash
scripts/run_starjoin_comparison.sh --run \
  --sf 100 \
  --threads-list "64" \
  --modes "npo pro vj" \
  --prefetch-distances "0" \
  --payload-width 1 \
  --timeout-seconds 3600
```

## 10. 自检结果

已运行：

```text
[PASS] assignment self-check passed
```

补充检查：

- `git diff --check` 通过。
- full sweep summary 中 7 个变体各 26 个点，bad=0。
- starjoin `sf=100` CSV 所有点 status=OK。
- `docs/EXPERIMENT_REPORT.docx`、`docs/MIDTERM_REPORT.docx`、`docs/COURSE_REPORT.docx` 已生成。

## 11. 局限

1. `R=2^5` 在 64 线程下触发原始生成器的极小 R 边界问题，因此该点用 32 线程补跑。
2. PRO 当前未输出统一 extra-space 字段，空间表只比较已实现统计的算法。
3. 当前环境没有预留 hugetlb 大页；THP `madvise` 路径已实现并测试，但没有稳定收益。
4. GPU 端实验不在本仓库内，若课程最终要求一起提交，应作为单独 GPU 项目链接或附件补充。

## 12. 结论

本项目已经将课程前期实验、期中 starjoin 和期末连接算法研究整合进 hashjoin 开源代码框架。实验结果表明：

- NUMA 对 NPO/PRO 影响显著。
- VJ 在主键连续、vector 工作集可缓存时性能最好。
- 当 vector 工作集超过 L3 后，VJ 性能快速下降。
- PRVJ 通过 radix partition 缩小局部工作集，在大 R 下明显优于直接 VJ。
- starjoin 中 VJ 同时具有最好的性能和空间效率。

因此，最终可交付内容包括代码、统一课程报告、期中/期末报告、原始实验 CSV、图表和代码压缩包。

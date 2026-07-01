# 内存连接算法实验报告

## 1. 实验目标

本实验基于 ETH Zurich VLDB 2013 main-memory hash join 代码，围绕课程要求完成三类工作：

1. 理解双路多核服务器的 cache、NUMA 和内存层次对内存连接算法的影响。
2. 对比 NPO、PRO、sort-merge、VJ、PRVJ 等代表性连接算法的性能和空间开销。
3. 在原始代码上增加 VJ、PRVJ、sort-merge、NPO 空间统计和可复现实验脚本。

代码入口位于：

- `src/main.c`
- `src/no_partitioning_join.c`
- `src/vector_join.c`
- `src/parallel_radix_join.c`
- `scripts/run_extended_algo_comparison.sh`

## 2. 实验环境

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

Intel MLC v3.12 已从 Intel 官方下载页获取并校验 SHA256。由于当前环境不能 sudo，MLC 采用用户态可运行参数 `-e -r`：`-e` 表示不修改硬件 prefetcher 状态，`-r` 表示使用 random access latency 测量。`stream` 命令实际是 ImageMagick 工具，不是 STREAM memory bandwidth benchmark。默认 `/usr/bin/perf` 包装器找不到当前 6.12 自定义内核对应的 linux-tools，但直接调用 `/usr/lib/linux-tools/6.8.0-111-generic/perf` 可以采集用户态硬件事件。

MLC 原始输出保存于：

- `docs/data/mlc_latency_matrix.txt`
- `docs/data/mlc_bandwidth_matrix.txt`
- `docs/data/mlc_idle_latency.txt`
- `docs/data/mlc_loaded_latency.txt`

MLC latency matrix，单位 ns：

| requester NUMA node | memory node 0 | memory node 1 |
|---:|---:|---:|
| 0 | 82.8 | 140.8 |
| 1 | 155.6 | 81.5 |

MLC read-only bandwidth matrix，单位 MB/s：

| requester NUMA node | memory node 0 | memory node 1 |
|---:|---:|---:|
| 0 | 74078.8 | 34280.9 |
| 1 | 34360.1 | 74238.6 |

MLC idle latency 为 80.6 ns。Loaded latency 在最高注入带宽约 144724.6 MB/s 时延迟约 269.82 ns；当带宽降至约 24164.9 MB/s 时延迟约 90.92 ns。这些数据说明跨 NUMA 访问延迟约为本地访问的 1.7 到 1.9 倍，跨 NUMA 读带宽约为本地读带宽的 46%。

## 3. 算法实现

### NPO

NPO 是 no-partition hash join。实验代码新增了空间统计：

```text
NPO_STATS_CSV_HEADER,hash_table_bytes,total_extra_space_bytes,primary_bucket_bytes,overflow_bucket_bytes
```

其中 `hash_table_bytes = primary_bucket_bytes + overflow_bucket_bytes`。多线程 NPO 的 overflow 统计来自每个线程实际分配的 overflow buffer。

### PRO

PRO 保留原始 radix partition hash join 实现。实验中使用 `NUM_PASSES=2`、`NUM_RADIX_BITS=18` 作为代表性配置。

### Sort-Merge

新增内置 `sort-merge` 基线。实现方式是复制 R/S、按 key 排序后 merge 计数。该实现主要作为排序连接基线；在 `S=2^30` 的 full sweep 中也完成了全量测试，但耗时明显高于 hash/vector 类算法。

### VJ

VJ 利用 R 表主键连续整数的特点，将 R payload 放入 vector index，S 表 key 直接作为下标访问。支持：

```bash
--payload-width=1
--payload-width=2
--payload-width=4
--vj-hugepage
```

三种 payload width 模拟 payload 压缩率差异。`--vj-hugepage` 在当前 Linux THP=`madvise` 环境下，对 VJ vector 分配调用 `madvise(MADV_HUGEPAGE)`。VJ 输出：

```text
VJ_STATS_CSV_HEADER,payload_width_bytes,vector_index_bytes,vector_total_bytes,total_extra_space_bytes
```

### PRVJ

PRVJ 在 PRO 的 radix partition 框架后，对每个分区执行 vector lookup。当前代表实验使用 `payload_width=4`、`NUM_RADIX_BITS=18`。

## 4. 正确性验证

已运行：

```bash
scripts/self_check_assignment.sh
```

验证内容包括：

- `make -j4`
- NPO/PRO/sort-merge/VJ/PRVJ 小规模结果数检查
- VJ `--vj-hugepage` 小规模结果数检查
- NPO/VJ/PRVJ/sort-merge 空间统计输出检查
- starjoin npo/pro/vj 在 `sf=1` 下的正确性检查

结果：

```text
[PASS] assignment self-check passed
```

## 5. Full Sweep 综合实验

课程要求的主实验是固定 `|S| = 2^30`，令 `|R|` 从 `2^5` 到 `2^30` 变化。最终实验覆盖以下 7 个算法变体：

- NPO
- PRO
- sort-merge
- VJ-1B、VJ-2B、VJ-4B
- PRVJ-4B

主实验命令如下：

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

sort-merge 因单点耗时较长，单独运行后合并到最终结果：

```bash
scripts/run_extended_algo_comparison.sh --run \
  --threads 64 \
  --repeats 1 \
  --s-size 1073741824 \
  --r-exps "6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30" \
  --algorithms "sort-merge" \
  --timeout-seconds 3600 \
  --out-prefix final-sortmerge-s2e30-r6-r30
```

`R=2^5` 与 64 线程组合会触发原始数据生成器的极小 R 边界问题，因此该点使用 32 线程补跑。除 `R=2^5` 外，所有点均为 64 线程。合并后 7 个算法变体各 26 个点全部为 `OK`。

原始数据保存于：

- `docs/data/final_full_all_s2e30_summary.csv`
- `docs/data/final_full_all_s2e30_raw.csv`

吞吐曲线：

![final full throughput](assets/final-full-all-s2e30_throughput.svg)

空间开销曲线：

![final full space](assets/final-full-all-s2e30_space.svg)

### 吞吐结果

单位：MTuples/s。下表摘取关键 R 点，完整 26 点曲线见 CSV 与图。

| log2(|R|) | NPO | PRO | sort-merge | VJ-1B | VJ-2B | VJ-4B | PRVJ-4B |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 | 6686.06 | 1266.16 | 9.24 | 6944.26 | 5351.32 | 7605.43 | 1165.40 |
| 20 | 3690.51 | 1062.53 | 6.43 | 4535.82 | 4653.81 | 4178.67 | 1075.71 |
| 24 | 784.09 | 1126.27 | 5.86 | 1035.26 | 716.63 | 606.64 | 1077.05 |
| 28 | 373.69 | 772.47 | 4.60 | 89.43 | 73.70 | 71.69 | 842.03 |
| 30 | 136.19 | 516.02 | 2.79 | 23.26 | 20.40 | 19.20 | 476.67 |

### 空间开销结果

空间开销比值以 `(算法额外空间) / ((|R| + |S|) * sizeof(tuple_t))` 计算。PRO 当前未输出统一 extra-space 字段，因此表中留空。

| log2(|R|) | NPO | sort-merge | VJ-1B | VJ-2B | VJ-4B | PRVJ-4B |
|---:|---:|---:|---:|---:|---:|---:|
| 24 | 0.031010 | 1.000000 | 0.003846 | 0.005769 | 0.009615 | 0.009615 |
| 30 | 1.000122 | 1.000000 | 0.125000 | 0.187500 | 0.312500 | 0.312500 |

NPO 的 hash table 在 `R=2^30` 时约 17.18 GB，接近输入数据量，因此空间开销比约为 1。sort-merge 需要复制 R/S 进行排序，额外空间也约为 1 倍输入。VJ/PRVJ 的空间由 vector 宽度线性决定，`R=2^30` 时 VJ-1B、VJ-2B、VJ-4B 分别为 2.15 GB、3.22 GB、5.37 GB。

## 6. NUMA 对比

代表性 NUMA 实验命令：

```bash
./src/mchashjoins -a NPO -n 16 -r 16777216 -s 16777216
./src/mchashjoins -a NPO -n 16 -r 16777216 -s 16777216 --basic-numa
numactl --interleave=all ./src/mchashjoins -a NPO -n 16 -r 16777216 -s 16777216
```

同样方式测试 PRO 和 VJ。原始 CSV 保存为 `docs/data/numa_comparison.csv`。

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

NPO 和 PRO 明显受益于 NUMA 优化，尤其是 NPO 从 478.582 ms 降到 241.643 ms。VJ 在该数据点下没有受益，原因是 VJ 主要访问紧凑 vector index，且实验中的 vector 大约为 32 MiB，接近单 socket L3 容量，内存放置不是主要瓶颈。

## 7. VJ 工作集实验

为分析 VJ 的 cache 拐点，固定 `S=2^30`、64 线程、`payload_width=1`，让 vector index 大小覆盖 L1/L2/L3 的 10% 到 150%，并继续扩展到 L3 的 10 倍。原始 CSV 保存为 `docs/data/vj_cache_sweep_s2e30.csv`。

![VJ cache sweep](assets/vj-cache-sweep-s2e30.svg)

| label | vector bytes | cache ratio | time ms | throughput MT/s |
|---|---:|---:|---:|---:|
| L1_10 | 3,278 | 0.10 | 137.894 | 7786.72 |
| L2_50 | 524,290 | 0.50 | 131.454 | 8168.19 |
| L2_150 | 1,572,866 | 1.50 | 205.453 | 5226.22 |
| L3_10 | 5,190,454 | 0.10 | 286.720 | 3744.91 |
| L3_100 | 51,904,514 | 1.00 | 1861.238 | 576.90 |
| L3_2x | 103,809,026 | 2.00 | 3559.445 | 301.66 |
| L3_10x | 519,045,122 | 10.00 | 12352.788 | 86.92 |

VJ 的简化代价模型可以写为：

```text
T ~= T_build_vector(R, W) + T_probe(S, W)
W ~= max_key(R) * (present_bytes + payload_width)
```

当工作集 `W` 小于 cache/TLB 覆盖能力时，probe 主要是低延迟数组访问；当 `W` 接近或超过 L3 后，probe 退化为高比例随机内存访问，吞吐随 `W` 增大快速下降。实测中 vector 从约 0.5 MiB 到 51.9 MiB 时，吞吐从 8168.19 MT/s 降到 576.90 MT/s；继续扩展到 519 MiB 后降到 86.92 MT/s。

## 8. Perf 与大页状态

使用以下工具补充采集 VJ 的 cache/TLB 事件：

```bash
/usr/lib/linux-tools/6.8.0-111-generic/perf stat \
  -e cache-references,cache-misses,dTLB-loads,dTLB-load-misses \
  ./src/mchashjoins -a VJ --payload-width=<W> -n 16 -r <R> -s 16777216
```

原始 CSV 保存为 `docs/data/perf_vj_cache_tlb.csv`。

| payload width | log2(|R|) | vector bytes | time ms | cache miss rate | dTLB miss rate |
|---:|---:|---:|---:|---:|---:|
| 1 | 20 | 2,097,154 | 24.214 | 46.36% | 4.03% |
| 1 | 24 | 33,554,434 | 466.315 | 52.33% | 7.68% |
| 4 | 24 | 83,886,085 | 595.599 | 63.02% | 8.03% |

结果显示，R 从 `2^20` 增大到 `2^24` 后，VJ-1B 的 dTLB miss rate 从 4.03% 上升到 7.68%，时间从 24.214 ms 增加到 466.315 ms。保持 `R=2^24` 时，将 payload width 从 1B 增加到 4B，vector 从约 32 MiB 增至约 80 MiB，cache miss rate 从 52.33% 增加到 63.02%，时间进一步增加到 595.599 ms。

大页状态保存为 `docs/data/hugepage_status.txt`。当前机器 `HugePages_Total=0`，transparent huge page 为 `madvise` 模式；因此代码新增 `--vj-hugepage`，在 VJ vector 分配后调用 `madvise(MADV_HUGEPAGE)`。实测数据保存为 `docs/data/vj_hugepage_madvise.csv`。

| mode | direct time ms | perf cache miss rate | perf dTLB miss rate |
|---|---:|---:|---:|
| default | 425.170 | 52.32% | 7.57% |
| `--vj-hugepage` | 477.601 | 52.19% | 7.57% |

该点为 `R=S=2^24`、VJ-1B、16 线程。时间列来自直接运行，cache/TLB 比例来自 perf 伴随运行。`madvise` 调用没有报错，但 dTLB miss rate 未改善，说明当前环境下 THP 提示没有稳定转化为 VJ 的可见收益。后续如果有 root 权限或预留 hugetlb 大页，可进一步把 vector allocation 改为显式 hugetlb 分配再复测。

## 9. 结果分析

### 性能趋势

当 R 很小时，VJ 直接下标访问的优势明显，吞吐可达到数千 MTuples/s。随着 R 增大，VJ vector index 逐渐超过 cache 容量，随机访问代价上升，吞吐下降明显。

在 `R=2^24` 时：

- VJ-1B vector size 约 32 MiB，低于单 socket L3 49.5 MiB，吞吐 1035.26 MTuples/s。
- VJ-2B vector size 约 48 MiB，接近单 socket L3，吞吐 716.63 MTuples/s。
- VJ-4B vector size 约 80 MiB，超过单 socket L3，吞吐 606.64 MTuples/s。

在 `R=2^30` 时，三种 VJ vector 都达到 GB 级，VJ-4B 降到 19.20 MTuples/s。PRVJ-4B 通过 radix partition 限制每个分区的 vector working set，在 `R=2^30` 达到 476.67 MTuples/s，明显优于直接 VJ-4B。

### 算法取舍

NPO 在中小 R 下很快，但 hash table 空间随 R 增长明显，`R=2^30` 时吞吐降到 136.19 MTuples/s。PRO 在大 R 下更稳定，`R=2^30` 仍有 516.02 MTuples/s，是最终点中最快的通用算法。sort-merge 作为排序连接基线，空间开销约 1 倍输入，且 `S=2^30` 下排序成本极高，`R=2^30` 只有 2.79 MTuples/s。

## 10. 局限

1. `R=2^5` 在 64 线程下触发原始生成器的极小 R 边界问题，因此该点用 32 线程补跑；其余 full sweep 点均为 64 线程。
2. PRO 当前未输出统一 extra-space 字段，报告只比较已实现统计的算法空间开销。
3. 当前环境没有预留 hugetlb 大页；已完成 THP `madvise` 路径实测，但未观察到稳定 TLB 收益。

## 11. 结论

本实验完成了课程要求中的主要工程扩展和全量对比：

- NPO/PRO/hash join 基线可运行。
- VJ 和 PRVJ 已实现并支持 payload width 参数。
- sort-merge 已接入统一实验脚本，并完成 `S=2^30` full sweep。
- NPO、VJ、PRVJ、sort-merge 可输出空间统计。
- MLC、NUMA、perf cache/TLB、VJ cache-size sweep 和 THP `madvise` 大页尝试均已形成数据文件。
- `|R|=2^5..2^30`、`|S|=2^30` 的全量曲线已经生成。

综合结果是：小 R 下直接 VJ 最快；当 vector working set 超过 L3 后，VJ 性能快速下降；PRVJ 能在大 R 下通过分区显著恢复性能；PRO 在最大规模点表现最稳定。

# 内存连接算法探索：最终交付说明

本文档对应 `/home/xuzihuan/内存连接算法探索.pdf` 的作业要求，汇总当前
`vldb13-eth-hashjoin` 的工程实现、最终实验数据和报告文件。

## 1. 总体结论

当前项目已经形成可交付版本：

- 保留 ETH Zurich VLDB 2013 代码中的 NPO、PRO 等 hash join 基线。
- 新增 VJ，并支持 `--payload-width=1|2|4`。
- 新增 `--vj-hugepage`，在 THP=`madvise` 环境下对 VJ vector 分配调用 `MADV_HUGEPAGE`。
- 新增 PRVJ，即 radix partition 后在每个分区内执行 vector lookup。
- 新增内置 `sort-merge` 基线。
- 新增 NPO、VJ、PRVJ、sort-merge 的空间统计输出。
- 新增 `--starjoin=npo|pro|vj --sf=<N>` 多表连接入口。
- 完成 `|S|=2^30`、`|R|=2^5..2^30` 的 full sweep。
- 完成 `sf=100` starjoin 主实验、NUMA 对比、prefetch 对比和 PRO 顺序对比。
- 完成 MLC、NUMA、perf cache/TLB、VJ cache-size sweep 和 THP `madvise` 大页尝试记录。

主要报告文件：

- `docs/COURSE_REPORT.md`
- `docs/COURSE_REPORT.docx`
- `docs/EXPERIMENT_REPORT.md`
- `docs/EXPERIMENT_REPORT.docx`
- `docs/MIDTERM_REPORT.md`
- `docs/MIDTERM_REPORT.docx`

## 2. 作业要求对照

### 硬件参数测试

已完成：

- `lscpu` / `numactl --hardware` 记录 CPU、socket、core、cache、NUMA distance。
- Intel MLC v3.12 测量 NUMA latency matrix、bandwidth matrix、idle latency、loaded latency。
- perf 采集 VJ cache miss 和 dTLB miss。
- 记录 hugetlb/THP 状态。

数据文件：

- `docs/data/mlc_latency_matrix.txt`
- `docs/data/mlc_bandwidth_matrix.txt`
- `docs/data/mlc_idle_latency.txt`
- `docs/data/mlc_loaded_latency.txt`
- `docs/data/perf_vj_cache_tlb.csv`
- `docs/data/hugepage_status.txt`

### 算法实现

已完成：

- NPO 内存统计：primary bucket、overflow bucket/buffer、total extra space。
- VJ：连续 key vector index，payload width 1/2/4。
- PRVJ：radix partition + per-partition vector lookup。
- sort-merge：复制 R/S 后排序并 merge count。
- starjoin：npo/vj pipeline，pro materialized。

关键文件：

- `src/main.c`
- `src/no_partitioning_join.c`
- `src/vector_join.c`
- `src/vector_join.h`
- `src/parallel_radix_join.c`
- `src/starjoin.c`
- `src/starjoin.h`

### Full Sweep

已完成 `|S|=2^30`、`|R|=2^5..2^30`，算法包含：

- NPO
- PRO
- sort-merge
- VJ-1B、VJ-2B、VJ-4B
- PRVJ-4B

合并后 7 个变体各 26 个点全部为 `OK`。`R=2^5` 在 64 线程下触发原始数据生成器的极小 R 边界问题，因此该点用 32 线程补跑；其余 full sweep 点均为 64 线程。

数据和图：

- `docs/data/final_full_all_s2e30_summary.csv`
- `docs/data/final_full_all_s2e30_raw.csv`
- `docs/assets/final-full-all-s2e30_throughput.svg`
- `docs/assets/final-full-all-s2e30_space.svg`

### VJ 分析

已完成：

- VJ payload width 1/2/4 对比。
- VJ cache-size sweep：L1/L2/L3 的 10% 到 150%，以及 L3 的 2x/4x/8x/10x。
- perf cache/TLB 代表性计数。
- `--vj-hugepage` THP `madvise` 实测。
- 报告中给出简化工作集代价模型。

数据和图：

- `docs/data/vj_cache_sweep_s2e30.csv`
- `docs/assets/vj-cache-sweep-s2e30.svg`
- `docs/data/vj_hugepage_madvise.csv`

### Star Join

已完成作业要求的 `sf=100` 规模：

```bash
./src/mchashjoins --starjoin=npo --sf=100 -n 64
./src/mchashjoins --starjoin=pro --sf=100 -n 64
./src/mchashjoins --starjoin=vj  --sf=100 -n 64 --payload-width=1
```

正式结果：

- npo: 15552.289 ms
- pro: 17355.132 ms
- vj: 3645.527 ms

三种模式均输出：

- `matches = 600000000`
- `aggregate_sum = 1800000000`

PDF 中表达式结果与“结果等于记录数”的文字说明存在矛盾；当前实现按 SQL 表达式计算聚合值，每条匹配贡献为 3，因此报告同时使用 `matches` 验证连接基数，并使用 `aggregate_sum` 验证表达式计算。

数据文件：

- `docs/data/starjoin_sf100_t64.csv`
- `docs/data/starjoin_sf100_t64_basicnuma.csv`
- `docs/data/starjoin_sf100_prefetch_t64.csv`
- `docs/data/starjoin_sf100_pro_order.csv`

## 3. 自检

已运行：

```bash
scripts/self_check_assignment.sh
```

结果：

```text
[PASS] assignment self-check passed
```

补充校验：

- `bash -n scripts/run_extended_algo_comparison.sh scripts/run_starjoin_comparison.sh`
- `git diff --check`
- VJ `--vj-hugepage` 小规模 correctness
- full sweep summary: 7 个变体各 26 个点，bad=0
- starjoin `sf=100` CSV: 所有点 status=OK

## 4. 环境限制

当前环境 `HugePages_Total=0`，没有预留 hugetlb 大页；透明大页为 `madvise` 模式。因此本项目实现并测试了 `--vj-hugepage` 的 `MADV_HUGEPAGE` 路径，但未观察到稳定 TLB 收益。若后续能获得 root 权限或预留 hugetlb 大页，可进一步做显式 hugetlb 分配复测。

当前系统中的 `stream` 命令是 ImageMagick 工具，不是 STREAM benchmark；内存带宽结论采用 Intel MLC 的 bandwidth matrix。

## 5. 交付口径

代码、实验脚本、CSV、SVG 图和 docx 报告已经在项目内整理完毕。正式提交时建议以 `docs/COURSE_REPORT.docx` 作为统一课程报告，以 `docs/EXPERIMENT_REPORT.docx` 作为前期/扩展算法细节报告，以 `docs/MIDTERM_REPORT.docx` 作为期中 Star Join 细节报告，同时保留 `docs/data/` 和 `docs/assets/` 作为可复核材料。期末 TPC-H Q5/GPU 报告位于仓库根目录 `docs/FINAL_REPORT.docx`。

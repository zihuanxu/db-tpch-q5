# 期中课程报告：Star Join 多表连接实现与实验

## 1. 实验目标

期中任务要求在原始 main-memory join 框架中增加 starjoin 算法，支持：

```bash
./src/mchashjoins --starjoin=npo --sf=100 -n <threads>
./src/mchashjoins --starjoin=pro --sf=100 -n <threads>
./src/mchashjoins --starjoin=vj  --sf=100 -n <threads>
```

并比较三种执行方式：

- `npo`: pipeline hash probe，不物化中间结果。
- `vj`: pipeline vector lookup，不物化中间结果。
- `pro`: 物化中间表，再执行第二次连接。

## 2. 数据模型

按照作业要求生成三张表：

| 表 | 结构 | 规模 |
|---|---|---:|
| L | `<FKO, FKPS, PAYLOAD>` | `sf * 6000000` |
| O | `<PK, PAYLOAD>` | `sf * 1500000` |
| PS | `<PK, PAYLOAD>` | `sf * 800000` |

当前代码中 tuple 结构只有 `key` 和 `payload` 两列，因此 L 表用：

- `tuple.key` 表示 `FKO`
- `tuple.payload` 表示 `FKPS`

payload 常量按作业要求设置：

- `O.PAYLOAD = 3`
- `L.PAYLOAD = 1`
- `PS.PAYLOAD = 1`

查询为：

```sql
select sum(O.PAYLOAD - (L.PAYLOAD - PS.PAYLOAD))
from L, O, PS
where L.FKO = O.PK
  and L.FKPS = PS.PK;
```

注意：PDF 中同时写到“计算结果与记录数量相同”。但按上述表达式和常量，每行贡献为 `3 - (1 - 1) = 3`，因此当前程序输出：

- `matches = sf * 6000000`
- `aggregate_sum = matches * 3`

报告使用 `matches` 验证连接完整性，同时保留 `aggregate_sum` 验证表达式计算。

## 3. 实现说明

实现文件：

- `src/starjoin.h`
- `src/starjoin.c`
- `src/main.c`

### npo 模式

npo 模式先为 O 和 PS 构造 hash table，然后扫描 L 表。每个 L tuple 同时 probe O 和 PS，payload 计算只保存在寄存器变量中，不物化中间结果。

### vj 模式

vj 模式利用 O.PK 和 PS.PK 都是连续整数的特点，为 O 和 PS 构造 vector index。扫描 L 时直接用 FKO/FKPS 作为下标访问 vector。`--payload-width=1|2|4` 控制 vector payload 宽度。

### pro 模式

pro 模式采用物化处理：

1. L 与 PS 连接，生成临时表 `temp<FKO,TEMPPAYLOAD>`。
2. 临时表再与 O 连接，完成最终聚合。

代码支持 `--starjoin-pro-order=0|1`，默认先执行 L-PS。

## 4. 正确性验证

使用：

```bash
scripts/self_check_assignment.sh
```

其中包含：

```bash
./src/mchashjoins --starjoin=npo --sf=1 -n 2
./src/mchashjoins --starjoin=pro --sf=1 -n 2
./src/mchashjoins --starjoin=vj  --sf=1 -n 2 --payload-width=1
```

自检结果：

```text
[PASS] assignment self-check passed
```

所有 starjoin 模式在 `sf=1` 下均输出：

- `matches = 6000000`
- `aggregate_sum = 18000000`

## 5. 实验配置

本报告以作业要求的 `sf=100` 作为正式实验规模，64 线程运行三种 starjoin 模式：

```bash
scripts/run_starjoin_comparison.sh --run \
  --sf 100 \
  --threads-list "64" \
  --modes "npo pro vj" \
  --prefetch-distances "0" \
  --payload-width 1 \
  --out starjoin-sf100-t64.csv \
  --log-dir starjoin-sf100-t64.logs \
  --timeout-seconds 3600
```

NUMA 对比命令：

```bash
scripts/run_starjoin_comparison.sh --run \
  --sf 100 \
  --threads-list "64" \
  --modes "npo pro vj" \
  --prefetch-distances "0" \
  --payload-width 1 \
  --basic-numa \
  --out starjoin-sf100-t64-basicnuma.csv \
  --log-dir starjoin-sf100-t64-basicnuma.logs \
  --timeout-seconds 3600
```

prefetch 对比命令：

```bash
scripts/run_starjoin_comparison.sh --run \
  --sf 100 \
  --threads-list "64" \
  --modes "npo vj" \
  --prefetch-distances "0 8 32" \
  --payload-width 1 \
  --out starjoin-sf100-prefetch-t64.csv \
  --log-dir starjoin-sf100-prefetch-t64.logs \
  --timeout-seconds 3600
```

所有 `sf=100` 实验点均为 `OK`。原始 CSV 保存为：

- `docs/data/starjoin_sf100_t64.csv`
- `docs/data/starjoin_sf100_t64_basicnuma.csv`
- `docs/data/starjoin_sf100_prefetch_t64.csv`
- `docs/data/starjoin_sf100_pro_order.csv`

## 6. `sf=100` 性能结果

单位：ms。VJ 使用 `payload_width=1`。三种模式均输出 `matches=600000000`、`aggregate_sum=1800000000`。

| mode | threads | time ms | matches | aggregate_sum |
|---|---:|---:|---:|---:|
| npo | 64 | 15552.289 | 600000000 | 1800000000 |
| pro | 64 | 17355.132 | 600000000 | 1800000000 |
| vj | 64 | 3645.527 | 600000000 | 1800000000 |

VJ 比 NPO 快约 4.27 倍，比 PRO 快约 4.76 倍。原因是 O/PS 维表主键连续，VJ 把 hash probe 变成数组下标访问，避免了链式 hash table 访问和物化中间结果。

## 7. 空间效率

`sf=100` 时输入数据大小为 6,640,000,000 bytes。三种模式在 64 线程下的空间统计：

| mode | input bytes | aux bytes | total bytes | aux/input |
|---|---:|---:|---:|---:|
| npo | 6,640,000,000 | 9,663,676,416 | 16,303,676,416 | 1.455 |
| pro | 6,640,000,000 | 14,463,676,416 | 21,103,676,416 | 2.178 |
| vj | 6,640,000,000 | 460,000,004 | 7,100,000,004 | 0.069 |

vj 的空间效率最高，原因是 O/PS 的主键连续，vector index 只需要 present bitmap 和压缩 payload。npo 需要两个 hash table。pro 在此基础上还需要物化中间表，因此空间开销最高。

## 8. NUMA 与 Prefetch

`--basic-numa` 下的 `sf=100` 结果：

| mode | non-NUMA ms | `--basic-numa` ms | 变化 |
|---|---:|---:|---:|
| npo | 15552.289 | 16666.803 | +7.2% |
| pro | 17355.132 | 18066.061 | +4.1% |
| vj | 3645.527 | 4354.036 | +19.4% |

在 starjoin 这个任务上，`--basic-numa` 没有带来收益。更可能的原因是维表结构在 probe 阶段被所有线程共享访问，单纯按线程本地分配并不能改善共享维表访问，反而可能增加跨 socket 访问或初始化成本。

prefetch distance 对比：

| prefetch distance | npo ms | vj ms |
|---:|---:|---:|
| 0 | 15623.447 | 3729.132 |
| 8 | 15206.068 | 3602.398 |
| 32 | 15187.158 | 3441.220 |

npo 在 distance 32 时比 distance 0 快约 2.8%。vj 在 distance 32 时比 distance 0 快约 7.7%。说明 starjoin 的 L 表扫描和维表 probe 仍能从适度预取中受益，但收益没有改变 VJ 明显领先的总体结论。

## 9. PRO 物化顺序

代码支持 `--starjoin-pro-order=0|1`，用于比较两种物化顺序。`sf=100`、64 线程结果如下：

| pro order | time ms | matches | aggregate_sum |
|---:|---:|---:|---:|
| 0 | 17205.709 | 600000000 | 1800000000 |
| 1 | 17613.673 | 600000000 | 1800000000 |

两个顺序都正确，order 0 略快约 2.3%。由于当前合成数据中每条 L 记录都能匹配 O 和 PS，两个顺序的中间结果规模都接近 L 表规模，因此差距不大。

## 10. 分析

### vj 为什么最快

starjoin 的 O 和 PS 都是连续主键维表，完全符合 vector index 假设。vj 将 hash probe 变成数组下标访问，减少了 hash 计算、链式访问和冲突处理。`sf=100` 下，O/PS vector index 辅助空间约 460 MB，远小于 NPO/PRO 的 hash table 与物化开销。

### pro 为什么空间最高

pro 采用物化策略。虽然分区可以改善局部性，但当前 starjoin 任务中 L 与 PS/O 的连接结果规模等于 L 表规模，即 6 亿行，物化中间表会显著增加内存占用。因此 pro 的 `aux/input` 为 2.178，高于 npo 和 vj。

### 正确性口径

PDF 中表达式结果与“结果等于记录数”的文字说明存在矛盾。本实现按 SQL 表达式计算聚合值，每条匹配贡献为 3，因此 `aggregate_sum = matches * 3`。报告用 `matches` 验证连接基数，用 `aggregate_sum` 验证表达式计算。

## 11. 局限

1. 本报告完成了 `sf=100`、64 线程主实验、NUMA 对比、prefetch 对比和 PRO 顺序对比；没有再额外做完整线程数扩展曲线。
2. 目前未加入 SIMD vectorization，只实现 vector index 数据结构和 lookup 模式。
3. `--basic-numa` 在本任务上没有收益，后续若继续优化，应按 socket 拆分 L 表并复制或分区维表结构，而不是只调整初始化分配。

## 12. 结论

本期中实验完成了 starjoin 的三种执行模式：

- npo pipeline hash starjoin
- pro materialized starjoin
- vj pipeline vector starjoin

在作业要求的 `sf=100` 规模下，VJ 是性能和空间上最优的方案：64 线程耗时 3645.527 ms，明显快于 NPO 的 15552.289 ms 和 PRO 的 17355.132 ms；辅助空间约为输入的 6.9%，明显低于 NPO 的 145.5% 和 PRO 的 217.8%。

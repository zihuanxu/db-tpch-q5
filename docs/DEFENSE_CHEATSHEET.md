# 答辩速查表

> V6 正式口径。讲稿见 `docs/defense/`，证据见 `docs/artifacts/v5_sf1`。

## 30 秒版本

我实现了一个固定 TPC-H Q5 Arrow 查询执行器，比较 specialized CPU、Arrow
Acero、CUDA copy/managed/mapped、cuDF 和 batch 级 CPU--GPU hybrid。所有
19 组 SF1 配置、190 次正式运行都通过官方 oracle。16 线程 specialized CPU
查询中位数 61.414 ms 最快；CUDA 中 copy 最好；hybrid 正确但没有超过纯 CPU。
结论只覆盖固定 Q5、SF1、RTX 4090 和 cold process。

## 必画数据流

```text
Arrow six tables
   -> region/nation filter
   -> supplier_nation[] + customer_nation[] + order_nation[]
   -> lineitem scan
   -> exact revenue_1e4 by nation
   -> sort + result hash + official oracle
```

hybrid 在 `lineitem scan` 前按 Arrow batch 切成不重叠 CPU/GPU 行区间。

## 必记数字

| 项 | V5 正式值 |
| --- | ---: |
| lineitem rows | 6,001,215 |
| result rows | 5 |
| result hash | `542abf4003633c7c` |
| 配置/预热/测量 | 19 / 57 / 190 |
| specialized CPU 16t query | 61.414 ms |
| Acero 32t query | 321.535 ms |
| cuDF query / process | 116.427 / 3682.381 ms |
| gpu-copy query | 314.151 ms |
| gpu-managed query | 358.158 ms |
| gpu-mapped query | 412.264 ms |
| hybrid 25/50/75% CPU | 295.435 / 254.478 / 222.832 ms |

## 三种 GPU 模式

| 模式 | 数据位置与移动 |
| --- | --- |
| copy | host staging 后显式 H2D 到 device memory |
| managed | unified allocation，runtime 管理迁移，本项目先 prefetch |
| mapped | pinned host pages 映射进 UVA，GPU 经 PCIe 远程读 |

不能说 mapped “没有传输”。它只是没有大块显式输入 H2D，Arrow 列仍要准备到
pinned buffer，kernel load 仍可能产生 PCIe 事务。

## 两种时间

- `query_total_ms`：后端内部查询阶段。
- `process_elapsed_ms`：外部冷进程时间，包含启动、加载和退出。
- cuDF 另有 `load_ms` 记录 Arrow 读取与 DataFrame 转换。

不能拿一个后端的 query 和另一个后端的 process 比排名。

## 五个危险问题

### 为什么客户和供应商必须同国家

Q5 要统计目标地区内每个国家内部客户与供应商产生的收入。只要求两者都在 ASIA
还会包含跨国供应，国家归属就不符合查询语义。

### managed 是什么

一块统一虚拟地址的 CUDA allocation，页面位置由 runtime 管理，可按需迁移或
prefetch。不是“永远在 CPU 留一份副本，GPU 每用一次就整块复制”。

### 为什么 GPU 和 hybrid 没有加速

每个 cold sample 都重新准备计划、分配和 staging；hybrid 两侧还有重复准备。
SF1 没有摊薄这些固定成本。这个解释与实现一致，但没有 profiler 就不能说每个
原因的占比已经被证明。

### hash 一样是否足够

不够。hash 证明后端精确结果一致；`raw.csv.oracle_status` 和
`correctness.json` 还证明五行结果通过官方 q5.out 十进制比较。

### overlap 是否证明 kernel overlap

不证明。当前只有 backend duration overlap。CPU scan 与 CUDA kernel 的精确
重叠需要 Nsight timeline。

## 必须主动承认的限制

- 固定 Q5，不是 DBMS；
- GPU 只扫描 lineitem；
- 正式结果只有 SF1 cold process；
- 没有 resident、SF10、并发、GPU 峰值显存和 Nsight timeline；
- specialized 牺牲通用性，不能据此否定 Acero；
- hybrid speedup 假设在当前实验中被拒绝。

## AI 参与的诚实表述

> 代码初稿主要由 AI 辅助生成。我接手后按源码、测试和实验日志重新核对，修复了
> decimal 语义，补齐 Arrow CPU/CUDA/hybrid、正式证据审计和论文数值导入。现在
> 我能沿函数解释数据流，也能说明哪些结论没有证据。

## 上台前自检

- [ ] 能从六表条件讲到三个 nation 映射和 lineitem scan。
- [ ] 能区分 Arrow、UVA、managed 和 mapped。
- [ ] 能解释 query time 与 cold process time。
- [ ] 能说出 copy/managed/mapped 和 hybrid 的正式结果顺序。
- [ ] 能说明为什么负结果仍然有效。
- [ ] 能列出至少四项未完成范围。

# Current Status

最后核对：2026-07-14。当前对外口径以 V7 源码、
`docs/research/CLAIM_LEDGER.md`、两组 resident evidence 和 compact profiler
为准。V5 SF1 是 cold-process 历史对照，不能与 V7 request 直接混排。

## 已完成实现

- canonical Arrow IPC 六表数据、manifest、C++ loader 和官方 Q5 oracle；
- Arrow specialized CPU、Arrow Acero 和 cuDF 算子基线；
- Arrow `gpu-copy`、`gpu-managed`、`gpu-mapped` 三种 CUDA 模式；
- resident session：一次 setup 后重复执行请求，并记录 1/10/100 次摊销值；
- fixed ratio 与 hybrid-auto CPU--GPU 数据分片；
- 可选 NVTX、Nsight Systems/Compute 采集和严格证据审计；
- SF1/SF10 正式矩阵、模型评估、论文生成、学习和发布材料。

## 正式证据

| 项目 | SF1 resident | SF10 resident |
| --- | ---: | ---: |
| 配置数 | 18 | 18 |
| warmups / measured | 54 / 180 | 54 / 180 |
| 结果 hash | `542abf4003633c7c` | `b1351a421ba8dcfd` |
| specialized CPU p50 | 3.201 ms (16t) | 14.955 ms (32t) |
| copy / managed / mapped p50 | 1.267 / 1.440 / 22.971 ms | 15.416 / 15.066 / 358.582 ms |
| best fixed hybrid | 1.160 ms，CPU=0.125 | 10.054 ms，CPU=0.375 |
| hybrid-auto | 1.553 ms，regret 33.89% | 10.980 ms，regret 9.21% |
| manifest | `81c48484b397...` | `2a9e07aaaeb8...` |

所有正式请求都通过 oracle。auto 能随规模改变比例，但没有达到离线 fixed
扫描的最佳点；这是保留在论文中的负结果。setup 仍然很重，例如 SF10 最佳
fixed hybrid 的 setup 约 4997 ms，所以少量请求时不一定比 CPU 划算。

## Profiler 状态

已完成 SF1/SF10 × copy/managed/mapped/hybrid-fixed(0.5)/hybrid-auto 共 10 组
采集。完整 bundle 位于本机
`/tmp/memq5-v7-profiler-80dba7a-rerun2`，包含数据硬链接和完整指标全集，严格
审计结果为 `ok=true`。仓库中的 `docs/artifacts/v7_profiler` 是轻量发布副本，
保留命令、工具版本、等长脱敏后的 NSYS report、CSV、所选 NCU 指标和来源哈希。
完整 bundle 可能包含采集进程继承的环境变量，只能保存在本机，不能直接公开。

## 当前验证命令

```bash
python3 scripts/v7_evidence_bundle.py audit --directory docs/artifacts/v7_sf1_resident
python3 scripts/v7_evidence_bundle.py audit --directory docs/artifacts/v7_sf10_resident
python3 scripts/validate_claim_ledger.py docs/research/CLAIM_LEDGER.md
python3 scripts/import_paper_evidence.py \
  --sf1 docs/artifacts/v7_sf1_resident \
  --sf10 docs/artifacts/v7_sf10_resident \
  --model docs/artifacts/v7_hybrid_model/memq5-v7-hybrid-model.json \
  --ledger docs/research/CLAIM_LEDGER.md \
  --output docs/paper/generated
bash docs/paper/build.sh
python3 scripts/check_paper.py
```

## 已知限制

- 固定 Q5，不是通用 SQL/事务数据库；
- GPU 只执行 lineitem 扫描和聚合，过滤传播索引仍由 CPU 准备；
- 只有 SF1、SF10 和一台 RTX 4090，没有并发查询或多硬件结论；
- setup 每组只建立一次，摊销值对一次性抖动较敏感；
- profiler 会扰动统一内存行为，不进入普通延迟排名；
- hybrid-auto 特征较少，SF1 regret 明显偏高。

## 外部待办

- 把 `docs/process/` 同步到腾讯共享文档并给老师开权限；
- 确认 GitHub 仓库可见性并创建最终 release。

这两项依赖用户账号操作，仓库内保持 `EXTERNAL_ACTION_REQUIRED`。

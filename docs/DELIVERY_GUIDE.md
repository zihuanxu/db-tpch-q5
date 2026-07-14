# V7 交付与复现入口

本项目固定实现 TPC-H Q5，在 Apache Arrow 输入上比较专用 CPU、Acero、
copy/managed/mapped CUDA、cuDF 和 CPU--GPU hybrid。正式结论覆盖同一台
RTX 4090 上的 SF1/SF10 resident session；它不是通用数据库。

## 建议阅读顺序

1. `docs/paper/paper.pdf`：计算机学报模板课程论文；
2. `docs/CURRENT_STATUS.md`：当前实现、数字和限制；
3. `docs/artifacts/v7_sf1_resident/` 与 `v7_sf10_resident/`：正式证据；
4. `docs/artifacts/v7_profiler/`：10 组 profiler 轻量证据；
5. `docs/research/CLAIM_LEDGER.md`：成立、被拒绝和受限的结论；
6. `docs/learning/README.md`、`docs/defense/README.md`：接手和答辩；
7. `docs/process/README.md`：可同步到腾讯文档的过程记录。

## 最快验收

```bash
python3 scripts/v7_evidence_bundle.py audit --directory docs/artifacts/v7_sf1_resident
python3 scripts/v7_evidence_bundle.py audit --directory docs/artifacts/v7_sf10_resident
sha256sum -c docs/artifacts/v7_profiler/checksums.sha256
python3 scripts/validate_claim_ledger.py docs/research/CLAIM_LEDGER.md
bash docs/paper/build.sh
python3 scripts/release_audit.py --json
```

完整 CPU/GPU 重建与数据生成见 `README.md` 和 `docs/GPU_SERVER_RUNBOOK.md`。
TPC-H `.tbl` 和 Arrow SF1/SF10 数据不随提交包分发。

## 课程要求对应

| 课程要求 | 本项目实现 | 状态 |
| --- | --- | --- |
| TPC-H Q5 CPU--GPU 查询 | 固定六表过滤传播，CPU/GPU 分片扫描 lineitem | 已实现 |
| CPU 端 Apache Arrow | Arrow IPC、manifest、specialized 与 Acero | 已实现 |
| PCIe 显式传输 | `gpu-copy` 在 setup 显式 H2D | 已实现 |
| UVA/统一地址 | `gpu-mapped` 远程访问、`gpu-managed` 迁移/预取 | 已实现并 profile |
| GPU 算子库 | cuDF merge/filter/groupby/sort | 已实现 |
| CPU--GPU 协同 | fixed 比例和 hybrid-auto，按 batch 无重叠切分 | 已实现 |
| 可共享、可重现、可验证 | 源码、生成器、tiny、SF1/SF10 bundle、审计器 | 已完成 |
| 过程文档 | 七份仓库记录；腾讯共享需用户授权 | 外部操作待完成 |
| 课程论文 | CjC LaTeX/PDF，数字由审计证据生成 | 已完成 |

## 核心结果

- SF1/SF10 各 18 配置、54 warmups、180 measured，全部通过 oracle；
- copy/managed/mapped p50：SF1 为 1.267/1.440/22.971 ms，SF10 为
  15.416/15.066/358.582 ms；
- fixed hybrid 最佳 p50：SF1 1.160 ms（CPU=0.125），SF10 10.054 ms
  （CPU=0.375）；
- auto p50 为 1.553/10.980 ms，相对最佳 fixed 的 regret 为 33.89%/9.21%；
- setup 很重，少量请求时专用 CPU 仍可能更合适；
- mapped 无显式输入 H2D，但 profiler 证明核函数仍经 PCIe 读取主机页。

## 打包

```bash
python3 scripts/package_submission.py \
  --output dist/memory-db-tpch-q5-v7.tar.gz
sha256sum -c dist/memory-db-tpch-q5-v7.tar.gz.sha256
```

包中保留 compact profiler，不包含完整数据硬链接、TPC-H 工具、构建目录或
Nsight 支持指标全集。

## 已知限制

- 固定 Q5、两个规模、一台 RTX 4090，没有多查询并发和 NUMA 对照；
- GPU 只扫描聚合 lineitem，维表索引仍由 CPU 建立；
- Arrow 是共同输入，不代表后端内部准备完全相同；
- setup 每配置只建立一次，摊销估计对一次性抖动敏感；
- auto 模型较简单，SF1 regret 明显偏高。

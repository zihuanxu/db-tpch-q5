# 实验证据目录

最后更新：2026-07-14。

## V7 正式证据

论文的常驻请求结论使用以下目录：

- `v7_sf1_resident/`：SF1，18 配置，54 warmups，180 measured；
- `v7_sf10_resident/`：SF10，18 配置，54 warmups，180 measured；
- `v7_hybrid_model/`：fixed 比例曲线、auto 选择和 regret；
- `v7_profiler/`：10 组 Nsight Systems/Compute 的 compact publication copy。

两组 resident bundle 都包含 matrix、raw/warmup/setup CSV、环境、命令、oracle、
correctness、summary、日志和 manifest。审计命令：

```bash
python3 scripts/v7_evidence_bundle.py audit --directory docs/artifacts/v7_sf1_resident
python3 scripts/v7_evidence_bundle.py audit --directory docs/artifacts/v7_sf10_resident
sha256sum -c docs/artifacts/v7_profiler/checksums.sha256
```

SF1/SF10 的唯一结果 hash 分别为 `542abf4003633c7c` 和
`b1351a421ba8dcfd`。正式证据对应同一提交、GPU UUID 和软件环境。

## Profiler 完整与轻量副本

完整 profiler bundle 需要数据集硬链接和 Nsight 支持指标全集，本机目录约含
2.7GB 数据引用，完整 manifest 也因指标全集较大，不适合直接提交。运行时完整
bundle 为 `/tmp/memq5-v7-profiler-80dba7a-rerun2`，已通过：

```bash
python3 scripts/v7_profiler_bundle.py audit \
  --directory /tmp/memq5-v7-profiler-80dba7a-rerun2
```

`v7_profiler/` 保留来源 manifest SHA256、10 组身份与命令、NSYS 等长脱敏
report、四类 CSV、NCU report、所选指标、工具版本、解析结果和逐文件 checksum。
脱敏次数写入各 profile 的 summary；发布审计还会再次扫描密钥形态字符串。它
省略可重建的数据、SQLite sidecar、巨大 `supported_metrics.txt` 和 collector
metadata 指标全集；因此它用于课程发布和阅读，不冒充完整 bundle 审计输入。

## 正式环境

- CPU：2 x AMD EPYC 9654；GPU：NVIDIA GeForce RTX 4090；
- driver 595.71.05；Arrow/PyArrow 23.0.1；cuDF 26.06.00；
- 数据：TPC-H V3.0.1 dbgen SF1/SF10，Arrow IPC 六表输入。

## 历史证据

`v5_sf1/` 是 SF1 cold-process 正式历史对照，`mvp_sf1/` 是更早的最小实验。
V5 的 `query_total_ms`、冷进程墙钟、V7 的 setup 和 V7 resident request 属于
不同计时口径，不能直接混成一个排名。TPC-H `.tbl`、Arrow 大数据和 TPC-H
工具不随仓库发布，可按 README 和 GPU runbook 重新生成。

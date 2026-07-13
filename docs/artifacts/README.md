# 实验证据目录

最后更新：2026-07-14。

## 正式证据

论文和 V6 结论只使用 `docs/artifacts/v5_sf1/`。该目录是自包含 evidence
bundle，包含：

- `matrix.yml`：冻结的 19 个配置；
- `raw.csv` / `warmups.csv`：190 次测量和 57 次预热；
- `environment.json` / `commands.txt`：环境与实际命令；
- `correctness.json`：hash 和官方 q5.out 正确性门禁；
- `summary.csv` / `summary.json`：由 raw records 重算的统计；
- `logs/`：每个子进程的 stdout/stderr；
- `manifest.json` / `manifest.sha256`：文件 checksum、协议和 Git 状态。

审计命令：

```bash
python3 scripts/evidence_bundle.py audit --directory docs/artifacts/v5_sf1
```

通过标准是 190 个 measured、57 个 warmup、无 checksum/matrix/coverage/summary
错误，且所有正式记录 hash 为 `542abf4003633c7c`。冻结 manifest SHA-256 为
`e3337842d367b541b10f3ecb425d1ba0c7a0378555a87f6c42669ed831b5e660`。

## 正式环境

- CPU：2 x AMD EPYC 9654；GPU：NVIDIA GeForce RTX 4090。
- GPU compute capability 8.9；driver 595.71.05；nvcc 12.6。
- Arrow/PyArrow 23.0.1；Python 3.11.15；cuDF 26.06.00。
- 数据：TPC-H V3.0.1 dbgen SF1，Arrow IPC 六表统一输入。

## 历史目录

`docs/artifacts/mvp_sf1/` 是更早的最小实验，仅保留用于说明项目演进。它使用
更少重复和较旧的计时边界，不能与 V5 正式矩阵混用，也不会进入 V6 压缩包。
`docs/assets/` 下旧图同样只属于历史输出。

生成的 TPC-H `.tbl`、Arrow 数据集、TPC-H 工具、`build*`、`results/` 和
`dist/` 不提交；它们可按 README 和 GPU runbook 重新生成。

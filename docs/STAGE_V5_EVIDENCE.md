# V5 可复现实验与证据记录

## 最小交付范围

V5 没有继续扩展成通用 benchmark 平台，而是完成课程项目需要的最小闭环：

- `scripts/benchmark_schema.py` 定义 `schema_version=1` 的固定 47 字段记录；
- `scripts/process_monitor.py` 保留子进程 stdout、stderr、返回码、超时和 peak RSS；
- `scripts/run_benchmarks.py --experiment-id ...` 启用严格模式，旧 MVP 命令仍兼容；
- `scripts/summarize_run_records.py` 计算 min、median、max、线性 p95 和样本标准差；
- `scripts/evidence_bundle.py` 固化 manifest、correctness 和所有 artifact SHA-256，
  并可重新检查文件是否发生变化；
- `experiments/v5_formal_sf1.yml` 固定正式 SF1 矩阵。

每个失败、timeout、OOM 和 no-GPU skip 都会成为 raw CSV 中的一行，并链接自己的
stdout/stderr 文件。0 个成功样本的配置没有性能统计，不会被写成 0 ms。

## 正式矩阵

正式运行使用规范 Arrow SF1 数据、3 次 warmup 和 10 次 measured repeat：

- specialized CPU 和 Arrow Acero：线程 1、2、4、8、16、32；
- `gpu-copy`、`gpu-managed`、`gpu-mapped`：各一组；
- hybrid：CPU ratio 0.25、0.50、0.75，CPU 线程固定为 8；
- cuDF：一组；
- 所有成功记录必须得到 `542abf4003633c7c`。

## 冷启动与 resident 边界

本次正式矩阵只有 `cold`。这里的 cold 指每个样本启动独立进程、读取和校验 Arrow
数据后执行查询；`query_total_ms` 是 backend 内部查询时间，`process_elapsed_ms`
包含进程启动和数据加载。C++ 目前没有可复用的 in-process session API，因此 V5
不生成 resident 性能数据。runner 接受 `resident` 只是为了记录
`ERROR_RESIDENT_UNSUPPORTED`，不会偷偷用多次冷启动冒充 resident。

同理，当前监控记录 CPU peak RSS，但没有可靠接入 NVML 子进程显存采样；
`gpu_peak_memory_bytes` 为 0，并在 `not_applicable_phases` 中标明
`gpu_memory_monitor`。这些是已知缺口，不影响时间和正确性证据的可追溯性。

bundle 还生成 `manifest.sha256`，最终 manifest digest 会写入 `progress.md` 并随
V5 commit 固定。单独的本地 checksum 不能防止有人同时重写文件和 checksum；
这里提供的是可发现意外修改的完整性检查，Git commit 才是外部版本锚点。

## 复核

```bash
python3 scripts/benchmark_schema.py validate results/experiments/v5-sf1-final/raw.csv
python3 scripts/evidence_bundle.py audit \
  --directory results/experiments/v5-sf1-final
```

第一个命令检查每一行的类型、范围、ratio、counter 和状态约束；第二个命令重新
计算所有 artifact checksum，并核对 raw/warmup 行数和 correctness 状态。

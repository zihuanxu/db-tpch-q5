# V6 交付与复现入口

这份文件是项目提交入口。当前范围固定为 TPC-H Q5、官方 SF1 和 RTX 4090：
代码、测试、实验记录、论文与接手材料都在仓库内；SF10、真正 resident session
和 Nsight 时间线不在本次结论范围内。

## 建议阅读顺序

1. `docs/paper/paper.pdf`：计算机学报模板课程论文。
2. `README.md`：代码结构、环境和复现命令。
3. `docs/artifacts/v5_sf1/`：论文使用的正式 SF1 原始证据。
4. `docs/research/CLAIM_LEDGER.md`：哪些结论成立、被拒绝或尚未实现。
5. `docs/learning/README.md`：按七节课接手原理和代码。
6. `docs/defense/README.md`：5/10 分钟讲稿与高风险问题。
7. `docs/process/README.md`：根据仓库证据整理的研究过程记录。

## 最快验收

```bash
conda env create -f environment-arrow-cpu.yml
conda run -n memq5-arrow-cpu bash scripts/ci_cpu.sh
python3 scripts/evidence_bundle.py audit --directory docs/artifacts/v5_sf1
python3 scripts/release_audit.py --json
```

带 CUDA 和 cuDF 的复验步骤见 `docs/GPU_SERVER_RUNBOOK.md`。正式 Arrow 数据集
由 TPC-H dbgen 输出生成，不随提交包分发；tiny fixture 可以直接运行。

## 打包

```bash
python3 scripts/package_submission.py \
  --output dist/memory-db-tpch-q5-v6.tar.gz
(cd dist && sha256sum -c memory-db-tpch-q5-v6.tar.gz.sha256)
```

压缩包内含 `MANIFEST.sha256`，旁边生成整个压缩包的 `.sha256` 文件。脚本会
排除构建目录、`data/`、`results/`、早期 MVP 证据、前期 hashjoin 实验和未定稿
报告，保留源码、测试、论文、学习材料及完整 V5 证据。

## 课程要求对应

| 课程要求 | 本项目实现 | 状态 |
| --- | --- | --- |
| 基于 TPC-H Q5 的 CPU-GPU 查询 | 固定六表连接计划，CPU 预处理后执行 lineitem 聚合 | 已实现 |
| CPU 端 Apache Arrow | Arrow IPC、manifest、C++ specialized 与 Acero 两条路径 | 已实现 |
| PCIe 显式传输 | `gpu-copy` staging 后 `cudaMemcpy` 到 device memory | 已实现 |
| UVA/统一地址访问 | `gpu-mapped`；另比较 `gpu-managed` prefetch | 已实现 |
| GPU 算子库 | cuDF merge/filter/groupby/sort 对照 | 已实现 |
| CPU-GPU 协同 | `hybrid-arrow` 按 batch 切分并发执行、精确合并 | 已实现，未获得加速 |
| 可共享、可重现、可验证 | Git 源码、生成器、tiny、CI、V5 原始记录和审计器 | 工程材料已完成 |
| 过程文档 | 七份仓库过程记录；腾讯共享需用户创建并授权 | 外部操作待完成 |
| 课程论文 | CjC 模板 LaTeX/PDF，结果由证据自动导入 | 已完成 |

## 正式结论

V5 冻结矩阵含 19 个配置，每个配置 3 次预热、10 次正式测量，共 57 次预热和
190 次有效测量。所有正式结果 hash 都是 `542abf4003633c7c`，并通过官方
`q5.out` 逐行核对。专用 CPU 最佳查询中位数为 61.414 ms；cuDF 为
116.427 ms；三种 CUDA 路径为 314.151 至 412.264 ms；最佳 hybrid 为
222.832 ms。

因此本实验只支持“数据位置和执行生命周期会显著影响性能”，不支持“GPU 或
hybrid 在本实现的 SF1 上一定快于 CPU”。`gpu-mapped` 没有显式 H2D 计时，
但 GPU 仍通过 PCIe 读取主机内存，不能称为没有传输。

## 已知限制

- 只测 SF1 和一台 RTX 4090 主机，不能推广到所有规模和硬件。
- GPU 只扫描聚合 lineitem，维表过滤和索引构造仍在 CPU。
- Arrow 23 环境没有使用 Arrow CUDA 扩展类；CUDA 路径由 Arrow 数据 staging
  到原生 device、managed 或 mapped buffer。
- 冷进程包含加载和初始化；`query_total_ms` 与 `process_elapsed_ms` 必须分开解释。
- 没有实现真正的 resident 多请求会话，也没有用 Nsight 证明 kernel 时间线重叠。

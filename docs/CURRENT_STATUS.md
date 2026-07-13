# Current Status

最后核对：2026-07-14。当前对外口径只以 V2-V6 源码、
`docs/research/CLAIM_LEDGER.md` 和 `docs/artifacts/v5_sf1` 为准。早期
`mvp_sf1`、旧 hash `9f1f...` 和 `.tbl` full matrix 只属于历史审计，不再作为
最终性能或正确性证据。

## 已完成实现

- canonical Arrow IPC 六表数据、manifest 和 C++ loader；
- Arrow specialized CPU 与 Arrow Acero 查询；
- Arrow `gpu-copy`、`gpu-managed`、`gpu-mapped` 三种 CUDA 模式；
- Arrow batch 级 CPU--GPU `hybrid-arrow`，支持 0.25/0.50/0.75 CPU 比例；
- 读取同一 Arrow 数据集的 cuDF 算子库基线；
- `revenue_1e4` 精确整数聚合、结果 hash 和官方 q5.out oracle；
- 版本化 benchmark schema、进程监控、统计重算和证据 checksum 审计。

## 正式证据锚点

| 项目 | V5 正式值 |
| --- | --- |
| 数据 | TPC-H SF1，ASIA，1994-01-01 |
| 配置 | 19 组 |
| 协议 | 每组 3 warmups + 10 measured cold processes |
| 成功 | 190/190 measured，57/57 warmups |
| 唯一 hash | `542abf4003633c7c` |
| evidence manifest | `e3337842d367b541b10f3ecb425d1ba0c7a0378555a87f6c42669ed831b5e660` |

主要查询中位数：specialized CPU 16 线程 61.414 ms；Acero 32 线程
321.535 ms；cuDF 116.427 ms；copy/managed/mapped 分别为 314.151、
358.158、412.264 ms；hybrid 最佳 75% CPU 为 222.832 ms。混合执行正确且
并发，但没有超过 pure CPU，这是正式负结果。

`query_total_ms` 是后端查询阶段；`process_elapsed_ms` 还包含启动、Arrow
加载和 Python/Conda 等成本。cuDF 的查询中位数 116.427 ms，冷进程中位数
3682.381 ms，这两列不能混作一个排名。

## 当前验证命令

证据与出版材料：

```bash
python3 scripts/evidence_bundle.py audit --directory docs/artifacts/v5_sf1
python3 scripts/validate_claim_ledger.py docs/research/CLAIM_LEDGER.md
python3 scripts/validate_process_docs.py docs/process
python3 scripts/check_learning_links.py docs/learning
python3 scripts/import_paper_evidence.py \
  --evidence docs/artifacts/v5_sf1 \
  --ledger docs/research/CLAIM_LEDGER.md \
  --output docs/paper/generated
bash docs/paper/build.sh
```

Arrow+CUDA Release 构建：

```bash
cmake -S . -B build-arrow-cuda-release \
  -DMEMQ5_ENABLE_ARROW=ON \
  -DMEMQ5_ENABLE_CUDA=ON \
  -DMEMQ5_ENABLE_TESTS=ON \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=89
cmake --build build-arrow-cuda-release -j 8
CUDA_VISIBLE_DEVICES=0 ctest --test-dir build-arrow-cuda-release --output-on-failure
```

正式矩阵的精确命令和环境保存在 `docs/artifacts/v5_sf1/commands.txt` 与
`environment.json`，无需从旧文档猜测。

## 已知限制

- 固定 Q5，不是通用 SQL/事务数据库；
- GPU 只执行 lineitem 扫描和聚合，不是完整六表 GPU 计划；
- 只有 SF1 cold-process 正式证据；
- 没有 SF10、resident、并发查询、NVML GPU 峰值显存或 Nsight timeline；
- duration overlap 不等于已证明 CPU scan 与 CUDA kernel 重叠；
- hybrid 两侧仍有重复计划和 GPU staging。

## 外部待办

- 把 `docs/process/` 同步到腾讯共享文档并给老师开权限；
- 确认 GitHub 仓库可见性并创建最终 release。

这两项依赖用户账号操作，仓库内保持 `EXTERNAL_ACTION_REQUIRED`，不会伪装成
已经完成。

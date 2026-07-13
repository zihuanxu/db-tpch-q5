# V6 提交检查单

最后更新：2026-07-14。正式证据目录为 `docs/artifacts/v5_sf1/`，实验 hash 为
`542abf4003633c7c`。早期 `mvp_sf1` 仅用于历史审计，不进入论文和提交包。

## 工程与测试

- [x] V2 Arrow CPU specialized/Acero 实现与 oracle 验证。
- [x] V3 Arrow 输入的 copy/managed/mapped CUDA 三模式。
- [x] V4 hybrid 三比例、cuDF 与真实 GPU sanitizer 验证。
- [x] V5 冻结 19 配置矩阵并完成 57 次预热、190 次测量。
- [x] V5 evidence bundle 校验 checksum、coverage、matrix 和 summary。
- [x] V6 主张、论文导入、过程文档和学习材料测试。
- [x] 最终 CPU CI：14/14 CTest、79 项 Python 通过，2 项设备测试跳过。
- [x] 最终 Arrow+CUDA 编译和 21 项 CTest：0 失败；本轮沙箱没有设备节点，
  7 项 CUDA runtime 测试跳过，真实 GPU 21/21 结果保存在 V4/V5 记录中。
- [x] 最终 RAPIDS/PyArrow 测试：14/14 通过。

## 论文与讲解

- [x] `docs/paper/paper.tex` 读取 `generated/results.tex`，没有手抄正式数字。
- [x] `docs/paper/paper.pdf` 编译为 3 页并通过文本、版面和错误日志检查。
- [x] `docs/research/CLAIM_LEDGER.md` 明确保留 hybrid 负结果。
- [x] 七节接手材料、答辩讲稿和高风险问题齐全。
- [x] SF1、冷进程、计时边界、Arrow CUDA 扩展等限制已写明。

## 发布与打包

- [x] `LICENSE`、`NOTICE`、`CITATION.cff`、贡献说明和 changelog。
- [x] CPU GitHub Actions、Conda 环境、CMake presets 和 Dockerfile。
- [x] `scripts/release_audit.py --json` 区分工程失败和外部操作。
- [x] 生成并解压复验 `dist/memory-db-tpch-q5-v6.tar.gz`。
- [ ] 创建/推送 GitHub 最终 tag 与公开 release（外部操作）。
- [ ] 创建腾讯共享文档、同步 `docs/process/` 并确认老师权限（外部操作）。

所有工程复验完成后，把上面三个工程项勾选；两项外部操作不能由离线仓库伪造。

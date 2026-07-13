# V6 完成情况审计

日期：2026-07-14

V2 至 V5 的实现和正式实验已经冻结；V6 的论文、主张台账、过程记录、接手材料、
开源元数据、CI、自动审计和提交包均已完成工程验收。腾讯文档共享和 GitHub
公开发布仍是明确的外部操作，不计为本地代码失败。

## 需求状态

| 需求 | 状态 | 主要证据 |
| --- | --- | --- |
| TPC-H Q5 六表语义与精确收入 | 完成 | `src/engine/`、oracle tests |
| Apache Arrow CPU 存储/查询 | 完成 | loader、specialized、Acero、Arrow CTest |
| CUDA explicit copy | 完成 | `gpu-copy`、SF1、sanitizer |
| CUDA managed memory | 完成 | `gpu-managed`、SF1、sanitizer |
| UVA mapped host memory | 完成 | `gpu-mapped`、mapped read counters |
| RAPIDS cuDF 算子库 | 完成 | `baselines/cudf_q5.py`、SF1 测量 |
| CPU-GPU hybrid | 完成但假设被拒绝 | 三个比例正确；SF1 未快于最优 CPU |
| 正式实验协议与证据 | 完成 | `docs/artifacts/v5_sf1/` |
| 论文 | 完成 | `docs/paper/paper.tex`、`paper.pdf` |
| 接手与答辩材料 | 完成 | `docs/learning/`、`docs/defense/` |
| 开源工程入口 | 完成 | license、citation、CI、runbook、release audit |
| 腾讯过程共享 | 外部待办 | `docs/process/TENCENT_DOCS.md` |
| GitHub 公开 release | 外部待办 | 本地仓库不能确认远端状态 |

## 正式证据

- 实验 ID：`v5-sf1-final`。
- 矩阵：19 个精确配置，3 次预热和 10 次测量。
- 结果：57/57 预热、190/190 测量成功，零失败。
- 正确性：唯一 hash `542abf4003633c7c`，官方五行 oracle 一致。
- manifest SHA-256：
  `e3337842d367b541b10f3ecb425d1ba0c7a0378555a87f6c42669ed831b5e660`。
- 证据包：503 个 checksummed artifacts，包含 raw/warmup CSV、环境、matrix、
  correctness、summary 和每进程 stdout/stderr。

## 自动审计

```bash
python3 scripts/evidence_bundle.py audit --directory docs/artifacts/v5_sf1
python3 scripts/check_paper.py
python3 scripts/validate_claim_ledger.py docs/research/CLAIM_LEDGER.md
python3 scripts/validate_process_docs.py docs/process
python3 scripts/check_learning_links.py docs/learning
python3 scripts/release_audit.py --json
```

`release_audit.py` 只有工程检查失败时返回非零；腾讯文档和 GitHub 发布显示为
`EXTERNAL_ACTION_REQUIRED`，不会被错误写成代码测试失败，也不会被假装已完成。

## 最终回归结果

- 基础 CPU preset：干净构建，CTest 5/5 通过。
- Arrow CPU CI：干净 Release 构建，CTest 14/14；Python 79 passed、2 skipped；
  specialized 和 Acero 均通过 tiny oracle。
- Arrow+CUDA：重新编译成功，CTest 21 项、0 failed；当前沙箱没有
  `/dev/nvidia*`，7 项 CUDA runtime 测试按返回码 77 跳过。V4 同一代码主线已有
  真实 RTX 4090 21/21 和 compute-sanitizer 零错误记录，V5 正式矩阵也全部成功。
- RAPIDS/PyArrow：V5 真实 GPU Python 3.11 环境 14/14 通过；最终当前环境没有
  GPU，复验为 12 passed、2 skipped。cuDF 测试现在同时检查包和 CUDA device，
  不再把 `cudaErrorNoDevice` 错报为查询实现失败。
- 论文：3 页 A4 PDF；无 Overfull、未定义引用或 LaTeX error；文本包含正式 hash
  和 61.414/222.832 ms，第一页人工检查无重叠。
- 提交包：687 个文件，内部 manifest 与外部 SHA256 通过。全新解压目录再次通过
  release audit、Arrow 14/14 CTest、Python 79 passed/2 skipped 和 tiny oracle。
- 打包复验曾发现中文路径被 Git 转义、解压目录没有 `.git` 两个问题；均先复现、
  再加回归测试并修复，最终包不再缺 process/learning 文件。
- Dockerfile 已通过发布文件静态检查；本机 Docker socket 无访问权限，因此没有
  把“镜像实际构建成功”列入完成证据。Conda/CMake 是已实测的主复现路径。
- 论文构建生成 `paper.provenance.json`，将当前源稿、自动导入结果和 PDF 的
  SHA256 绑定；release audit 会拒绝任一文件在构建后发生变化的情况。
- 独立最终审阅发现并关闭三项发布审计问题：缺失 archive payload 曾触发
  traceback、同名目录可冒充必需文件、`.dockerignore` 未进入 release audit。

## 当前限制

- 正式性能数据只有 SF1、冷进程和一台 RTX 4090。
- 未实现真正 resident session、NVML 峰值显存采样和 SF10。
- 没有 Nsight/NVTX 时间线，因此只报告 host duration overlap，不声称 kernel overlap。
- GPU 路径没有完成整个六表查询，前半段仍由 CPU 准备。
- 旧实验和旧报告保留在 Git 历史或仓库历史目录，但不用于 V6 论文结论。

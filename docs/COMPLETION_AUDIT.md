# V7 完成情况审计

日期：2026-07-14

V7 已完成常驻会话、SF1/SF10 双规模实验、独立正确性门禁、hybrid fixed/auto
比较和 NSYS/NCU 剖析。V5 冷进程证据保留为历史对照，不再承担论文的主要结论。
腾讯文档共享和 GitHub 公开发布仍属于需要本人账户完成的外部操作。

## 需求状态

| 需求 | 状态 | 主要证据 |
| --- | --- | --- |
| TPC-H Q5 六表语义与精确收入 | 完成 | `src/engine/`、独立 oracle、correctness gate |
| Apache Arrow CPU 存储与查询 | 完成 | specialized、Arrow Acero、SF1/SF10 |
| CUDA copy / managed / mapped | 完成 | 三种常驻后端、双规模结果、NSYS/NCU |
| RAPIDS cuDF 算子库 | 完成 | `baselines/cudf_q5.py`、双规模结果 |
| CPU-GPU hybrid | 完成 | fixed sweep、auto模型、阶段重叠剖析 |
| 常驻实验协议 | 完成 | setup/warmup/measured request 分离 |
| 可审计证据 | 完成 | 两个V7 evidence bundle和profiler checksum |
| 课程论文 | 完成 | `docs/paper/paper.tex`、4页 `paper.pdf` |
| 接手与答辩材料 | 完成 | `docs/learning/`、`docs/defense/` |
| 开源工程入口 | 完成 | license、citation、CI、runbook、release audit |
| 腾讯过程共享 | 外部待办 | `docs/process/TENCENT_DOCS.md` |
| GitHub公开release | 外部待办 | 需要本人确认远端仓库和账号 |

## 正式证据

- SF1和SF10各18个配置、54次warmup、180次measured request，全部成功。
- 八类后端均通过oracle；SF1 hash为`542abf4003633c7c`，SF10为
  `b1351a421ba8dcfd`。
- SF1 evidence manifest SHA-256 为
  `81c48484b397d8b9d8c3d24e87a434a6cbb7d7e576ae9bfb900cc6abdf2532aa`。
- SF10 evidence manifest SHA-256 为
  `2a9e07aaaeb837f46217197b2b82e2ba473e85073c21437db53845dc42075997`。
- 完整 profiler bundle 共10个 profile，manifest SHA-256 为
  `fb24875ef52cd00c80cc94985018290d32fbe1b6d8af2eb05d7bfaccbea186a7`。
- 论文中的数字由 `scripts/import_paper_evidence.py` 从上述证据导入，不手抄结果。

## 主要研究结论

- 在常驻请求口径下，SF1最佳fixed hybrid为0.125，SF10为0.375；最优比例随规模
  变化，因此不能把单一比例推广到所有数据规模。
- 当前auto模型在SF1和SF10分别产生约33.89%和9.21%的regret，原有“auto接近
  最优fixed”的假设被正式拒绝，而不是隐藏负面结果。
- SF10上copy和managed核函数时间接近；mapped明显更慢。mapped的低device DRAM
  读取来自远程主机页访问，不能解释成“读取数据更少”。
- hybrid profiler显示CPU scan和GPU请求位于同一measured request时间窗内，支持
  阶段重叠；profiler时间不与普通延迟混算。
- setup成本很大。混合路径稳态请求快，不等于单次端到端查询一定快。

## 自动审计

```bash
python3 scripts/v7_evidence_bundle.py audit --directory docs/artifacts/v7_sf1_resident
python3 scripts/v7_evidence_bundle.py audit --directory docs/artifacts/v7_sf10_resident
python3 scripts/check_paper.py
python3 scripts/validate_claim_ledger.py docs/research/CLAIM_LEDGER.md
python3 scripts/validate_process_docs.py docs/process
python3 scripts/check_learning_links.py docs/learning
python3 scripts/release_audit.py --json
```

`release_audit.py` 只在工程检查失败时返回非零；腾讯文档和GitHub发布显示为外部
待办，不会被伪装成本地已经完成。

## 最终回归

- 默认Python环境：395 passed、6 skipped；Arrow/cuDF Python 3.11环境：22 passed。
- Arrow+CUDA构建：CTest 45项、0 failed。最终shell不再暴露CUDA设备，因此15项
  runtime测试按返回码77明确跳过；正式GPU矩阵和profiler来自此前同机RTX 4090
  的完整运行，不能用本轮skip替代真实GPU证据。
- 论文：4页PDF，无Overfull或未定义引用，provenance与源稿、自动导入结果和PDF
  checksum一致。
- 提交包：`/tmp/memq5-v7-final.tar.gz`，1034个文件，外部SHA-256通过；在全新
  解压目录中再次运行release audit，仍为`engineering_ready: true`。
- 最终CTest曾发现`test_q5_hybrid_auto_cuda`的无设备检查位置太晚；添加入口门禁
  并重编译后，专项和全量CTest均通过。

## 当前限制

- 正式性能数据只来自一台RTX 4090，不能直接推广到PCIe代际或GPU型号不同的机器。
- SF1/SF10都只使用一个固定Q5参数组合，没有研究选择率变化和并发请求。
- GPU只处理lineitem主扫描与部分聚合准备，六表关系语义仍需CPU侧准备。
- auto模型样本少且setup较高，还不是生产级调度器。
- NCU/NSYS用于解释机制，不替代普通runner的端到端延迟统计。

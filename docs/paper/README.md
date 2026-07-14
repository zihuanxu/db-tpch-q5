# 课程论文目录

本目录使用《计算机学报》官网提供的 LaTeX 模板。模板下载地址：

`http://cjc.ict.ac.cn/wltg/new/submit/LatexTemplet.zip`

下载日期为 2026-07-13，原始压缩包 SHA256 为：

`ec348005155844078f84d74f801e2978234c80a8cf9aa0c126dd905eb37c1e82`

仓库只保留编译课程论文所需的模板类和样式文件。它们的版权归原作者
或《计算机学报》相关权利人所有，不适用本项目源码许可证。

当前论文已填写作者徐子桓和学号 2025104082。实验结果只能从仓库中已保存的
CSV、环境记录和正确性校验记录引用；没有证据的实验不得写成已完成。

## V7 结果来源

- `docs/artifacts/v7_sf1_resident`：SF1 常驻会话正式矩阵；
- `docs/artifacts/v7_sf10_resident`：SF10 常驻会话正式矩阵；
- `docs/artifacts/v7_hybrid_model`：fixed 比例曲线、auto 选择和 regret；
- `docs/artifacts/v7_profiler`：Nsight 的轻量发布副本。

V5 的 `docs/artifacts/v5_sf1` 只作为冷进程历史对照。V5 的冷进程墙钟、V7 的
setup 和 V7 的 resident request 是三种不同口径，不能放在同一列中直接排名。

重新生成论文数值并编译：

```bash
python3 scripts/import_paper_evidence.py \
  --sf1 docs/artifacts/v7_sf1_resident \
  --sf10 docs/artifacts/v7_sf10_resident \
  --model docs/artifacts/v7_hybrid_model/memq5-v7-hybrid-model.json \
  --ledger docs/research/CLAIM_LEDGER.md \
  --output docs/paper/generated
bash docs/paper/build.sh
python3 scripts/check_paper.py
```

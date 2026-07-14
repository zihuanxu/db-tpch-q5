# V7 提交检查单

最后更新：2026-07-14。

## 工程与证据

- [x] Arrow specialized/Acero、三种 CUDA、cuDF 和 hybrid 实现；
- [x] resident session 分离 setup、request 和 1/10/100 次摊销值；
- [x] SF1/SF10 各 18 配置、54 warmups、180 measured；
- [x] 两组正式 evidence bundle 独立审计 `ok=true`；
- [x] fixed 比例曲线和 hybrid-auto regret 评估；
- [x] 10 组 NSYS/NCU 完整 bundle 审计 `ok=true`；
- [x] compact profiler 183 个文件通过 `checksums.sha256`；
- [x] 全部正式请求通过独立 oracle；
- [x] 真实 RTX 4090 CTest 45/45 和 sanitizer 零错误。

## 论文与讲解

- [x] `paper.tex` 同时引用 SF1/SF10 resident 和模型证据；
- [x] `paper.pdf` 由 CjC 模板编译并写入 provenance；
- [x] 冷进程、setup、resident request、profiler 时间明确分开；
- [x] auto regret、setup 成本和系统限制没有隐藏；
- [ ] 学习、答辩和过程文档全部复核到 V7；

## 发布

- [x] `LICENSE`、`CITATION.cff`、贡献说明和 changelog；
- [ ] V7 release audit 通过且生成最终压缩包；
- [ ] 创建/推送 GitHub tag 与公开 release（外部操作）；
- [ ] 同步腾讯共享文档并确认老师权限（外部操作）。

外部账号操作不能由离线仓库伪造成已完成。

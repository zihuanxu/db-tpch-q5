# V7 Resident、SF10、Profiler 与动态 Hybrid 研究扩展设计

**日期：** 2026-07-14  
**基础版本：** `submission-v6-final` (`14696bd`)  
**工作分支：** `codex/v7-resident-sf10-profiling`

## 1. 目标

V7 在不破坏 V6 可交付版本的前提下，回答四个延伸问题：

1. 数据和 GPU 缓冲区常驻后，冷进程开销与单次查询延迟相差多少？
2. V6 在 SF1 上得到的性能排序，扩展到 TPC-H SF10 后是否仍然成立？
3. copy、managed、mapped 和 hybrid 的 CUDA 时间线与 kernel 指标有何差异？
4. 根据当前数据规模和 CPU/GPU 校准速度自动选择 hybrid 比例，能否接近或超过
   固定比例？

V7 不是通用数据库服务，不增加 SQL parser、事务、任意查询、多租户或 SF100。
所有新结论必须由新证据支持；没有获得加速时保留负结果。

## 2. 版本与交付策略

- V6 commit、tag、论文和压缩包保持冻结，始终可以单独提交。
- V7 分为四个可独立验收的里程碑：resident、SF10、profiler、hybrid-auto。
- 每个里程碑先通过 tiny/SF1 正确性与回归，再进入下一阶段。
- V7 使用独立 evidence bundle 和 claim ID，不修改 V5 原始证据。
- 实验完成后再更新论文、学习材料、答辩材料和最终压缩包。

## 3. Resident Session

### 3.1 语义边界

V7 resident 是固定 dataset、region、日期和 engine 的进程内会话。会话创建时只做
一次 Arrow 加载、Q5 plan 构造、host staging、CUDA 分配和初始传输；随后执行
3 次预热请求和 10 次正式请求。

不支持在同一会话内改变 region 或日期。参数变化需要创建新会话，这个限制会写入
论文，避免把固定查询原型描述成通用数据库服务。

### 3.2 会话接口

新增以下边界，具体类名可在实现时按现有命名调整：

- CPU specialized session：缓存 Q5 plan 和 Arrow lineitem 视图，重复执行并行扫描。
- Acero session：Arrow dataset 常驻，重复执行 Acero plan；分别报告 plan 构造和
  执行时间，不声称 Acero 内部状态全部缓存。
- CUDA session：统一支持 copy、managed、mapped 三种 mode。
- Hybrid session：持有一个 CPU session 和一个 GPU copy session，lineitem 分区在
  setup 时固定，重复请求并发执行两个分区。
- cuDF resident runner：六张 DataFrame 只创建一次，重复执行 merge/filter/groupby。

CUDA session 的持久状态包括输入列、order/supplier nation map、结果数组、匹配行
计数器和错误标志。每次请求只允许清零输出、启动 kernel、同步和复制小结果。
resident copy 请求的输入 H2D 必须为 0；managed 和 mapped 必须分别报告预取与
远程读取语义，不能写成“无数据传输”。

### 3.3 CLI 协议

新增 resident CLI，使用 JSON Lines 输出：

- 第一行是 `record_type=session_setup`，记录 dataset、engine、参数、load/setup
  分解、常驻 host/GPU bytes 和选择比例。
- 后续每行是 `record_type=request`，记录 warmup 标志、request index、结果 hash、
  query timing、计数器和错误状态。
- 任一请求失败时仍保留之前的行，并以非零状态结束。

benchmark runner 将一个 resident 进程的多行输出展开为多条 V7 run record，不能
把一个冷进程循环伪装成多个独立 resident session。

## 4. 计时与记录模型

V7 明确分开：

- `process_elapsed_ms`：整个子进程时间。
- `dataset_load_ms`：Arrow manifest 校验与六表加载。
- `session_setup_ms`：plan、staging、分配、初始 H2D/prefetch。
- `tune_ms`：hybrid-auto 校准和比例选择。
- `query_total_ms`：一次 resident 请求延迟。
- `kernel_ms`、`cpu_ms`、`d2h_ms`、`overlap_wall_ms`：请求内部阶段。
- `resident_gpu_bytes`、`resident_pinned_bytes`、`cpu_peak_rss_bytes`、
  `gpu_peak_memory_bytes`：容量证据。

冷进程与 resident 延迟不放在同一列中混淆。论文同时给出 setup 摊销前后结果，
例如 1、10、100 次请求时的估算总成本。

## 5. SF10 数据与正确性

### 5.1 数据流水线

1. 运行磁盘预检，要求至少 40 GiB 可用空间。
2. 使用仓库内已校验的 TPC-H V3.0.1 `dbgen -s 10 -f` 生成原始数据。
3. 用现有准备脚本只保留 Q5 六表及所需列，生成 source manifest。
4. 生成 Arrow IPC 六表数据集，记录 schema、行数、batch、字节和 SHA256。
5. 原始 `.tbl`、Arrow 数据和 TPC-H 工具仍不进入 Git 或提交包。

预计 SF10 的 Q5 Arrow 列小于 4 GiB，CUDA 输入缓冲区小于 3 GiB，可放入 24 GiB
RTX 4090。实际值以 manifest 和 NVML 为准，估算值不进入结果结论。

### 5.2 正确性门禁

- specialized 与 Acero 必须得到相同的五行结果和 hash。
- copy、managed、mapped、hybrid 固定比例、hybrid-auto 和 cuDF 必须匹配该 hash。
- 使用独立 DuckDB SQL 对六个 `.tbl` 文件运行 Q5，逐行核对国家和 Decimal 收入。
- 所有后端一致后才允许性能样本进入正式 summary。
- 若独立 SQL 与 C++ 不一致，停止正式测量并保留失败证据。

## 6. 动态 Hybrid 选择

### 6.1 方案

V7 不做黑箱穷举后再把最佳值称为“动态优化”。hybrid-auto 在 session setup 内：

1. 分别运行一次 CPU-only 和 resident GPU-only 校准请求。
2. 根据有效 scan rows、CPU 吞吐、GPU 吞吐和 GPU 固定开销建立简单 makespan 模型。
3. 求解 CPU/GPU 预计完成时间相等的 CPU ratio，并裁剪到 `[0.0, 1.0]`。
4. 将 ratio 映射到实际 Arrow batch 边界。
5. 报告原始预测、最终行数比例、校准时间和模型输入。

核心近似为：

```text
cpu_time(r) = r * N / cpu_rows_per_ms
gpu_time(r) = gpu_fixed_ms + (1-r) * N / gpu_rows_per_ms
```

选择使 `max(cpu_time, gpu_time)` 最小的比例。该模型有意保持简单，论文必须讨论
过滤选择率、NUMA、batch 边界和共享内存带宽造成的误差。

### 6.2 验证

- 固定比例使用 0.25、0.50、0.75，并增加 0.125 间隔的诊断 sweep。
- auto 选择不能使用正式测量样本反向调参。
- 分别比较预测比例、实测最佳固定比例、CPU-only、GPU-only。
- 报告选择误差和调优成本；auto 变慢时拒绝“自动优化有效”的假设。

## 7. Profiler 设计

### 7.1 NVTX 与 Nsight Systems

在 CUDA 和 hybrid 路径加入可选 NVTX range：dataset/plan、host staging、allocation、
H2D/prefetch、CPU scan、kernel、D2H、merge 和 request。普通构建可关闭 NVTX，
不改变查询结果。

对 SF1 和 SF10 的 copy、managed、mapped、固定 hybrid、hybrid-auto 各采集一个
稳定 resident 请求：

```text
nsys profile --trace=cuda,nvtx,osrt ...
nsys stats --report cuda_api_sum,cuda_gpu_kern_sum,cuda_gpu_mem_time_sum ...
```

保存 `.nsys-rep`、导出 CSV、命令、工具版本和 SHA256。只有时间线实际显示重叠时
才使用“kernel overlap”表述。

### 7.2 Nsight Compute

先用 `ncu --query-metrics` 验证 RTX 4090 支持的指标，再冻结最小 metric 集：kernel
duration、DRAM read bytes/throughput、SM throughput 和 occupancy。每种 mode 只分析
一个稳定请求，避免 profiler 重放污染正式 latency 样本。

Profiler 运行与性能 benchmark 分开，带 profiler 的时间不能进入普通中位数。

## 8. 实验矩阵

### 8.1 开发门禁

- tiny：所有 session 重复请求结果一致，错误参数拒绝，setup/request 行数正确。
- SF1：cold 与 resident smoke；copy/managed/mapped/hybrid sanitizer。
- 所有正式配置采用 3 次预热、10 次测量。

### 8.2 SF1 与 SF10 正式配置

- CPU specialized：8、16、32 threads。
- Acero：8、16、32 threads。
- copy、managed、mapped resident session。
- cuDF resident session。
- hybrid fixed：0.25、0.50、0.75 CPU，线程数使用 CPU sweep 中的最佳值。
- hybrid diagnostic sweep：0.125 至 0.875，仅用于模型评价。
- hybrid-auto：每个数据规模独立校准一次。
- cold 对照保留 V6 引擎，并在 SF10 运行相同核心后端。

每个数据规模使用独立 bundle，不能把 SF1 与 SF10 样本合并计算一个中位数。

## 9. Evidence Bundle 与主张门禁

V7 新 schema 在 V5 字段基础上增加 lifecycle、session、setup、resident memory、
auto ratio 和 profiler provenance。bundle 至少包含：

- matrix、raw requests、warmups、session setup records；
- environment、commands、stdout/stderr；
- dataset manifest 与独立 SQL oracle；
- profiler 命令、导出统计和文件 checksum；
- summary、correctness gate、manifest 与 manifest digest。

新增主张默认是 `PLANNED`。只有 bundle audit、正确性和样本覆盖全部通过后才能改为
`VERIFIED` 或 `REJECTED`。旧 V5 结论不因 V7 预期而改写。

## 10. 测试与验收

- C++ 单元测试：session 生命周期、重复请求、三种内存 mode、分区、auto 模型、
  overflow 和错误清理。
- Python 单元测试：JSONL parser、V7 schema、resident process attribution、matrix、
  summary、bundle checksum 和 profiler parser。
- CPU/Arrow CTest 必须无回归。
- 真实 GPU CTest 必须全部运行而非 skip；代表配置通过 compute-sanitizer。
- SF10 所有后端 hash 与 DuckDB 逐行一致。
- resident 正式矩阵和 profiler bundle 必须可在新目录重新审计。
- 提交包解压后至少通过 CPU/Arrow CI 和 evidence/release audit。

## 11. 失败与资源处理

- 磁盘不足、GPU OOM、profiler 权限不足和 timeout 都写为结构化失败，不删除样本。
- SF10 生成使用独立目录和 manifest；不覆盖 SF1。
- GPU 实验固定 `CUDA_VISIBLE_DEVICES=0` 并记录物理 GPU UUID。
- profiler 先运行一个 tiny/SF1 smoke，失败时不启动批量采集。
- 不使用失败配置推导速度结论，也不把 skip 写成 pass。

## 12. 最终文档更新

实验完成后再执行以下工作：

- 由 V7 bundle 自动生成论文宏和性能图。
- 论文新增 resident 生命周期、SF1/SF10 对比、profiler 时间线、动态比例误差和限制。
- 更新 claim ledger、CURRENT_STATUS、GPU runbook、过程记录、学习材料和答辩问题。
- 保留学生项目边界，不把固定 Q5 原型包装成通用查询优化器。
- 生成独立 `submission-v7-research` tag 和可审计压缩包，V6 tag 不移动。

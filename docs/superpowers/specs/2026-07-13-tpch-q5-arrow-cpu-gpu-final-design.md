# TPC-H Q5 Arrow CPU-GPU 协同查询处理最终设计

状态：已由用户批准
日期：2026-07-13
仓库：`db-tpch-q5`

## 1. 目标

将当前课程原型升级为严格对应老师技术路线的最终研究项目：

- 以 Apache Arrow 作为 CPU 端规范内存存储。
- 基于 Arrow 的 GPU 支持和 CUDA 内存机制实现 GPU 端存储。
- 实现 TPC-H Q5 的 Arrow CPU、专用 CPU、手写 CUDA、CPU-GPU 混合和 cuDF 查询路径。
- 对比显式 PCIe copy、UVA managed memory 和 mapped pinned memory。
- 在相同数据、相同正确性语义和统一计时口径下完成实验。
- 提供公开、可复现、可验证的 GitHub 项目。
- 形成腾讯共享过程文档、计算机学报模板论文和答辩教学材料。

最终项目不是通用 DBMS，也不实现 SQL parser 或通用 optimizer。研究对象明确限定为固定 TPC-H Q5 的内存查询执行与 CPU-GPU 协同优化。

## 2. 当前基线与必须修复的问题

当前仓库已经具备：

- 固定 Q5 filter-propagation 计划。
- 专用 C++ CPU 和多线程扫描。
- `gpu-copy`、`gpu-managed`、`gpu-mapped` 三种 CUDA 路径。
- Python、PyArrow、DuckDB 和 cuDF 对照脚本。
- tiny、synthetic 和 TPC-H SF1 历史实验。
- 报告、图表、实验脚本和前期 `hashjoin-cpu` 课程成果。

最终设计必须修复以下缺口：

1. C++ 主存储是自定义 `Column<T>`，不是 Apache Arrow。
2. GPU 原始缓冲区没有形成 Arrow CPU-GPU 统一数据通路。
3. PyArrow 和 cuDF 各自重新解析 `.tbl`，与 C++ 的计时口径不公平。
4. 收入逐行截断到分，SF1 结果不严格匹配官方 `q5.out`。
5. CPU 和 GPU 没有并发处理同一查询，协同深度不足。
6. benchmark 缺少统一 warmup、p95、内存、传输字节和完整失败记录。
7. 正式 Q5 原始实验 CSV/JSON 没有进入 Git。
8. 缺少 LICENSE、固定依赖、正式论文模板和腾讯共享文档证据。

## 3. 范围与非目标

### 3.1 必做范围

- TPC-H Q5，默认参数 `ASIA` 和 `1994-01-01` 至 `1995-01-01`。
- tiny、synthetic、SF1、SF10。
- Arrow IPC 规范数据集。
- Arrow Acero CPU backend。
- 专用 Arrow-buffer CPU backend。
- 三种手写 CUDA 内存模式。
- `hybrid-arrow` CPU-GPU 并发 backend。
- cuDF Arrow backend。
- 独立 DuckDB decimal correctness oracle。
- 正确性、性能、profiling、开源、论文、过程和教学交付。

### 3.2 非目标

- 通用 SQL parser。
- 成本优化器或任意 join-order 搜索。
- 全部 TPC-H queries。
- 分布式执行。
- 为所有 GPU 模式实现自适应 CPU/GPU 调度。
- 将前期 `hashjoin-cpu` 重写为 Arrow。

`hashjoin-cpu` 保留为课程前期研究证据，在论文中用于说明 direct addressing、radix partition、cache/TLB 和 NUMA 对后期设计的启发，但不与最终 Q5 可执行文件耦合。

## 4. 总体架构

```text
TPC-H dbgen .tbl
        |
        v
Arrow 数据准备器
        |
        +-- Arrow IPC 数据集 + manifest + checksum
        |
        v
Arrow Table / RecordBatch 规范内存表示
        |
        +-- arrow-acero
        +-- cpu-specialized
        +-- gpu-copy
        +-- gpu-managed
        +-- gpu-mapped
        +-- hybrid-arrow
        +-- cudf
        +-- duckdb oracle（只做正确性参考）
```

所有正式 backend 必须从相同 Arrow IPC 数据集或同一规范 Arrow Table 开始。不得让某个 backend 静默使用不同的输入精度、过滤条件或预处理结果。

## 5. 可复现依赖环境

主版本固定为：

- Apache Arrow C++ / PyArrow 23.0.1。
- Arrow C++ 启用 CUDA、Acero、Compute、CSV 和 IPC。
- RAPIDS cuDF 26.06.0。
- CUDA 12.x，实际编译器版本和 driver 版本写入环境 manifest。
- CMake 和 C++17。

现有 PyPI PyArrow 23.0.1 包含 `libarrow`，但缺少可导入的 `_cuda` 扩展，不能作为 Arrow CUDA 完成证据。最终环境需要构建或安装启用 CUDA 的 Arrow C++ 组件。

仓库同时提供：

- CPU-only Conda 环境。
- GPU Conda 环境。
- CUDA development Dockerfile。
- CMake presets。
- 环境捕获脚本。

CPU-only 构建不要求 CUDA。GPU 构建必须显式验证可用设备和 Arrow CUDA 组件。

## 6. Arrow 规范数据层

### 6.1 表与类型

仅保留 Q5 所需列：

| 表 | Arrow 列 |
| --- | --- |
| region | `r_regionkey:int32`, `r_name:dictionary<string>` |
| nation | `n_nationkey:int32`, `n_name:dictionary<string>`, `n_regionkey:int32` |
| supplier | `s_suppkey:int32`, `s_nationkey:int32` |
| customer | `c_custkey:int32`, `c_nationkey:int32` |
| orders | `o_orderkey:int32`, `o_custkey:int32`, `o_orderdate:date32` |
| lineitem | `l_orderkey:int32`, `l_suppkey:int32`, `l_extendedprice:decimal128(15,2)`, `l_discount:decimal128(15,2)` |

专用 backend 可以读取等价的定点整数 view，但规范 schema 和持久化数据保持 Decimal128 语义。

### 6.2 数据准备

数据准备器负责：

1. 解析官方 `.tbl`。
2. 严格校验字段和小数尺度。
3. 构建 Arrow RecordBatch。
4. 保存每张表的 Arrow IPC 文件。
5. 写 manifest：schema、行数、scale factor、来源、生成命令、文件 checksum。
6. 可选执行全外键完整性验证。

RecordBatch 大小可配置，但正式实验固定并记录该值。

### 6.3 生命周期和接口

`ArrowTpchDataset` 负责加载和持有六张 Arrow 表。backend 只接收不可变 dataset view 和 Q5 参数，不直接打开 `.tbl`。

所有 buffer 访问必须：

- 检查 Arrow 类型。
- 检查 null count；Q5 官方数据路径不接受关键列 null。
- 正确处理 chunked arrays，不能假设每列只有一个 chunk。
- 通过明确 ownership 保证 CPU/GPU 异步操作期间 buffer 仍然存活。

## 7. 正确性语义

### 7.1 Q5 条件

- 目标 region 匹配参数。
- 订单日期满足半开区间 `[start_date, start_date + 1 year)`。
- 客户和供应商属于同一个目标 region 国家。
- 收入为 `l_extendedprice * (1 - l_discount)`。
- 按国家分组，收入降序；收入相同时国家名升序，保证确定性。

### 7.2 精度

禁止逐行截断到分。

- Arrow Acero 使用 Decimal128 等价语义。
- 专用 CPU/GPU backend 使用与 Decimal128 等价的高精度定点累计。
- 在 SF1/SF10 支持范围内使用经过溢出界限证明和运行时检查的 `int64` 累计尺度；CPU oracle 可使用更宽中间整数交叉验证。
- 聚合完成后按官方输出规则格式化。

### 7.3 三层 oracle

1. tiny fixture 手算结果。
2. SF1 官方 `dbgen/answers/q5.out`。
3. 独立 DuckDB decimal SQL 结果，用于 SF10 和额外参数。

result hash 只作为跨 backend 快速一致性检查，不能替代官方 oracle。

## 8. 查询 backend

### 8.1 Arrow Acero CPU

使用 Arrow Acero/Compute 表达：

- region、nation 过滤。
- supplier/customer/orders/lineitem joins。
- 日期过滤。
- 同国家过滤。
- Decimal revenue 计算。
- group-by aggregate 和排序。

该 backend 是老师要求的 Arrow 接口 CPU 查询实现和通用列式执行基线。

### 8.2 专用 CPU

从 Arrow arrays 读取 raw buffer view，保留当前固定物理计划：

- `supplier_nation_by_key`。
- `customer_nation_by_key`。
- `order_nation_by_key`。
- 多线程 lineitem scan。
- 线程本地 nation revenue 累计和最终归并。

该 backend 用于研究通用 Arrow 算子与固定查询专用优化的差异，不再拥有独立存储格式。

### 8.3 `gpu-copy`

- 输入来自 Arrow host buffers。
- 使用启用 CUDA 的 Arrow device buffer 或与 Arrow device interface 一致的受管 device buffer。
- 显式记录 Arrow host-to-device copy。
- kernel 从 GPU device memory 读取。
- 小结果复制回 CPU。

### 8.4 `gpu-managed`

- 使用 `cudaMallocManaged` 和 UVA。
- CPU 填充成本、prefetch、页面迁移和 kernel 分开计时。
- 正式运行在 kernel 前 prefetch；补充 profiling 观察迁移行为。
- 统一地址不被描述为零传输。

### 8.5 `gpu-mapped`

- 使用 mapped pinned host memory。
- GPU 通过 UVA device pointer 经 PCIe 读取主机数据。
- CPU 原始 Arrow buffer 到 pinned buffer 的复制单独计时。
- 不将 mapped 描述为完全无复制。

### 8.6 cuDF

- 使用 `cudf.from_arrow` 消费同一规范 Arrow 数据。
- 实现与 Q5 相同的 filter、joins、revenue、group-by 和排序。
- cold-start 与 resident-data 分开测量。

## 9. `hybrid-arrow` CPU-GPU 协同

### 9.1 必须是真并发

`hybrid-arrow` 将 lineitem Arrow RecordBatches 分成互不重叠的 CPU 和 GPU 集合：

- CPU worker pool 扫描 CPU batch。
- CUDA stream 传输并执行 GPU batch。
- CPU scan 与 GPU H2D/kernel 在墙钟时间上重叠。
- CPU 和 GPU 使用独立局部 revenue 数组。
- 完成后合并并排序。

只有 Nsight/时间线证明存在重叠，才能声称实现了 CPU-GPU 协同。

### 9.2 分配比例

正式比例：

- CPU 75% / GPU 25%。
- CPU 50% / GPU 50%。
- CPU 25% / GPU 75%。

纯 CPU 和纯 GPU 由已有 backend 覆盖。

主 hybrid 实验使用 explicit copy GPU 路径。managed/mapped hybrid 只作为补充敏感性实验，不是最终验收阻塞项。

### 9.3 分块和流水线

GPU 路径使用可配置、正式实验固定的 batch/chunk 大小。允许双缓冲，将下一批 H2D 与当前批 kernel、CPU scan 重叠。

最终结果必须与纯 CPU oracle 完全一致。分配比例、线程数或执行完成顺序不能改变结果。

## 10. 实验问题和假设

### 10.1 研究问题

- RQ1：Arrow 通用 CPU 查询与专用 CPU 物理计划差异是什么？
- RQ2：copy、managed、mapped 的成本分别发生在哪些阶段？
- RQ3：cuDF 通用 GPU 算子与手写 CUDA 的差异是什么？
- RQ4：CPU-GPU 并发是否优于纯 CPU 或纯 GPU？
- RQ5：数据规模变化是否改变最佳 CPU/GPU 比例？

### 10.2 预注册假设

- H1：固定 Q5 上，专用 CPU 快于 Arrow Acero。
- H2：copy kernel 最快，但有显式 H2D 成本。
- H3：mapped H2D 较少，但 kernel 因 PCIe 远程读取变慢。
- H4：resident-data 比 cold-start 更能体现 GPU 优势。
- H5：至少一个规模下存在优于纯 CPU/GPU 的 hybrid 中间比例。

假设被实验否定仍是有效结果，不能为了报告叙事删除或改写数据。

## 11. 实验矩阵

### 11.1 数据集

- tiny：正确性，不做性能结论。
- synthetic：控制 key 密度、选择率和规模。
- SF1：基础正式矩阵和官方 oracle。
- SF10：规模扩展和 hybrid 研究。

### 11.2 引擎

- `arrow-acero`。
- `cpu-specialized`。
- `gpu-copy`。
- `gpu-managed`。
- `gpu-mapped`。
- `hybrid-arrow`。
- `cudf`。

DuckDB 主要作为正确性 oracle；如纳入性能表，必须使用同一 Arrow/Parquet 等价输入并单列说明。

### 11.3 参数

- CPU threads：1、2、4、8、16、32。
- hybrid CPU/GPU ratio：75/25、50/50、25/75。
- warmup：3 次。
- measured repeats：10 次。

### 11.4 场景

Cold start 包含进程、IPC 加载、计划、传输、执行和输出。

Resident data 在数据和可复用缓冲区常驻后重复查询，排除一次性文本解析和主要初始化成本。

## 12. 统一指标

每次运行至少记录：

- `load_ms`。
- `plan_build_ms`。
- `h2d_ms`。
- `cpu_scan_ms`。
- `gpu_kernel_ms`。
- `d2h_ms`。
- `overlap_wall_ms`。
- `query_total_ms`。
- `process_elapsed_ms`。
- 输入行数和命中行数。
- 传输字节数。
- CPU peak RSS。
- GPU peak memory。
- 吞吐量。
- 结果 hash 和 oracle 状态。

汇总输出 median、min、max、p95 和标准差。图表不能静默忽略失败行。

代表配置使用 Nsight Systems/Compute，验证：

- H2D 与 CPU scan 是否重叠。
- kernel 是显存、PCIe、原子竞争还是其他瓶颈。
- managed 页面迁移。
- hybrid 的 CPU/GPU 等待关系。

## 13. 实验证据格式

```text
results/experiments/<experiment-id>/
├── manifest.json
├── environment.json
├── commands.txt
├── raw.csv
├── summary.csv
├── correctness.json
├── logs/
├── profile/
└── figures/
```

manifest 记录 Git commit、dirty 状态、数据 checksum、硬件、软件版本、参数和运行时间。

实验失败必须保留 return code、stdout、stderr 和失败状态。OOM、无 GPU 或不支持不能被改写成成功或用缩小数据后的结果替代原配置。

## 14. 双线并行协作

### 14.1 A 线：工程与实验

负责环境、Arrow 数据层、backend、测试、实验、profiling 和证据。

每个完成模块向 B 线输出：实现状态、代码入口、命令、测试、数据、可写结论和限制。

### 14.2 B 线：论文与教学

在工程进行期间同步编写：

- 背景和相关工作。
- 研究问题。
- 系统设计与算法。
- 实验方法。
- 结果表格骨架。
- 学习材料和答辩问题。

未完成实验只允许出现显式内部占位符。最终 PDF 不得包含占位符或未验证结果。

### 14.3 结论账本

`docs/research/CLAIM_LEDGER.md` 使用状态：

- `PLANNED`。
- `IMPLEMENTED`。
- `VERIFIED`。
- `REJECTED`。
- `SUPERSEDED`。

最终论文的结果性陈述必须为 `VERIFIED`，研究假设可以为 `REJECTED`。

## 15. 论文、过程和教学交付

### 15.1 计算机学报论文

使用课程认可的计算机学报 LaTeX 模板，包含：

- 中英文题目、摘要和关键词。
- 作者、学号、单位。
- 引言。
- 背景与相关工作。
- 研究问题与系统设计。
- Arrow CPU-GPU 数据层。
- Q5 查询处理算法。
- CPU、GPU 与混合执行。
- 实验设计。
- 结果与分析。
- 局限性与未来工作。
- 结论和规范参考文献。

摘要和结论在正式实验完成后最终重写。

### 15.2 腾讯过程文档

仓库维护可复制原稿：

```text
docs/process/
├── 01-选题与研究问题.md
├── 02-系统设计方案.md
├── 03-实验设计.md
├── 04-阶段讨论记录.md
├── 05-实验执行日志.md
├── 06-问题与决策记录.md
└── 07-最终完成情况.md
```

用户负责在腾讯文档创建共享空间、授权老师并记录链接。仓库文件负责版本历史。

### 15.3 教学材料

```text
docs/learning/
├── 01-Q5与六表连接.md
├── 02-Apache-Arrow内存布局.md
├── 03-CPU物理计划.md
├── 04-CUDA三种内存模式.md
├── 05-CPU-GPU混合执行.md
├── 06-实验方法与统计.md
└── 07-论文与答辩问答.md
```

每节包含通俗解释、代码入口、图、练习、追问和可复述总结。

最终提供 5 分钟答辩稿、10 分钟答辩稿、一页速查表、架构图、实验图和高风险问题回答。

## 16. GitHub 开源交付

补齐：

- `LICENSE`。
- `CITATION.cff`。
- `environment.yml` 和 GPU environment。
- `Dockerfile`。
- `CMakePresets.json`。
- `CONTRIBUTING.md`。
- `CHANGELOG.md`。

README 提供 CPU-only quickstart、Arrow/CUDA 环境、数据生成、所有 backend、正确性验证、正式实验和报告入口。

TPC-H 官方工具和大规模 `.tbl` 不直接分发。仓库提供官方获取说明、生成/转换脚本、manifest 和 checksum。tiny fixture、synthetic generator、可公开 CSV、图表和报告进入版本控制。

CPU 测试进入普通 CI。GPU 测试由 GPU 服务器运行并保存环境和日志。

## 17. 测试设计

### 17.1 单元测试

- Arrow schema 和类型。
- `.tbl` 严格解析。
- Arrow IPC round-trip。
- 日期范围。
- Decimal/定点数收入。
- dictionary encoding。
- 结果排序和 hash。
- Arrow chunk 边界。
- CPU/GPU 分块。
- hybrid 结果归并。

### 17.2 集成测试

tiny 上运行所有正式 backend，每个结果等于手算 oracle。

SF1 每个 backend 逐国家匹配官方 `q5.out`。

SF10 使用 DuckDB decimal oracle 和跨 backend hash。

### 17.3 GPU 测试

- 无 GPU 明确返回 `SKIPPED_NO_GPU`。
- CUDA 错误使测试/实验失败。
- 使用 compute-sanitizer 检查代表性配置。
- managed、mapped 和 hybrid 覆盖边界规模。
- 任意 hybrid 比例结果必须确定且等于 oracle。

## 18. 错误处理

- Arrow `Status` 和 `Result<T>` 必须传播，不能忽略。
- schema、null、行数或 checksum 不符立即失败。
- CLI 参数错误返回非零和明确提示。
- 异步 GPU 操作结束前保持 buffer ownership。
- OOM 保留原配置失败证据，不静默降级。
- 报告生成只读取可追溯、状态允许的结果。
- 人工排除样本必须记录理由。

## 19. 实施阶段

1. 保护现有成果并建立隔离分支/worktree。
2. 固定 Arrow/CUDA/cuDF 环境。
3. 实现 Arrow 数据层和 IPC。
4. 修复官方 Q5 精度与 oracle。
5. 实现 Arrow Acero 和专用 CPU。
6. 重构三种 GPU backend。
7. 实现 `hybrid-arrow`。
8. 重构 cuDF 和 DuckDB oracle。
9. 完成 benchmark/evidence 框架。
10. 运行正式实验和 profiling。
11. 完成论文、腾讯过程材料和教学。
12. 完成开源包装、最终复核和发布。

双线写作从第 2 阶段开始，不等待第 10 阶段结束。

## 20. 最终验收

### 20.1 技术

- CPU 规范存储是 Apache Arrow。
- Arrow Acero 查询可运行。
- 专用 CPU 直接消费 Arrow buffers。
- copy、managed、mapped 均可运行。
- cuDF 直接消费 Arrow 数据。
- hybrid 有 CPU/GPU 真并发证据。
- SF1 严格匹配官方 Q5。
- 所有 backend 结果一致。

### 20.2 实验

- SF1、SF10 正式矩阵完成。
- 每配置 3 次预热、10 次测量。
- cold-start 和 resident-data 分开。
- 原始数据、环境、命令、失败日志保留。
- 代表配置有 Nsight 证据。
- 论文数字可追溯到 CSV。

若某个非核心 baseline 在 SF10 因真实 OOM 失败，保留并分析失败可视为矩阵已执行；核心 Arrow CPU、专用 CPU、三种手写 CUDA 和至少一个 hybrid 配置必须成功。

### 20.3 课程交付

- GitHub 可公开访问并带 LICENSE。
- 数据生成和环境可复现。
- 腾讯过程文档已分享并记录链接。
- 论文使用课程认可的计算机学报模板。
- 最终 PDF 无占位符和未验证结论。
- 学习和答辩材料完整。
- 用户能够独立解释查询、Arrow、三种 CUDA 模式、hybrid、实验和局限。

## 21. 设计原则

- 证据优先于叙事。
- 统一数据优先于不公平对比。
- 正确性优先于性能。
- 方法章节可并行写，实验数字必须后填。
- 不把 planned 功能写成 implemented。
- 不把 skip、OOM 或失败伪装成通过。
- 不因假设被否定而修改原始数据。

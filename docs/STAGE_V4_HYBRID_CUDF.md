# V4 CPU-GPU 混合执行与 cuDF 对照

## 实现范围

V4 在同一份规范 Arrow IPC 数据上增加两条路径：

1. `hybrid-arrow` 把 `lineitem` 的全局行范围切成互不重叠的 CPU 前缀和 GPU
   后缀，先异步启动 `gpu-copy`，再由当前线程执行 Arrow specialized CPU，最后
   对两个局部结果做 checked int64 归并和统一排序。
2. `baselines/cudf_q5.py` 读取同一份 manifest 和六张 Arrow Table，通过
   `cudf.from_arrow`（或版本兼容的 `DataFrame.from_arrow`）转换后执行 Q5。

混合切分使用 `llround(total_rows * cpu_ratio)`。切分器保留 RecordBatch 下标、
offset 和 length，并验证负长度、总行数溢出和 batch 下标范围。当前执行器利用
Arrow `Table::Slice` 形成等价的两个连续视图，因此满足：

```text
cpu_rows + gpu_rows = input_rows
CPU 和 GPU 行范围不相交
每一行恰好由一个 backend 处理
```

## 运行方法

```bash
./build-arrow-cuda-v3/memq5_arrow_query \
  --dataset /tmp/memq5-sf1-arrow \
  --engine hybrid-arrow --cpu-ratio 0.5 \
  --region ASIA --date 1994-01-01 --threads 8 --format json
```

把 `--cpu-ratio` 改为 `0.25`、`0.50` 或 `0.75` 可以完成三组混合实验。

```bash
conda run -n memq5-cudf python baselines/cudf_q5.py \
  --dataset /tmp/memq5-sf1-arrow \
  --region ASIA --date 1994-01-01 --format rows
```

## 验证记录

- Arrow+CUDA Release CTest 21/21 通过，包括 batch 切分、三种 ratio 的 tiny
  精确结果、三种 CUDA 模式及四条 GPU CLI 路径。
- SF1 的 `0.25`、`0.50` 和 `0.75` 均得到 hash `542abf4003633c7c`，并通过
  官方 Q5 oracle。三组 `cpu_rows/gpu_rows` 分别为
  `1500304/4500911`、`3000608/3000607`、`4500911/1500304`。
- 三组 SF1 都报告了正的 backend-duration overlap 推导值；tiny 输入因任务太短
  报告 0，不把计时噪声当作重叠证据。
- cuDF 26.06.0 的 tiny 测试验证六张表都经过 Arrow 转换；SF1 结果通过同一官方
  oracle。
- `compute-sanitizer` 检查 hybrid tiny 路径时报告 0 个错误。

## 计时口径和限制

`cpu_ms`、`gpu_ms` 是两个 backend 各自的端到端时长，`scan_ms` 是等待两个任务
结束的墙钟时长，`overlap_ms=max(0,cpu_ms+gpu_ms-scan_ms)`。这个值说明两个
backend 任务的持续区间有交集，但不能证明 CPU scan 恰好与 GPU kernel 或 H2D
阶段重叠。当前版本没有 NVTX range 和 Nsight Systems 时间线，所以报告只能声称
“实现了并发调度并观察到 backend duration overlap”，不能声称“已经用硬件轨迹
证明 CPU scan 与 GPU kernel 重叠”。

另外，CPU 和 GPU 目前分别构建自己的 Q5 map，GPU 路径会先把 Arrow 列 staging
为连续 host vector；尚未实现共享 prepared plan、GPU chunk 双缓冲或常驻显存。
这些限制会增加混合路径开销，也是后续优化空间。

# V3 Arrow 输入的 CUDA 三种内存模式

## 实现范围

V3 让 `gpu-copy`、`gpu-managed` 和 `gpu-mapped` 直接接收经过 V2
manifest/schema/checksum 校验的 `ArrowQ5Dataset`。六表过滤和直接索引 map 与
Arrow CPU 共用 `ArrowQ5Plan`；`lineitem` 可以包含多个 RecordBatch，GPU staging
会逐批读取 Int32 和 Decimal128 buffer，并保留 scale-4 收入语义。

三个模式共用一个 CUDA kernel，差别只在数据位置和访问方式：

| 模式 | 输入位置 | GPU 如何读取 | counters 口径 |
| --- | --- | --- | --- |
| `gpu-copy` | `cudaMalloc` 显存 | 显式 H2D 后读显存 | `h2d_bytes` 为输入字节 |
| `gpu-managed` | `cudaMallocManaged` | kernel 前 prefetch 到 GPU | H2D 包含输入和预取的输出缓冲 |
| `gpu-mapped` | mapped pinned host memory | UVA 指针经 PCIe 远程读取 | `h2d_bytes=0`，输入计入 `mapped_remote_read_bytes` |

`gpu-mapped` 的 0 H2D 只表示没有显式输入传输，不表示数据没有移动；GPU 读取
主机页时仍经过 PCIe。三个模式的输出收入、命中行数和溢出标志都会复制或迁移
回 CPU。

## 构建与运行

```bash
cmake -S . -B build-arrow-cuda \
  -DMEMQ5_ENABLE_ARROW=ON \
  -DMEMQ5_ENABLE_CUDA=ON \
  -DMEMQ5_ENABLE_TESTS=ON \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$CONDA_PREFIX" \
  -DCMAKE_CUDA_ARCHITECTURES=89
cmake --build build-arrow-cuda
ctest --test-dir build-arrow-cuda --output-on-failure
```

```bash
./build-arrow-cuda/memq5_arrow_query \
  --dataset tests/fixtures/tpch_q5_tiny_arrow \
  --engine gpu-copy --region ASIA --date 1994-01-01 --format json
```

把 `gpu-copy` 替换为 `gpu-managed` 或 `gpu-mapped` 即可运行另两种模式。

## 2026-07-14 验证记录

- Arrow+CUDA Release 构建通过，CTest 18/18 通过。
- tiny fixture 的三个 GPU 模式与 `cpu-specialized` 精确 hash 一致，覆盖三个
  RecordBatch 和零行输入。
- SF1 三个模式均得到 5 行结果和 hash `542abf4003633c7c`，并通过强化后的
  oracle 与官方 TPC-H V3.0.1 `q5.out` 逐行核对。
- `compute-sanitizer --tool memcheck` 报告 `ERROR SUMMARY: 0 errors`。
- kernel 使用 CAS 累计并检查 int64 聚合溢出，不允许静默回绕。

## 边界

当前 Conda Arrow 23.0.1 不包含可用的 Arrow CUDA 扩展，因此本实现没有声称
使用 `arrow::cuda::CudaBuffer`。规范输入仍是 Apache Arrow Table；进入查询后，
代码按三种实验模式把 Arrow 列 staging 到 CUDA device、managed 或 mapped
buffer。该限制必须在报告中保留。V3 仍是整表 GPU 执行，CPU-GPU 同时分担
`lineitem` 的混合执行属于 V4。
